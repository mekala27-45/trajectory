"""The model figures gate: regenerate, verify, catch drift, and leave the prose alone."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import check_model_numbers as gate
import pytest

ROOT = Path(__file__).resolve().parent.parent
RECORDED = ROOT / "fixtures" / "recorded-runs"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A fixtures directory holding a few real run records, and an empty document path."""
    fixtures = tmp_path / "model-runs"
    fixtures.mkdir()
    for path in sorted(RECORDED.glob("*.json"))[:6]:
        shutil.copy2(path, fixtures / path.name)
    document = tmp_path / "MODEL-RESULTS.md"
    monkeypatch.setattr(gate, "FIXTURES", fixtures)
    monkeypatch.setattr(gate, "DOCUMENT", document)
    monkeypatch.setattr(gate, "AGREEMENT", tmp_path / "judge-agreement.json")
    return fixtures, document


def _agreement(run_ids: list[str]) -> dict[str, object]:
    def mode(both: int, first: int, second: int, neither: int, kappa: float | None):
        total = both + first + second + neither
        return {
            "both_present": both,
            "first_only": first,
            "second_only": second,
            "neither_present": neither,
            "raw_agreement": (both + neither) / total,
            "cohens_kappa": kappa,
        }

    return {
        "first_judge": "nvidia_nim/stepfun-ai/step-5-preview",
        "second_judge": "nvidia_nim/deepseek-ai/deepseek-v4.1-flash",
        "temperature": 0.0,
        "eligible_runs": len(run_ids),
        "sampled_runs": len(run_ids),
        "compared_runs": len(run_ids),
        "per_mode": {
            "F06": mode(1, 1, 0, 2, 0.5),
            "F07": mode(0, 0, 1, 3, 0.0),
            "F10": mode(0, 0, 0, 4, None),
        },
        "drift_mean_absolute_difference": 0.125,
        "drift_max_absolute_difference": 0.4,
        "runs": run_ids,
    }


def test_no_records_means_nothing_to_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gate, "FIXTURES", tmp_path / "absent")
    assert gate.main([]) == 0


def test_records_without_a_document_fail(workspace: tuple[Path, Path]):
    assert gate.main([]) == 1


def test_a_written_block_verifies(workspace: tuple[Path, Path]):
    _, document = workspace
    assert gate.main(["--write"]) == 0
    assert gate.BEGIN in document.read_text(encoding="utf-8")
    assert gate.main([]) == 0


def test_a_drifted_figure_is_caught(workspace: tuple[Path, Path]):
    _, document = workspace
    gate.main(["--write"])
    body = document.read_text(encoding="utf-8")
    row = next(line for line in body.splitlines() if line.startswith("| `"))
    document.write_text(body.replace(row, row.replace("|", "| x", 1)), encoding="utf-8")
    assert gate.main([]) == 1


def test_write_leaves_the_prose_around_the_block_alone(workspace: tuple[Path, Path]):
    _, document = workspace
    document.write_text("# Real models\n\nAuthor prose before.\n", encoding="utf-8")
    gate.main(["--write"])
    document.write_text(
        document.read_text(encoding="utf-8") + "\nAuthor prose after.\n", encoding="utf-8"
    )
    gate.main(["--write"])  # a second regeneration must keep both paragraphs
    body = document.read_text(encoding="utf-8")
    assert "Author prose before." in body
    assert "Author prose after." in body
    assert body.count(gate.BEGIN) == 1


def test_the_version_line_is_the_recorded_one(workspace: tuple[Path, Path]):
    """A report re-rendered after a version bump must name the version that recorded it."""
    from trajectory_runner.store import read_runs

    fixtures, _ = workspace
    recorded = {run.harness_version for run in read_runs(fixtures)}
    rendered = gate.render(fixtures)
    assert f"- Harness version: `{', '.join(sorted(recorded))}`" in rendered


def test_a_task_carries_binary_signal_only_when_its_samples_disagree():
    from trajectory_runner.store import read_runs

    runs = [run for run in read_runs(RECORDED) if run.config.model == "stub:hasty"]
    lines = gate.signal_lines(runs)
    table = lines[: lines.index("### One group, worked")]
    body = [line for line in table if line.startswith("| ") and "-0" in line.split("|")[1]]
    assert len(body) == 12
    for row in body:
        cell = [part.strip() for part in row.strip("|").split("|")][1]
        bold = cell.startswith("**")
        solved, samples = (int(n) for n in cell.strip("*").split("/"))
        assert bold == (0 < solved < samples), row


