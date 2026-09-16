#!/usr/bin/env python
"""Promote a fresh matrix run to the published fixtures, and update the figures with it.

`scripts/check_published_numbers.py` recomputes 64 published figures from the committed run
records and fails the build when a document disagrees. That gate is why two wrong numbers
cannot ship again. It also means re-recording the matrix, which is the one thing that
removes the repository's largest caveat, invalidates 64 figures at once and leaves you to
find and retype every one of them by hand across two documents. Nobody does that correctly,
so in practice the gate discourages the exact improvement it should enable.

This inverts it. Every claim carries the rendered string the document must contain, so the
same code that detects a mismatch can repair it: compute the claims from the old fixtures,
swap in the new runs, compute them again, and for each figure that moved, replace the old
rendered string with the new one. Only strings the gate had already verified present are
touched, and anything that cannot be replaced mechanically is reported rather than guessed
at.

    uv run python scripts/run_matrix.py --parallel 2
    uv run python scripts/promote_matrix.py runs/<the directory it printed>
    git diff

What it does not do, deliberately: rewrite a sentence. If a finding changes in kind rather
than in value, for example a failure mode that stops firing on solved runs altogether, the
numbers are updated and the surrounding prose is left alone, because a claim about what the
data means is the author's to make. Those are listed at the end as prose to review.

Refuses to run when the files it edits already have uncommitted changes, so `git diff`
afterwards shows exactly what it did and nothing else.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import check_published_numbers as gate

from trajectory_core.models import Run

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "recorded-runs"
TOUCHED = ("README.md", "RESULTS.md", "fixtures/recorded-runs", "web/public/data")

REPLACED = "replaced"
REFLOWED = "replaced, line may need rewrapping"
NOT_FOUND = "not found in the document"
AMBIGUOUS = "appears more than once, left alone"


@dataclass
class Edit:
    """One published figure that moved, and what became of it."""

    document: Path
    label: str
    before: str
    after: str
    outcome: str


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir",
        type=Path,
        help="The directory run_matrix.py wrote, containing runs/*.json.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing anything.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Proceed even when the files this edits have uncommitted changes.",
    )
    return parser.parse_args(argv)


def dirty_paths() -> list[str]:
    """The paths this script edits that already have uncommitted changes."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *TOUCHED],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line[3:].strip() for line in result.stdout.splitlines() if line.strip()]


def load_new_runs(run_dir: Path) -> list[Run]:
    """Read the run records from a matrix directory, with the usual mistakes named."""
    candidates = run_dir / "runs" if (run_dir / "runs").is_dir() else run_dir
    paths = sorted(candidates.glob("*.json"))
    if not paths:
        raise SystemExit(
            f"no run records under {candidates}. Point this at the directory that "
            "run_matrix.py printed, the one containing a runs/ subdirectory."
        )
    runs = [Run.model_validate_json(path.read_text(encoding="utf-8")) for path in paths]
    backends = sorted({run.config.sandbox_backend.value for run in runs})
    suites = sorted({run.suite for run in runs})
    print(f"{len(runs)} runs, suite(s) {', '.join(suites)}, backend(s) {', '.join(backends)}")
    errored = sum(1 for run in runs if run.status.value == "error")
    if errored:
        print(
            f"warning: {errored} of {len(runs)} runs errored. Promoting them publishes the "
            "errors as measured results, which is honest but is probably not intended.",
            file=sys.stderr,
        )
    return runs


def write_fixtures(runs: list[Run]) -> None:
    """Swap the committed fixtures for a new set, written the way the store writes them."""
    for stale in FIXTURES.glob("*.json"):
        stale.unlink()
    for run in runs:
        (FIXTURES / f"{run.id}.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")


def flexible_pattern(text: str) -> re.Pattern[str]:
    """A pattern matching `text` with any run of whitespace between its words.

    The gate collapses whitespace on both sides before comparing, so a claim is satisfied
    even when markdown has wrapped it across a line break. A literal replace would miss
    exactly those, which are the prose claims rather than the table cells.
    """
    return re.compile(r"\s+".join(re.escape(word) for word in text.split()))


def apply_edits(claims_before: list[gate.Claim], claims_after: list[gate.Claim]) -> list[Edit]:
    """Replace each moved figure in its document, reporting anything not mechanical."""
    after_by_key = {(c.document, c.label): c for c in claims_after}
    documents: dict[Path, str] = {}
    edits: list[Edit] = []

    for old in claims_before:
        new = after_by_key.get((old.document, old.label))
        if new is None or not old.present or not new.present or old.text == new.text:
            continue
        if old.document not in documents:
            documents[old.document] = old.document.read_text(encoding="utf-8")
        body = documents[old.document]

        count = body.count(old.text)
        if count == 1:
            documents[old.document] = body.replace(old.text, new.text, 1)
            edits.append(Edit(old.document, old.label, old.text, new.text, REPLACED))
            continue
        if count > 1:
            edits.append(Edit(old.document, old.label, old.text, new.text, AMBIGUOUS))
            continue

        matches = list(flexible_pattern(old.text).finditer(body))
        if len(matches) == 1:
            start, end = matches[0].span()
            documents[old.document] = body[:start] + new.text + body[end:]
            edits.append(Edit(old.document, old.label, old.text, new.text, REFLOWED))
        elif len(matches) > 1:
            edits.append(Edit(old.document, old.label, old.text, new.text, AMBIGUOUS))
        else:
            edits.append(Edit(old.document, old.label, old.text, new.text, NOT_FOUND))

    for path, body in documents.items():
        path.write_text(body, encoding="utf-8")
    return edits


