"""The model figures gate: regenerate, verify, catch drift, and leave the prose alone."""

from __future__ import annotations

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
    return fixtures, document


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
