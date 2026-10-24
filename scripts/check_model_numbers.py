#!/usr/bin/env python3
"""Hold the real model figures in MODEL-RESULTS.md to the run records they came from.

The published matrix already has this gate: `check_published_numbers.py` recomputes every
figure in README and RESULTS from the committed records and fails the build when a
document disagrees. Real model results deserve the same guarantee, and are more likely to
need it, because they are re-recorded whenever a model or a seed count changes.

So this renders the report from `fixtures/model-runs` with the same code `trajectory report`
uses, and requires every table row and every summary line it produces to appear verbatim in
MODEL-RESULTS.md. A figure that drifted from its records, or a document that was not
regenerated after a re-record, fails here rather than in a reader's hands.

    uv run python scripts/check_model_numbers.py           # verify
    uv run python scripts/check_model_numbers.py --write   # regenerate the measured block

`--write` replaces only the block between the two markers in MODEL-RESULTS.md, so the prose
around it, which is the author's to write, is never touched.

With no committed model records there is nothing to hold, and the check passes saying so.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from trajectory_core.aggregate import failure_mode_counts, leaderboard
from trajectory_core.models import Run
from trajectory_runner.cli import _markdown_report
from trajectory_runner.store import read_runs

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "model-runs"
DOCUMENT = ROOT / "MODEL-RESULTS.md"

BEGIN = "<!-- measured: rendered from fixtures/model-runs by scripts/check_model_numbers.py -->"
END = "<!-- end measured -->"

SUMMARY_PREFIXES = (
    "- Tasks with a learning signal",
    "- Harness version:",
    "- Runs:",
    "- Models:",
    "- Tasks:",
    "- Seeds:",
    "- Backends:",
    "- Total provider spend:",
    "- Total wall clock:",
)


def render(fixtures: Path) -> str:
    """The report for the committed records, without its title, ready to embed."""
    runs = read_runs(fixtures)
    text = _markdown_report(
        Path("model"),
        runs,
        leaderboard(runs),
        failure_mode_counts(runs),
        failure_mode_counts(runs, among="solved"),
    )
    lines = text.splitlines()
    # Drop the report's own H1 and demote its sections, so it nests under the document's.
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    lines = ["#" + line if line.startswith("## ") else line for line in lines]
    return "\n".join([*lines, "", *signal_lines(runs)]).strip("\n")


def _variance(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def signal_lines(runs: list[Run]) -> list[str]:
    """Per task outcomes across seeds, read as a reinforcement learning reward signal.

    A group of samples on one task that all pass, or all fail, has zero reward variance, so
    a group relative method such as GRPO gets no gradient from it. Only a task with mixed
    outcomes teaches anything at this model's level. Under a partial credit reward a task
    can still carry signal when every sample fails, which is the case for partial credit,
    and the case against it is the shortcut a partial reward pays for.
    """
    by_task: dict[str, list[Run]] = defaultdict(list)
    for run in runs:
        by_task[run.task_id].append(run)

    rows: list[str] = []
    binary_signal = partial_signal = 0
    for task_id in sorted(by_task):
        group = by_task[task_id]
        solved = sum(1 for run in group if run.solved)
        partial = [run.score.partial_credit if run.score else 0.0 for run in group]
        binary = solved not in (0, len(group))
        graded = len(group) > 1 and _variance(partial) > 0
        binary_signal += binary
        partial_signal += graded
        rows.append(
            f"| {task_id} | {len(group)} | {solved} | {sum(partial) / len(partial):.3f} "
            f"| {'yes' if binary else 'no'} | {'yes' if graded else 'no'} |"
        )

    seeds = sorted({run.config.seed for run in runs})
    return [
        "### Per task, read as an RL reward signal",
        "",
        f"Each task was sampled {len(seeds)} time(s). A task whose samples all pass or all "
        "fail gives a group relative method no gradient; only mixed outcomes teach. This "
        "describes the reward signal these tasks offer this model. It is not a training run.",
        "",
        "| task | samples | solved | mean partial credit "
        "| signal, binary reward | signal, partial credit |",
        "| --- | --- | --- | --- | --- | --- |",
        *rows,
        "",
        f"- Tasks with a learning signal under a binary reward: {binary_signal} of {len(by_task)}",
        f"- Tasks with a learning signal under partial credit: {partial_signal} of {len(by_task)}",
    ]


def required(rendered: str) -> list[str]:
    """Every line a reader would quote: the table rows and the summary bullets."""
    return [
        line
        for line in rendered.splitlines()
        if line.startswith("| ") or line.startswith(SUMMARY_PREFIXES)
    ]


def write_block(document: Path, rendered: str) -> None:
    """Replace the measured block, or append one if the document has none yet."""
    block = f"{BEGIN}\n\n{rendered}\n\n{END}"
    body = document.read_text(encoding="utf-8") if document.exists() else ""
    if BEGIN in body and END in body:
        head, rest = body.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        body = head + block + tail
    else:
        body = (body.rstrip("\n") + "\n\n" if body else "") + block + "\n"
    document.write_text(body, encoding="utf-8")


def main(argv: list[str]) -> int:
    """Verify, or with --write regenerate, the measured block."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Regenerate the measured block.")
    args = parser.parse_args(argv)

    if not FIXTURES.is_dir() or not any(FIXTURES.glob("*.json")):
        print("no model run records are committed under fixtures/model-runs; nothing to check")
        return 0

    rendered = render(FIXTURES)
    if args.write:
        write_block(DOCUMENT, rendered)
        print(f"wrote the measured block of {DOCUMENT.name} from {FIXTURES.name}")
        return 0

    if not DOCUMENT.exists():
        print(
            f"{DOCUMENT.name} is missing but model records are committed. "
            "Run with --write to create its measured block.",
            file=sys.stderr,
        )
        return 1

    body = DOCUMENT.read_text(encoding="utf-8")
    lines = required(rendered)
    missing = [line for line in lines if line not in body]
    if missing:
        print(
            f"{len(missing)} of {len(lines)} model figures disagree with the records:",
            file=sys.stderr,
        )
        for line in missing:
            print(f"  expected: {line}", file=sys.stderr)
        print(
            "\nRegenerate with: uv run python scripts/check_model_numbers.py --write",
            file=sys.stderr,
        )
        return 1

    count = len(read_runs(FIXTURES))
    print(f"{len(lines)} model figures match the {count} committed model run records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
