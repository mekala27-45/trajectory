"""The model panel planner: request validation, ceilings, rate shares, and the job matrix."""

from __future__ import annotations

import json
import shlex
from pathlib import Path

import plan_panel as panel
import pytest

from trajectory_runner.loader import discover

ROOT = Path(__file__).resolve().parent.parent
TASKS = discover(ROOT / "tasks", suite="core-12")
REQUEST = ROOT / "model-requests" / "panel.json"


def body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "purpose": "test panel",
        "models": [{"model": "nvidia_nim/vendor/model-a"}],
    }
    base.update(overrides)
    return base


def test_the_committed_request_is_valid():
    request = panel.parse_request(json.loads(REQUEST.read_text(encoding="utf-8")), TASKS)
    assert request.entries
    assert request.rpm_total <= panel.RPM_CEILING
    assert len({entry.slug for entry in request.entries}) == len(request.entries)


def test_the_default_ceiling_can_never_cut_a_seed():
    request = panel.parse_request(body(seeds=[0, 1]), TASKS)
    (entry,) = request.entries
    assert entry.max_calls == 2 * sum(loaded.task.max_steps for loaded in TASKS)


def test_a_slice_gets_a_ceiling_for_the_slice_only():
    request = panel.parse_request(
        body(models=[{"model": "nvidia_nim/v/m", "tasks": ["py-perf-01"], "seeds": [1]}]),
        TASKS,
    )
    (entry,) = request.entries
    perf = next(loaded for loaded in TASKS if loaded.id == "py-perf-01")
    assert entry.max_calls == perf.task.max_steps
    assert entry.tasks == ("py-perf-01",)
    assert entry.seeds == (1,)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"purpose": ""}, "purpose"),
        ({"models": []}, "models"),
        ({"models": [{"model": "stub:methodical"}]}, "scripted policy"),
        ({"models": [{"model": "no-provider"}]}, "not a model identifier"),
        ({"models": [{"model": "nvidia_nim/v/m; rm -rf /"}]}, "not a model identifier"),
        ({"models": [{"model": "nvidia_nim/v/m", "tasks": ["nope-01"]}]}, "not in core-12"),
        ({"models": [{"model": "nvidia_nim/v/m", "seeds": [1, 1]}]}, "distinct"),
        ({"models": [{"model": "nvidia_nim/v/m", "max_calls": 0}]}, "max_calls"),
        ({"models": [{"model": "nvidia_nim/v/m", "colour": "red"}]}, "unknown keys"),
        ({"rpm_total": 120}, "rpm_total"),
        ({"surprise": True}, "unknown keys"),
        ({"judge_model": "not a model"}, "judge_model"),
        (
            {"models": [{"model": "nvidia_nim/a/same"}, {"model": "nvidia_nim/b/same"}]},
            "share a job name",
        ),
    ],
)
def test_a_bad_request_is_refused_with_its_reason(change: dict[str, object], message: str):
    with pytest.raises(panel.RequestError, match=message):
        panel.parse_request(body(**change), TASKS)


def test_the_rate_is_shared_and_never_overshoots():
    request = panel.parse_request(body(rpm_total=34, max_parallel=4), TASKS)
    assert panel.rate_per_job(request, 7) == 8.5
    assert panel.rate_per_job(request, 2) == 17.0
    assert panel.rate_per_job(request, 7) * 4 <= 34


def test_the_job_matrix_round_trips_through_a_plain_split():
    request = panel.parse_request(
        body(
            judge_model="nvidia_nim/judge/model",
            models=[
                {"model": "ollama_chat/qwen2.5-coder:7b"},
                {"model": "nvidia_nim/v/m", "tasks": ["py-perf-01", "go-race-01"], "seeds": [1]},
            ],
        ),
        TASKS,
    )
    jobs = panel.plan(request, list(request.entries), request.judge_model)
    assert [job["slug"] for job in jobs] == ["qwen2.5-coder-7b", "m"]
    for job in jobs:
        # The workflow rebuilds the argument list with a whitespace split, which is only
        # safe when no token needed quoting.
        assert job["args"].split() == shlex.split(job["args"])
    second = jobs[1]["args"].split()
    assert second[second.index("--seed") + 1] == "1"
    assert {second[i + 1] for i, a in enumerate(second) if a == "--task"} == {
        "py-perf-01",
        "go-race-01",
    }
    assert second[second.index("--judge-model") + 1] == "nvidia_nim/judge/model"


def test_a_failed_probe_leaves_the_model_out(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    request = tmp_path / "panel.json"
    request.write_text(
        json.dumps(body(models=[{"model": "nvidia_nim/v/good"}, {"model": "nvidia_nim/v/bad"}])),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        panel, "probe", lambda model, timeout=90: (model.endswith("good"), "detail")
    )
    output = tmp_path / "out"
    summary = tmp_path / "summary.md"
    code = panel.main(
        [str(request), "--smoke", "--github-output", str(output), "--summary", str(summary)]
    )
    assert code == 0
    written = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    jobs = json.loads(written["entries"])
    assert [job["model"] for job in jobs] == ["nvidia_nim/v/good"]
    assert written["count"] == "1"
    assert "left out" in summary.read_text(encoding="utf-8")


def test_no_passing_model_fails_the_plan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    request = tmp_path / "panel.json"
    request.write_text(json.dumps(body()), encoding="utf-8")
    monkeypatch.setattr(panel, "probe", lambda model, timeout=90: (False, "FAILED: nope"))
    assert panel.main([str(request), "--smoke"]) == 1


def test_records_summary_counts_by_model(tmp_path: Path):
    recorded = sorted((ROOT / "fixtures" / "model-runs").glob("*.json"))[:3]
    for path in recorded:
        (tmp_path / path.name).write_bytes(path.read_bytes())
    text = panel.records_summary(tmp_path)
    assert "| `nvidia_nim/nvidia/nemotron-3.5-lightning-30b-a3b` | 3 |" in text
    assert "No sealed run records" in panel.records_summary(tmp_path / "absent")
