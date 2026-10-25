"""The demo reads both committed run sets and tells the reader which rows are which."""

from __future__ import annotations

import json
from pathlib import Path

import build_web_bundle as bundle

ROOT = Path(__file__).resolve().parent.parent


def _index(out: Path) -> dict:
    return json.loads((out / "index.json").read_text(encoding="utf-8"))


def test_from_fixtures_reads_the_scripted_and_the_real_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    out = tmp_path / "data"
    assert bundle.main(["--from-fixtures", "--out", str(out)]) == 0

    index = _index(out)
    committed = sum(len(list(d.glob("*.json"))) for d in bundle.DEFAULT_FIXTURES)
    assert index["run_count"] == committed
    assert any(m.startswith("stub:") for m in index["models"])
    assert any(not m.startswith("stub:") for m in index["models"])
    # Both halves of the note, because a mixed table without them misleads.
    assert bundle.STUB_NOTE in index["note"]
    assert bundle.REAL_NOTE in index["note"]


def test_a_named_directory_reads_only_that_set(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    out = tmp_path / "data"
    assert bundle.main(["--from-fixtures", "fixtures/recorded-runs", "--out", str(out)]) == 0

    index = _index(out)
    assert all(m.startswith("stub:") for m in index["models"])
    assert bundle.STUB_NOTE in index["note"]
    assert bundle.REAL_NOTE not in index["note"]


def _agreement(run_ids: list[str]) -> dict:
    entry = {
        "both_present": 1,
        "first_only": 0,
        "second_only": 1,
        "neither_present": 2,
        "raw_agreement": 0.75,
        "cohens_kappa": 0.5,
    }
    return {
        "first_judge": "nvidia_nim/a/first",
        "second_judge": "nvidia_nim/b/second",
        "temperature": 0.0,
        "eligible_runs": 4,
        "sampled_runs": 4,
        "compared_runs": 4,
        "per_mode": {"F06": entry},
        "drift_mean_absolute_difference": 0.1,
        "drift_max_absolute_difference": 0.2,
        "runs": run_ids,
        "generated_at": "2026-10-25T00:00:00+00:00",
    }


def test_a_judge_agreement_ships_only_beside_its_own_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(ROOT)
    committed = sorted(path.stem for path in (ROOT / "fixtures" / "model-runs").glob("*.json"))
    agreement = tmp_path / "judge-agreement.json"
    monkeypatch.setattr(bundle, "JUDGE_AGREEMENT", agreement)

    agreement.write_text(json.dumps(_agreement(committed[:2])), encoding="utf-8")
    out = tmp_path / "with"
    assert bundle.main(["--from-fixtures", "--out", str(out)]) == 0
    shipped = json.loads((out / "judge-agreement.json").read_text(encoding="utf-8"))
    assert shipped["second_judge"] == "nvidia_nim/b/second"

    # Measured on runs that are not in the bundle: it says nothing about these, so it stays out.
    agreement.write_text(json.dumps(_agreement(["no-such-run"])), encoding="utf-8")
    out = tmp_path / "without"
    assert bundle.main(["--from-fixtures", "--out", str(out)]) == 0
    assert not (out / "judge-agreement.json").exists()
