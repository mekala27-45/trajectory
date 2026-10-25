#!/usr/bin/env python3
"""Hold the mutation figures in docs/verifier-strength.md to the report they came from.

`trajectory tasks mutate` writes a JSON report of every sampled mutant and what the hidden
tests made of it. This renders the measured block of the document from that report and
requires every table row and summary line to appear in the document verbatim, the same
guarantee the published and real model figures have. A figure that drifted from its
report fails here rather than in a reader's hands.

    uv run python scripts/check_mutation_numbers.py           # verify
    uv run python scripts/check_mutation_numbers.py --write   # regenerate the measured block

`--write` replaces only the block between the markers, so the prose around it, which reads
the survivors and is the author's to write, is never touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "fixtures" / "mutation" / "core-12.json"
DOCUMENT = ROOT / "docs" / "verifier-strength.md"

BEGIN = (
    "<!-- measured: rendered from fixtures/mutation/core-12.json "
    "by scripts/check_mutation_numbers.py -->"
)
END = "<!-- end measured -->"

SUMMARY_PREFIXES = (
    "- Backend:",
    "- Harness version:",
    "- Sample:",
    "- Mutants scored:",
    "- Killed:",
    "- Confirmation:",
)


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render(report: dict[str, Any]) -> str:
    """The measured block for a report, ready to embed."""
    tasks: list[dict[str, Any]] = report["tasks"]
    killed = int(report["killed"])
    scored = int(report["scored"])
    by_assertion = int(report["killed_by_assertion"])
    # Reports from before kills were confirmed carry no unstable count and no confirmation.
    unstable = int(report.get("unstable", 0))
    lines = [
        f"- Backend: {report['backend']}",
        f"- Harness version: `{report['harness_version']}`",
        f"- Sample: up to {report['per_task']} mutants per task, fixed per task",
        f"- Mutants scored: {scored} across {sum(1 for task in tasks if task['scored'])} task(s)",
        f"- Killed: {killed} of {scored} ({_percent(report['score'])}), "
        f"{by_assertion} by a failing assertion and {killed - by_assertion} by a crash or a "
        "failed build",
    ]
    if "confirmation" in report:
        lines.append(
            f"- Confirmation: every kill was replayed once more; {unstable} mutant(s) failed "
            "once, passed the second time, and count as not killed"
        )
    lines += [
        "",
        "| task | solution files | sites | scored | killed | by a crash | unstable | survived "
        "| score |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for task in tasks:
        files = ", ".join(f"`{name}`" for name in task["files"]) or "none"
        if not task["scored"]:
            reason = task["control_detail"] or "no mutant could be scored"
            lines.append(
                f"| {task['task_id']} | {files} | {task['sites']} | 0 | | | | "
                f"| n/a: {_cell(reason)} |"
            )
            continue
        crash = int(task["killed"]) - int(task["killed_by_assertion"])
        lines.append(
            f"| {task['task_id']} | {files} | {task['sites']} | {task['scored']} "
            f"| {task['killed']} | {crash} | {task.get('unstable', 0)} | {task['survived']} "
            f"| {_percent(task['score'])} |"
        )

    survivors = [
        (task["task_id"], mutant)
        for task in tasks
        for mutant in task["mutants"]
        if mutant["outcome"] in ("survived", "unstable")
    ]
    lines += ["", "### Survivors", ""]
    if not survivors:
        lines.append("Every scored mutant was killed.")
    else:
        lines += [
            "Each row is a change the hidden tests did not reliably notice. Some are equivalent "
            "to the original and no test could notice them; the rest are gaps.",
            "",
            "| task | mutant | outcome | hidden tests passed |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {task_id} | `{_cell(mutant['label'])}` | {mutant['outcome']} "
            f"| {mutant['tests_passed']} of {mutant['tests_total']} |"
            for task_id, mutant in survivors
        ]
    return "\n".join(lines)


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

    if not REPORT.is_file():
        print("no mutation report is committed; nothing to check")
        return 0
    rendered = render(json.loads(REPORT.read_text(encoding="utf-8")))
    if args.write:
        write_block(DOCUMENT, rendered)
        print(f"wrote the measured block of {DOCUMENT.name} from {REPORT.name}")
        return 0
    if not DOCUMENT.exists():
        print(f"{DOCUMENT.name} is missing. Run with --write to create it.", file=sys.stderr)
        return 1
    body = DOCUMENT.read_text(encoding="utf-8")
    lines = required(rendered)
    missing = [line for line in lines if line not in body]
    if missing:
        print(f"{len(missing)} of {len(lines)} mutation figures disagree:", file=sys.stderr)
        for line in missing:
            print(f"  expected: {line}", file=sys.stderr)
        print("\nRegenerate with: uv run python scripts/check_mutation_numbers.py --write")
        return 1
    print(f"{len(lines)} mutation figures match {REPORT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
