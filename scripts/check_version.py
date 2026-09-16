#!/usr/bin/env python
"""Fail if the version is not the same number in every place that states it.

A project states its version in more places than anyone remembers. Here that is four
`pyproject.toml` files, the `HARNESS_VERSION` constant stamped into every run record, the
resolved versions inside `uv.lock`, and the heading of the release notes that become the
body of the GitHub release. On a tagged build the git tag is a seventh.

All seven were consistent at `v0.1.0` by construction, because the repository was written
in one day and nothing had moved yet. Then `v0.1.1` became the next tag and none of the
other six knew: the packages still said `0.1.0`, so a wheel built from that tag would have
carried the wrong version; `HARNESS_VERSION` still said `0.1.0`, so every run recorded
after the release would have misreported the harness that produced it, in a project whose
entire argument is that the run record tells you what produced a number; and the release
notes still opened with `## trajectory v0.1.0`, so the release page would have announced
the previous version under the new tag.

None of that breaks a build. That is exactly why it needs a check rather than attention.

    uv run python scripts/check_version.py                 # internal consistency
    uv run python scripts/check_version.py --tag v0.1.1    # and that a tag agrees

`--tag` also reads `GITHUB_REF_NAME` when the flag is absent and the ref is a tag, so the
release workflow needs no argument. A leading `v` is optional and stripped.

Exits 0 when every source agrees, 1 otherwise, naming each disagreement.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYPROJECTS = (
    Path("pyproject.toml"),
    Path("packages/core/pyproject.toml"),
    Path("packages/runner/pyproject.toml"),
    Path("packages/api/pyproject.toml"),
)
MODELS = Path("packages/core/src/trajectory_core/models.py")
LOCK = Path("uv.lock")
RELEASE_NOTES = Path(".github/release-notes.md")

HARNESS_RE = re.compile(r'^HARNESS_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)
NOTES_RE = re.compile(r"^#+\s*trajectory\s+v([0-9][^\s]*)", re.MULTILINE | re.IGNORECASE)
WORKSPACE_PACKAGES = ("trajectory-api", "trajectory-core", "trajectory-eval")


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tag",
        default=None,
        help="A git tag that must match, with or without the leading v. "
        "Defaults to GITHUB_REF_NAME when the workflow ref is a tag.",
    )
    return parser.parse_args(argv)


def tag_from_environment() -> str | None:
    """The tag this workflow was triggered by, if it was triggered by one."""
    if os.environ.get("GITHUB_REF_TYPE") != "tag":
        return None
    return os.environ.get("GITHUB_REF_NAME") or None


def declared_versions() -> dict[str, str]:
    """Every version this repository states, keyed by where it says it."""
    found: dict[str, str] = {}

    for relative in PYPROJECTS:
        data = tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))
        project = data.get("project", {})
        if "version" in project:
            found[str(relative)] = str(project["version"])

    models = (ROOT / MODELS).read_text(encoding="utf-8")
    if match := HARNESS_RE.search(models):
        found[f"{MODELS} (HARNESS_VERSION)"] = match.group(1)

    lock = (ROOT / LOCK).read_text(encoding="utf-8")
    for name in WORKSPACE_PACKAGES:
        pattern = re.compile(rf'name = "{re.escape(name)}"\nversion = "([^"]+)"')
        if match := pattern.search(lock):
            found[f"{LOCK} ({name})"] = match.group(1)

    notes = (ROOT / RELEASE_NOTES).read_text(encoding="utf-8")
    if match := NOTES_RE.search(notes):
        found[f"{RELEASE_NOTES} (heading)"] = match.group(1)

    return found


def main(argv: list[str]) -> int:
    """Check every declared version against the others, and against a tag if given."""
    args = parse_args(argv)
    found = declared_versions()

    expected = ("pyproject.toml", "packages/core/pyproject.toml", str(MODELS))
    missing = [name for name in expected if not any(name in key for key in found)]
    if missing:
        print(
            "could not read a version from: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    tag = args.tag if args.tag is not None else tag_from_environment()
    if tag:
        found[f"git tag {tag}"] = tag.lstrip("vV")

    distinct = sorted(set(found.values()))
    if len(distinct) == 1:
        sources = len(found)
        suffix = f", matching the tag {tag}" if tag else ""
        print(f"version {distinct[0]} agrees across {sources} source(s){suffix}")
        return 0

    print(
        f"{len(distinct)} different versions are declared: {', '.join(distinct)}\n", file=sys.stderr
    )
    for source, version in sorted(found.items(), key=lambda item: (item[1], item[0])):
        print(f"  {version:10s} {source}", file=sys.stderr)
    print(
        "\nBump them together. `uv lock` refreshes the lock after the pyproject files,\n"
        "and the release notes heading is the one everybody forgets, because nothing\n"
        "breaks when it is wrong: the release page just announces the previous version.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
