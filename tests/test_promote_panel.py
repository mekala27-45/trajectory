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
    assert sum(counts["runs"] for counts in written["models"].values()) == 4


def test_promotion_rescores_with_the_current_rules(tmp_path: Path):
    """A metric fixed after a panel started reaches its records at promotion."""
    from trajectory_runner.loader import discover

    tasks = {loaded.id: loaded.task for loaded in discover(ROOT / "tasks")}
    runs = promotion.load(_copy(MODEL_RUNS, tmp_path / "panel", 3))
    stale = [
        run.model_copy(update={"score": run.score.model_copy(update={"recovery_rate": 0.123})})
        for run in runs
        if run.score is not None
    ]
    fresh = promotion.rescored(stale, tasks)
    assert all(run.score is not None and run.score.recovery_rate != 0.123 for run in fresh)
    assert [run.steps for run in fresh] == [run.steps for run in stale]


def test_a_record_for_an_unknown_task_is_refused(tmp_path: Path):
    runs = promotion.load(_copy(MODEL_RUNS, tmp_path / "panel", 1))
    with pytest.raises(promotion.PromotionError, match="no task definition"):
        promotion.rescored(runs, {})


def _provider_ended_and_other() -> tuple[list, object, object]:
    runs = promotion.load(MODEL_RUNS)
    ended = next(run for run in runs if promotion.provider_ended(run))
    finished = next(run for run in runs if not promotion.provider_ended(run))
    return runs, ended, finished


def _rerun_of(run, run_id: str):
    from trajectory_core.models import RunStatus

    return run.model_copy(update={"id": run_id, "status": RunStatus.COMPLETED, "error": None})


def test_a_rerun_replaces_only_a_run_the_provider_ended():
    runs, ended, _ = _provider_ended_and_other()
    rerun = _rerun_of(ended, "01a1ffff-0000-7000-8000-000000000001")
    merged, replaced = promotion.merge_reruns(runs, [rerun])
    assert len(merged) == len(runs)
    assert rerun.id in {run.id for run in merged}
    assert ended.id not in {run.id for run in merged}
    assert replaced[0]["original_run"] == ended.id
    assert replaced[0]["rerun_provider_ended"] is False


def test_a_rerun_of_a_run_the_agent_finished_is_refused():
    """Replacing an agent's own failure with a second try would be choosing by outcome."""
    runs, _, finished = _provider_ended_and_other()
    rerun = _rerun_of(finished, "01a1ffff-0000-7000-8000-000000000002")
    with pytest.raises(promotion.PromotionError, match="it stands"):
        promotion.merge_reruns(runs, [rerun])


def test_two_reruns_of_one_run_or_a_rerun_of_nothing_are_refused():
    runs, ended, _ = _provider_ended_and_other()
    first = _rerun_of(ended, "01a1ffff-0000-7000-8000-000000000003")
    second = _rerun_of(ended, "01a1ffff-0000-7000-8000-000000000004")
    with pytest.raises(promotion.PromotionError, match="two reruns"):
        promotion.merge_reruns(runs, [first, second])
    stray = first.model_copy(update={"task_id": "py-perf-01"})
    others = [run for run in runs if run.task_id != "py-perf-01"]
    with pytest.raises(promotion.PromotionError, match="no run to replace"):
        promotion.merge_reruns(others, [stray])
