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
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from trajectory_core.aggregate import failure_mode_counts, leaderboard
from trajectory_core.models import Run, RunStatus
from trajectory_core.stats import group_advantages
from trajectory_runner.cli import _markdown_report
from trajectory_runner.providers import ended_by_provider
from trajectory_runner.store import read_runs

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures" / "model-runs"
DOCUMENT = ROOT / "MODEL-RESULTS.md"
# Written by scripts/judge_agreement.py and committed by hand once reviewed.
AGREEMENT = ROOT / "fixtures" / "judge-agreement.json"
MODE_NAMES = {
    "F06": "ignored test output",
    "F07": "long horizon context loss",
    "F10": "environment mismatch",
}

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
    "- Judges compared:",
    "- Drift, between the judges:",
    "- Left out of the comparison:",
)


class StaleAgreementError(ValueError):
    """The judge agreement was measured on runs that are no longer committed."""


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
    lines = ["#" + line if line.startswith(("## ", "### ")) else line for line in lines]
    agreement: list[str] = []
    if AGREEMENT.is_file():
        agreement = ["", *judge_lines(json.loads(AGREEMENT.read_text(encoding="utf-8")), runs)]
    return "\n".join([*lines, "", *ending_lines(runs), "", *signal_lines(runs), *agreement]).strip(
        "\n"
    )


def ending_lines(runs: list[Run]) -> list[str]:
    """How each run ended, per model, with the provider's failures apart from the agent's.

    A run the provider ended is a measurement of the provider, so it gets its own column:
    a solve rate read without it would charge the model for the free tier's bad hour.
    """
    columns = (
        ("agent finished", RunStatus.COMPLETED),
        ("step ceiling", RunStatus.MAX_STEPS),
        ("wall clock ceiling", RunStatus.TIMEOUT),
        ("call ceiling", RunStatus.BUDGET_EXCEEDED),
    )
    lines = [
        "### How each run ended",
        "",
        "| model | runs | solved | "
        + " | ".join(name for name, _ in columns)
        + " | provider ended | other error |",
        "| --- | --- | --- | " + " | ".join("---" for _ in columns) + " | --- | --- |",
    ]
    for model in sorted({run.config.model for run in runs}):
        mine = [run for run in runs if run.config.model == model]
        ended = sum(1 for run in mine if ended_by_provider(run))
        other = sum(1 for run in mine if run.status is RunStatus.ERROR) - ended
        counts = [sum(1 for run in mine if run.status is status) for _, status in columns]
        lines.append(
            f"| `{_short(model)}` | {len(mine)} | {sum(1 for run in mine if run.solved)} | "
            + " | ".join(str(count) for count in counts)
            + f" | {ended} | {other} |"
        )
    return lines


