"""The mutation figures gate: render, write, verify, and catch drift."""

from __future__ import annotations

import json
from pathlib import Path

import check_mutation_numbers as gate
import pytest

REPORT = {
    "suite": "core-12",
    "backend": "docker",
    "per_task": 12,
    "harness_version": "0.3.0",
    "generated_at": "2026-10-25T00:00:00+00:00",
    "scored": 3,
    "killed": 2,
    "killed_by_assertion": 1,
    "survived": 1,
    "score": 2 / 3,
    "tasks": [
        {
            "task_id": "py-perf-01",
            "files": ["src/rollup/core.py"],
            "sites": 9,
            "control_passed": True,
            "control_detail": "9/9",
            "scored": 3,
            "killed": 2,
            "killed_by_assertion": 1,
            "survived": 1,
            "score": 2 / 3,
            "mutants": [
                {
                    "label": "src/rollup/core.py:123 > -> >=",
                    "outcome": "survived",
                    "tests_passed": 9,
                    "tests_total": 9,
                },
                {
                    "label": "src/rollup/core.py:121 + -> -",
                    "outcome": "killed",
                    "tests_passed": 4,
                    "tests_total": 9,
                },
                {
                    "label": "src/rollup/core.py:117 is -> is not",
                    "outcome": "killed by a crash",
                    "tests_passed": 0,
                    "tests_total": 9,
                },
            ],
        },
        {
            "task_id": "git-surgery-01",
            "files": [],
            "sites": 0,
            "control_passed": False,
            "control_detail": "the reference writes no source file to mutate",
            "scored": 0,
            "killed": 0,
            "killed_by_assertion": 0,
            "survived": 0,
            "score": None,
            "mutants": [],
        },
    ],
}


@pytest.fixture
def paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    report = tmp_path / "core-12.json"
    report.write_text(json.dumps(REPORT), encoding="utf-8")
    document = tmp_path / "verifier-strength.md"
    document.write_text(f"# Title\n\nprose\n\n{gate.BEGIN}\n\n{gate.END}\n\nmore prose\n")
    monkeypatch.setattr(gate, "REPORT", report)
    monkeypatch.setattr(gate, "DOCUMENT", document)
    return report, document


def test_the_render_names_every_survivor_and_every_unscored_task():
    rendered = gate.render(REPORT)
    assert "- Killed: 2 of 3 (66.7%), 1 by a failing assertion and 1 by a crash" in rendered
    assert "| py-perf-01 | `src/rollup/core.py` | 9 | 3 | 2 | 1 | 0 | 1 | 66.7% |" in rendered
    assert "| py-perf-01 | `src/rollup/core.py:123 > -> >=` | survived | 9 of 9 |" in rendered
    assert "n/a: the reference writes no source file to mutate" in rendered
    # A report from before kills were confirmed claims no confirmation.
    assert "- Confirmation:" not in rendered


def test_a_confirmed_report_lists_its_unstable_mutants():
    report = json.loads(json.dumps(REPORT))
    report["confirmation"] = "every kill is replayed once more"
    report["unstable"] = 1
    task = report["tasks"][0]
    task["unstable"] = 1
    task["mutants"].append(
        {
            "label": "src/rollup/core.py:30 < -> <=",
            "outcome": "unstable",
            "tests_passed": 8,
            "tests_total": 9,
        }
    )
    rendered = gate.render(report)
    assert "- Confirmation: every kill was replayed once more; 1 mutant(s) failed once" in rendered
    assert "| py-perf-01 | `src/rollup/core.py:30 < -> <=` | unstable | 8 of 9 |" in rendered
    assert "- Confirmation:" in "\n".join(gate.required(rendered))


def test_a_missing_report_has_nothing_to_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gate, "REPORT", tmp_path / "absent.json")
    assert gate.main([]) == 0


def test_write_then_verify_and_leave_the_prose_alone(paths: tuple[Path, Path]):
    _, document = paths
    assert gate.main([]) == 1
    assert gate.main(["--write"]) == 0
    assert gate.main([]) == 0
    body = document.read_text(encoding="utf-8")
    assert body.startswith("# Title\n\nprose\n")
    assert body.rstrip().endswith("more prose")


def test_a_figure_that_drifts_fails(paths: tuple[Path, Path]):
    report, _ = paths
    assert gate.main(["--write"]) == 0
    changed = json.loads(report.read_text(encoding="utf-8"))
    changed["killed"] = 3
    report.write_text(json.dumps(changed), encoding="utf-8")
    assert gate.main([]) == 1
