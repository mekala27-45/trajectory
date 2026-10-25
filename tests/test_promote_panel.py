"""Promoting a panel: what it refuses, what it replaces, and what it says about itself."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import promote_panel as promotion
import pytest

ROOT = Path(__file__).resolve().parent.parent
MODEL_RUNS = ROOT / "fixtures" / "model-runs"
RECORDED = ROOT / "fixtures" / "recorded-runs"


def _copy(source: Path, target: Path, count: int) -> Path:
    target.mkdir(parents=True)
    for path in sorted(source.glob("*.json"))[:count]:
        shutil.copy2(path, target / path.name)
    return target


def test_a_scripted_policy_is_refused(tmp_path: Path):
    records = _copy(RECORDED, tmp_path / "panel", 3)
    with pytest.raises(promotion.PromotionError, match="scripted policy"):
        promotion.load(records)


def test_an_empty_directory_is_refused(tmp_path: Path):
    (tmp_path / "panel").mkdir()
    with pytest.raises(promotion.PromotionError, match="no run records"):
        promotion.load(tmp_path / "panel")


def test_mixed_harness_versions_are_refused(tmp_path: Path):
    records = _copy(MODEL_RUNS, tmp_path / "panel", 2)
    first = sorted(records.glob("*.json"))[0]
    body = json.loads(first.read_text(encoding="utf-8"))
    body["harness_version"] = "9.9.9"
    first.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(promotion.PromotionError, match="several harness versions"):
        promotion.load(records)


def test_promotion_replaces_the_records_and_states_where_they_came_from(tmp_path: Path):
    records = _copy(MODEL_RUNS, tmp_path / "panel", 4)
    fixtures = _copy(MODEL_RUNS, tmp_path / "fixtures", 6)
    provenance_path = tmp_path / "model-runs.provenance.json"
    runs = promotion.load(records)
    meta = promotion.provenance(runs, run_id="123", request={"judge_model": "nvidia_nim/j/m"})
    promotion.promote(runs, meta, fixtures, provenance_path)

    assert sorted(p.name for p in fixtures.glob("*.json")) == sorted(
        p.name for p in records.glob("*.json")
    )
    written = json.loads(provenance_path.read_text(encoding="utf-8"))
    assert written["panel_run_id"] == "123"
    assert written["judge_model"] == "nvidia_nim/j/m"
    assert written["harness_version"] == runs[0].harness_version
    (counts,) = written["models"].values()
    assert counts["runs"] == 4