def judge_lines(report: dict[str, Any], runs: list[Run]) -> list[str]:
    """Two judges compared on the same runs, mode by mode.

    Refuses a report measured on runs that are no longer committed, because an agreement
    figure printed beside records it was not measured on would be a claim about nothing.
    """
    stale = sorted(set(report["runs"]) - {run.id for run in runs})
    if stale:
        raise StaleAgreementError(
            f"{AGREEMENT.name} was measured on {len(stale)} run(s) that are no longer "
            "committed. Measure it again or remove it."
        )

    def kappa(value: float | None) -> str:
        return "not computable" if value is None else f"{value:.2f}"

    lines = [
        "### Two judges compared",
        "",
        f"- Judges compared: `{report['first_judge']}` and `{report['second_judge']}`, on "
        f"{report['compared_runs']} of the {report['eligible_runs']} runs the first judged, "
        f"temperature {report['temperature']:g}",
        "",
        "| mode | both present | first only | second only | neither | raw agreement | kappa |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for mode, entry in sorted(report["per_mode"].items()):
        lines.append(
            f"| {mode} {MODE_NAMES.get(mode, '')} | {entry['both_present']} "
            f"| {entry['first_only']} | {entry['second_only']} | {entry['neither_present']} "
            f"| {entry['raw_agreement']:.2f} | {kappa(entry['cohens_kappa'])} |"
        )
    lines += [
        "",
        f"- Drift, between the judges: mean absolute difference "
        f"{report['drift_mean_absolute_difference']:.3f}, largest "
        f"{report['drift_max_absolute_difference']:.3f}",
    ]
    # Older reports carry no reasons, and every run they read was compared.
    left_out: dict[str, str] = report.get("left_out") or {}
    if left_out:
        unreached = sum(1 for reason in left_out.values() if reason.startswith("not reached"))
        failed = len(left_out) - unreached
        parts = [
            f"{failed} run(s) the second judge returned no valid verdict on" if failed else "",
            f"{unreached} run(s) not reached before the workflow's deadline" if unreached else "",
        ]
        lines.append(
            "- Left out of the comparison: " + " and ".join(part for part in parts if part)
        )
    return lines


def _variance(values: list[float]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _short(model: str) -> str:
    return model.rsplit("/", 1)[-1]


def signal_lines(runs: list[Run]) -> list[str]:
    """Per task outcomes across seeds, for every model, read as an RL reward signal.

    A group of samples on one task that all pass, or all fail, has zero reward variance, so
    a group relative method such as GRPO gets no gradient from it. Only a task with mixed
    outcomes teaches anything at a model's level. Under a partial credit reward a task can
    still carry signal when every sample fails, which is the case for partial credit, and
    the case against it is the shortcut a partial reward pays for.
    """
    models = sorted({run.config.model for run in runs})
    tasks = sorted({run.task_id for run in runs})
    groups: dict[tuple[str, str], list[Run]] = defaultdict(list)
    for run in sorted(runs, key=lambda r: (r.config.seed, r.id)):
        groups[(run.config.model, run.task_id)].append(run)

    def partial_of(run: Run) -> float:
        return run.score.partial_credit if run.score else 0.0

    rows: list[str] = []
    for task_id in tasks:
        cells = []
        for model in models:
            group = groups.get((model, task_id), [])
            if not group:
                cells.append("n/a")
                continue
            solved = sum(1 for run in group if run.solved)
            cell = f"{solved}/{len(group)}"
            if len(group) >= 2 and solved not in (0, len(group)):
                cell = f"**{cell}**"
            elif len(group) >= 2 and _variance([partial_of(run) for run in group]) > 0:
                cell = f"*{cell}*"
            cells.append(cell)
        rows.append(f"| {task_id} | " + " | ".join(cells) + " |")

    summary: list[str] = []
    for model in models:
        compared = binary = graded = 0
        for task_id in tasks:
            group = groups.get((model, task_id), [])
            if len(group) < 2:
                continue
            compared += 1
            solved = sum(1 for run in group if run.solved)
            binary += solved not in (0, len(group))
            graded += _variance([partial_of(run) for run in group]) > 0
        summary.append(
            f"- Tasks with a learning signal for `{_short(model)}`: {binary} of {compared} "
            f"under a binary reward, {graded} of {compared} under partial credit"
        )

    return [
        "### Per task, read as an RL reward signal",
        "",
        "Each cell is solved runs over sampled runs. A task whose samples all pass or all fail "
        "gives a group relative method no gradient; only mixed outcomes teach. A bold cell has "
        "a learning signal under a binary reward, and an italic cell only under partial "
        "credit. This describes the reward signal these tasks offer these models. It is not a "
        "training run.",
        "",
        "| task | " + " | ".join(f"`{_short(model)}`" for model in models) + " |",
        "| --- | " + " | ".join("---" for _ in models) + " |",
        *rows,
        "",
        *summary,
        "",
        *grpo_lines(groups),
    ]


def grpo_lines(groups: dict[tuple[str, str], list[Run]]) -> list[str]:
    """One group worked through: the advantage GRPO would give each sample."""
    chosen = next(
        (
            key
            for key in sorted(groups)
            if len(groups[key]) >= 2
            and sum(1 for run in groups[key] if run.solved) not in (0, len(groups[key]))
        ),
        None,
    )
    if chosen is None:
        return []
    group = groups[chosen]
    binary = [1.0 if run.solved else 0.0 for run in group]
    partial = [run.score.partial_credit if run.score else 0.0 for run in group]
    lines = [
        "### One group, worked",
        "",
        "GRPO samples a group of answers to one prompt and moves each toward or away from the "
        "group's own average: its advantage is its reward less the group mean, over the "
        "group's spread. Here the prompt is a task and the group is a model's seeds on it, "
        f"`{_short(chosen[0])}` on {chosen[1]}, the first such group with mixed outcomes.",
        "",
        "| seed | solved | partial credit | advantage, binary reward | advantage, partial credit |",
        "| --- | --- | --- | --- | --- |",
    ]
    for run, a_binary, a_partial, score in zip(
        group, group_advantages(binary), group_advantages(partial), partial, strict=True
    ):
        lines.append(
            f"| {run.config.seed} | {'yes' if run.solved else 'no'} | {score:.3f} "
            f"| {a_binary:+.2f} | {a_partial:+.2f} |"
        )
    lines += [
        "",
        "A group whose samples all pass, or all fail, gets an advantage of zero for every "
        "sample under a binary reward, which is why the bold cells above are the only ones a "
        "binary reward can learn from.",
    ]
    return lines


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

    try:
        rendered = render(FIXTURES)
    except StaleAgreementError as exc:
        print(str(exc), file=sys.stderr)
        return 1
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