def backend_prose_to_review(before: list[Run], after: list[Run]) -> list[str]:
    """Lines whose prose describes the backend the matrix no longer used.

    The promotion updates figures, not meaning. Re-recording on Docker is the change that
    removes this repository's largest caveat, and every sentence explaining that caveat
    becomes wrong at the same moment: nine of them, spread over two documents, none of
    which any number-based check can see. Listing them by line is the difference between
    a tool that finishes the job and one that finishes the arithmetic.
    """
    old = {run.config.sandbox_backend.value for run in before}
    new = {run.config.sandbox_backend.value for run in after}
    if old == new:
        return []

    gone = old - new
    terms = sorted({*gone, "unisolated"})
    found: list[str] = []
    for document in (gate.README, gate.RESULTS):
        for number, line in enumerate(document.read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            if any(term in lowered for term in terms):
                found.append(f"  {gate._display(document)}:{number}: {line.strip()[:110]}")
    return found


def rebuild_web_bundle() -> int:
    """Regenerate web/public/data from the new fixtures, so the demo matches."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_web_bundle.py"), "--from-fixtures"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout.strip() or result.stderr.strip())
    return result.returncode


def report(edits: list[Edit]) -> int:
    """Print what happened, and return a process exit status."""
    done = [e for e in edits if e.outcome in (REPLACED, REFLOWED)]
    stuck = [e for e in edits if e.outcome not in (REPLACED, REFLOWED)]
    reflowed = [e for e in edits if e.outcome == REFLOWED]

    for edit in done:
        print(f"  {gate._display(edit.document)}: {edit.label}")
        print(f"      {edit.before!r}\n   -> {edit.after!r}")

    if reflowed:
        print(
            f"\n{len(reflowed)} of those were wrapped across lines in the source and have "
            "been written on one line. Rewrap them if you care about the margin."
        )

    if stuck:
        print("\nNot updated, and each needs a look:", file=sys.stderr)
        for edit in stuck:
            print(
                f"  {gate._display(edit.document)}: {edit.label}\n"
                f"      {edit.outcome}\n"
                f"      was:    {edit.before!r}\n"
                f"      now is: {edit.after!r}",
                file=sys.stderr,
            )

    print(f"\n{len(done)} figure(s) updated, {len(stuck)} left for you.")
    return 1 if stuck else 0


def main(argv: list[str]) -> int:
    """Promote a matrix run and update every figure derived from it."""
    args = parse_args(argv)

    dirty = dirty_paths()
    if dirty and not args.force and not args.dry_run:
        print(
            "These already have uncommitted changes, and this script edits them:\n  "
            + "\n  ".join(dirty)
            + "\n\nCommit or stash them first, so the diff afterwards shows only what this\n"
            "did. Pass --force to proceed anyway.",
            file=sys.stderr,
        )
        return 1

    new_runs = load_new_runs(args.run_dir)
    claims_before = gate.build_claims(gate.load_runs())

    if args.dry_run:
        # Compute the new claims without touching the fixtures on disk.
        claims_after = gate.build_claims(new_runs)
        moved = [
            (a, b)
            for a in claims_before
            for b in claims_after
            if (a.document, a.label) == (b.document, b.label) and a.text != b.text
        ]
        print(f"\n{len(moved)} of {len(claims_before)} published figures would change:")
        for old, new in moved:
            print(f"  {gate._display(old.document)}: {old.label}")
            print(f"      {old.text!r}\n   -> {new.text!r}")
        print("\nNothing was written. Drop --dry-run to apply.")
        return 0

    old_runs = gate.load_runs()
    write_fixtures(new_runs)
    claims_after = gate.build_claims(gate.load_runs())

    print(f"\nfixtures replaced with {len(new_runs)} runs, updating the documents:")
    status = report(apply_edits(claims_before, claims_after))

    prose = backend_prose_to_review(old_runs, new_runs)
    if prose:
        was = sorted({r.config.sandbox_backend.value for r in old_runs})
        now = sorted({r.config.sandbox_backend.value for r in new_runs})
        print(
            f"\nThe backend changed from {', '.join(was)} to {', '.join(now)}. The figures "
            f"are correct now, but the prose around them\nis not: these {len(prose)} lines "
            "mention the old backend and are candidates to revise. The match is\nliteral, so "
            "one or two may be incidental, which is the right way round for a list you read:",
        )
        print("\n".join(prose))
        status = 1

    if rebuild_web_bundle() != 0:
        print("the web bundle could not be rebuilt", file=sys.stderr)
        return 1

    print("\nnow run: uv run python scripts/check_published_numbers.py")
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