def test_every_model_gets_a_column_and_a_summary_line():
    from trajectory_runner.store import read_runs

    runs = read_runs(RECORDED)
    lines = gate.signal_lines(runs)
    header = next(line for line in lines if line.startswith("| task |"))
    assert header.count("`stub:") == 5
    summaries = [line for line in lines if line.startswith("- Tasks with a learning signal")]
    assert len(summaries) == 5


def test_the_worked_group_has_advantages_that_sum_to_zero():
    from trajectory_runner.store import read_runs

    runs = read_runs(RECORDED)
    lines = gate.signal_lines(runs)
    start = lines.index("### One group, worked")
    rows = [line for line in lines[start:] if line.startswith("| ") and "---" not in line][1:]
    assert len(rows) >= 2
    binary = [float(row.strip("|").split("|")[3]) for row in rows]
    assert abs(sum(binary)) < 0.02  # each cell is rounded to two places
    assert any(value > 0 for value in binary) and any(value < 0 for value in binary)


def test_a_committed_judge_agreement_is_rendered_and_held(workspace: tuple[Path, Path]):
    fixtures, document = workspace
    ids = sorted(path.stem for path in fixtures.glob("*.json"))[:4]
    gate.AGREEMENT.write_text(json.dumps(_agreement(ids)), encoding="utf-8")
    assert gate.main(["--write"]) == 0
    body = document.read_text(encoding="utf-8")
    assert "#### Two judges compared" in body or "### Two judges compared" in body
    assert "| F06 ignored test output | 1 | 1 | 0 | 2 | 0.75 | 0.50 |" in body
    assert "| F10 environment mismatch | 0 | 0 | 0 | 4 | 1.00 | not computable |" in body
    assert "mean absolute difference 0.125, largest 0.400" in body
    assert gate.main([]) == 0

    changed = _agreement(ids)
    changed["drift_max_absolute_difference"] = 0.5
    gate.AGREEMENT.write_text(json.dumps(changed), encoding="utf-8")
    assert gate.main([]) == 1


def test_runs_left_out_of_the_comparison_are_counted_by_reason(workspace: tuple[Path, Path]):
    fixtures, document = workspace
    ids = sorted(path.stem for path in fixtures.glob("*.json"))[:4]
    report = _agreement(ids)
    report["left_out"] = {
        "a": "the second judge failed: timed out",
        "b": "not reached before the deadline",
        "c": "not reached before the deadline",
    }
    gate.AGREEMENT.write_text(json.dumps(report), encoding="utf-8")
    assert gate.main(["--write"]) == 0
    body = document.read_text(encoding="utf-8")
    assert (
        "- Left out of the comparison: 1 run(s) the second judge returned no valid verdict "
        "on and 2 run(s) not reached before the workflow's deadline"
    ) in body
    assert gate.main([]) == 0


def test_a_report_with_nothing_left_out_says_nothing_about_it(workspace: tuple[Path, Path]):
    fixtures, document = workspace
    ids = sorted(path.stem for path in fixtures.glob("*.json"))[:4]
    report = _agreement(ids)
    report["left_out"] = {}
    gate.AGREEMENT.write_text(json.dumps(report), encoding="utf-8")
    assert gate.main(["--write"]) == 0
    assert "Left out of the comparison" not in document.read_text(encoding="utf-8")


def test_an_agreement_on_runs_no_longer_committed_fails(workspace: tuple[Path, Path]):
    _, document = workspace
    gate.AGREEMENT.write_text(json.dumps(_agreement(["not-a-committed-run"])), encoding="utf-8")
    assert gate.main(["--write"]) == 1
    assert not document.exists()


def test_how_each_run_ended_keeps_the_provider_apart(workspace: tuple[Path, Path]):
    """A run the provider ended has its own column, apart from the agent's own endings."""
    from trajectory_core.models import Run, RunStatus

    fixtures, _ = workspace
    first = sorted(fixtures.glob("*.json"))[0]
    run = Run.model_validate_json(first.read_text(encoding="utf-8"))
    ended = run.model_copy(
        update={
            "status": RunStatus.ERROR,
            "error": f"{run.config.model} failed after 4 attempt(s): Request timed out",
        }
    )
    first.write_text(ended.model_dump_json(indent=2), encoding="utf-8")
    lines = gate.ending_lines(gate.read_runs(fixtures))
    assert lines[0] == "### How each run ended"
    row = next(line for line in lines if gate._short(run.config.model) in line)
    assert row.rstrip(" |").endswith("| 1 | 0")
