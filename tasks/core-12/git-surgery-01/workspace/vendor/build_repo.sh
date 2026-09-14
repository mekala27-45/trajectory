#!/bin/sh
# Builds the repository this task starts from.
#
# Run once by setup_cmd, which removes this directory afterwards: the starting state has
# to be identical on every run, and it has to be a real object database rather than a
# committed one, and an agent that could read this script would be solving a different
# problem.
#
# Every identity and every date is fixed, so the object names are the same on every run.
set -eu

REPO=${1:-repo}
rm -rf "$REPO"
mkdir -p "$REPO"
cd "$REPO"

git init -q -b main .
git config user.name "Sam Okoro"
git config user.email "sam@example.com"
git config commit.gpgsign false
git config gc.auto 0
git config advice.detachedHead false

commit() {
    # commit <author name> <author email> <iso date> <subject>
    GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_AUTHOR_DATE="$3" \
    GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" GIT_COMMITTER_DATE="$3" \
        git commit -q -m "$4"
}

# ---------------------------------------------------------------- main, three commits

mkdir -p src tests

cat >README.md <<'EOF'
# feedloader

Pulls the vendor product feed and turns it into rows the catalogue service can read.
EOF

cat >src/fetcher.py <<'EOF'
"""Fetch the vendor feed."""

from __future__ import annotations

import urllib.request


def fetch(url: str, timeout: float = 10.0) -> bytes:
    """Return the body at a URL."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return response.read()
EOF

git add .
commit "Dana Whitfield" "dana@example.com" "2026-01-06T10:02:11+00:00" "Add the feed fetcher"

cat >src/csvio.py <<'EOF'
"""Read the vendor feed into rows."""

from __future__ import annotations

import csv
import io


def read_rows(body: bytes) -> list[dict[str, str]]:
    """Parse a feed body into a list of rows."""
    text = body.decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))
EOF

git add .
commit "Dana Whitfield" "dana@example.com" "2026-01-08T11:31:40+00:00" "Add the CSV reader"

git branch feature

# The third commit on main lands after feature was cut, which is why feature needs a
# rebase in the first place.
cat >src/csvio.py <<'EOF'
"""Read the vendor feed into rows."""

from __future__ import annotations

import csv
import io

RECORD_SEPARATOR = "\r\n"


def read_rows(body: bytes) -> list[dict[str, str]]:
    """Parse a feed body into a list of rows.

    The vendor writes CRLF and will not be talked out of it, so the separator is pinned
    rather than sniffed.
    """
    text = body.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text, newline=RECORD_SEPARATOR))
    return list(reader)
EOF

git add .
commit "Sam Okoro" "sam@example.com" "2026-02-13T16:45:02+00:00" "Pin the feed record separator to CRLF"

# ------------------------------------------------------------- feature, three commits

git checkout -q feature

cat >src/retry.py <<'EOF'
"""Retry the fetch step."""

from __future__ import annotations

import time
from collections.abc import Callable

MAX_ATTEMPTS = 4


def with_retry(call: Callable[[], bytes]) -> bytes:
    """Call something up to MAX_ATTEMPTS times, pausing a second between attempts."""
    last: Exception | None = None
    for _ in range(MAX_ATTEMPTS):
        try:
            return call()
        except OSError as exc:
            last = exc
            time.sleep(1.0)
    raise RuntimeError("the feed did not answer") from last
EOF

git add .
commit "Priya Raman" "priya@example.com" "2026-02-10T08:55:13+00:00" \
    "Add a retry wrapper around the fetch step"

# The commit the rebase dropped.
cat >src/retry.py <<'EOF'
"""Retry the fetch step."""

from __future__ import annotations

import time
from collections.abc import Callable

MAX_ATTEMPTS = 4
BASE_DELAY = 0.5
MAX_DELAY = 30.0


def backoff_seconds(attempt: int, retry_after: float | None = None) -> float:
    """Return how long to wait before the next attempt.

    The vendor answers 429 with a Retry-After header whenever the nightly job overlaps
    ours, and it means it: going again before then earns another 429. Honour the header
    when it is there and fall back to doubling, capped, when it is not.
    """
    if retry_after is not None:
        return min(max(retry_after, 0.0), MAX_DELAY)
    return min(BASE_DELAY * (2**attempt), MAX_DELAY)


def with_retry(call: Callable[[], bytes]) -> bytes:
    """Call something up to MAX_ATTEMPTS times, backing off between attempts."""
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return call()
        except OSError as exc:
            last = exc
            time.sleep(backoff_seconds(attempt, getattr(exc, "retry_after", None)))
    raise RuntimeError("the feed did not answer") from last
EOF

cat >tests/test_backoff.py <<'EOF'
"""The backoff schedule, which is the part the vendor cares about."""

from __future__ import annotations

from src.retry import MAX_DELAY, backoff_seconds


def test_doubles_without_a_header() -> None:
    assert [backoff_seconds(n) for n in range(4)] == [0.5, 1.0, 2.0, 4.0]


def test_honours_retry_after() -> None:
    assert backoff_seconds(0, retry_after=12.0) == 12.0


def test_caps_a_silly_header() -> None:
    assert backoff_seconds(0, retry_after=4000.0) == MAX_DELAY


def test_never_waits_a_negative_time() -> None:
    assert backoff_seconds(2, retry_after=-1.0) == 0.0
EOF

git add .
commit "Priya Raman" "priya@example.com" "2026-02-11T09:14:22+00:00" \
    "Back off when the vendor answers 429"

cat >src/fetcher.py <<'EOF'
"""Fetch the vendor feed."""

from __future__ import annotations

import urllib.request

from src.retry import with_retry


def fetch(url: str, timeout: float = 10.0) -> bytes:
    """Return the body at a URL, retrying a feed that is not answering."""

    def once() -> bytes:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()

    return with_retry(once)
EOF

git add .
commit "Priya Raman" "priya@example.com" "2026-02-12T14:03:47+00:00" \
    "Put the fetch path behind the retry wrapper"

# ------------------------------------------------------------------- the bad rebase

# An interactive rebase with the second line deleted out of the todo list, which is the
# way this happens to people. The committer is whoever ran it.
GIT_COMMITTER_NAME="Sam Okoro" \
GIT_COMMITTER_EMAIL="sam@example.com" \
GIT_COMMITTER_DATE="2026-02-14T09:20:00+00:00" \
GIT_SEQUENCE_EDITOR="sed -i 2d" \
GIT_EDITOR=true \
    git rebase -i main >/dev/null 2>&1

# Then a tidy up that took the reflog entry for the old tip with it.
git reflog expire --expire-unreachable=now --all
