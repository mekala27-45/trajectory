"""The em dash rule governs authored text, never a real model's recorded words."""

from __future__ import annotations

from pathlib import Path

import check_no_em_dash as gate

DASH = chr(0x2014)


def test_authored_text_is_checked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text(f"a {DASH} b\n", encoding="utf-8")
    assert gate.main(["notes.md"]) == 1


def test_a_real_models_recorded_output_is_left_verbatim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    record = tmp_path / "fixtures" / "model-runs" / "run.json"
    record.parent.mkdir(parents=True)
    record.write_text(f'{{"thought": "first {DASH} then"}}\n', encoding="utf-8")
    assert gate.main(["fixtures/model-runs/run.json"]) == 0


def test_the_scripted_policies_records_stay_checked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    record = tmp_path / "fixtures" / "recorded-runs" / "run.json"
    record.parent.mkdir(parents=True)
    record.write_text(f'{{"thought": "first {DASH} then"}}\n', encoding="utf-8")
    assert gate.main(["fixtures/recorded-runs/run.json"]) == 1
