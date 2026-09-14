"""Integration tests against a real Postgres.

What these assert, in order of how much it would cost to get wrong: that no read route can
leak anything about a hidden test, that ingest is idempotent and rejects a corrupted
upload, that the leaderboard never averages an unisolated run together with a container
run, and that the numbers the API serves are the same numbers the CLI computes.
"""

from __future__ import annotations

import json

import pytest
from conftest import build_bundle, build_run, build_task  # type: ignore[import-not-found]
from fastapi.testclient import TestClient
from sqlmodel import Session

from trajectory_api.db import engine
from trajectory_api.queries import list_run_summaries, reconstruct_runs
from trajectory_api.security import RateLimiter, bearer_token, token_matches
from trajectory_core.aggregate import leaderboard
from trajectory_core.models import SandboxBackend
from trajectory_core.scoring import score
from trajectory_core.testing import make_task


def ingest(client: TestClient, auth: dict[str, str], runs: list, **kwargs) -> dict:
    """Post a bundle and return the parsed response."""
    response = client.post(
        "/v1/runs",
        content=build_bundle(runs, **kwargs).model_dump_json(),
        headers={**auth, "Content-Type": "application/json"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def register(client: TestClient, auth: dict[str, str], tasks: list) -> None:
    """Register task metadata."""
    payload = [task.model_dump(mode="json") for task in tasks]
    response = client.post("/v1/tasks", json=payload, headers=auth)
    assert response.status_code == 200, response.text


class TestOperations:
    def test_healthz_reports_the_database(self, client: TestClient):
        body = client.get("/healthz").json()
        assert body["status"] == "ok"
        assert body["database"] == "ok"

    def test_root_points_somewhere_useful(self, client: TestClient):
        body = client.get("/").json()
        assert body["docs"] == "/docs"
        assert "leaderboard" in body

    def test_openapi_renders(self, client: TestClient):
        spec = client.get("/openapi.json").json()
        assert spec["info"]["title"] == "trajectory results API"
        assert "/v1/leaderboard" in spec["paths"]
        assert "/v1/runs/{run_id}/trajectory" in spec["paths"]

    def test_docs_render(self, client: TestClient):
        assert client.get("/docs").status_code == 200

    def test_metrics_are_prometheus_formatted(self, client: TestClient, auth):
        ingest(client, auth, [build_run()])
        body = client.get("/metrics").text
        assert "trajectory_runs_stored" in body
        assert "# TYPE trajectory_runs_stored gauge" in body

    def test_every_response_carries_a_request_id(self, client: TestClient):
        response = client.get("/v1/tasks")
        assert response.headers["X-Request-ID"]

    def test_a_supplied_request_id_is_echoed(self, client: TestClient):
        response = client.get("/v1/tasks", headers={"X-Request-ID": "abc123"})
        assert response.headers["X-Request-ID"] == "abc123"


class TestAuth:
    def test_read_routes_are_public(self, client: TestClient):
        """The project exists so a stranger can look at the results."""
        for path in ("/v1/index", "/v1/leaderboard", "/v1/tasks", "/v1/failure-modes"):
            assert client.get(path).status_code == 200, path

    def test_write_routes_need_a_token(self, client: TestClient):
        response = client.post("/v1/runs", json={})
        assert response.status_code == 401
        assert "bearer token" in response.json()["detail"]
        assert response.headers["WWW-Authenticate"] == "Bearer"

    def test_a_wrong_token_is_rejected(self, client: TestClient):
        response = client.post("/v1/runs", json={}, headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401

    def test_a_malformed_header_is_rejected(self, client: TestClient):
        for header in ("", "Basic abc", "Bearer", "bearer "):
            response = client.post("/v1/runs", json={}, headers={"Authorization": header})
            assert response.status_code == 401, header

    def test_token_comparison_helpers(self):
        assert bearer_token("Bearer abc") == "abc"
        assert bearer_token("bearer  abc ") == "abc"
        assert bearer_token("Basic abc") == ""
        assert bearer_token(None) == ""
        assert token_matches("abc", "abc") is True
        assert token_matches("abc", "abd") is False
        assert token_matches("", "abc") is False

    def test_a_valid_token_reaches_validation_not_a_401(self, client: TestClient, auth):
        """A good token with a bad body must be a 422, which proves auth ran and passed."""
        assert client.post("/v1/runs", json={}, headers=auth).status_code == 422


class TestRateLimiter:
    def test_a_bucket_refills_continuously(self):
        """Fixed windows let a client spend two allowances across a window boundary."""
        limiter = RateLimiter(per_minute=60, burst=1)
        assert limiter.allow("client", now=0.0)[0] is True
        allowed, retry_after = limiter.allow("client", now=0.0)
        assert allowed is False
        assert retry_after == pytest.approx(1.0, abs=0.01)
        assert limiter.allow("client", now=1.0)[0] is True

    def test_clients_are_limited_independently(self):
        limiter = RateLimiter(per_minute=60, burst=1)
        assert limiter.allow("a", now=0.0)[0] is True
        assert limiter.allow("b", now=0.0)[0] is True

    def test_the_burst_allowance_is_capped(self):
        limiter = RateLimiter(per_minute=60, burst=2)
        assert limiter.allow("a", now=0.0)[0] is True
        assert limiter.allow("a", now=0.0)[0] is True
        assert limiter.allow("a", now=0.0)[0] is False
        # A long idle period refills to the burst ceiling and no further.
        assert limiter.allow("a", now=10_000.0)[0] is True
        assert limiter.allow("a", now=10_000.0)[0] is True
        assert limiter.allow("a", now=10_000.0)[0] is False

    def test_writes_are_limited_through_the_route(self, client: TestClient, auth):
        from trajectory_api import security

        security.set_limiter(RateLimiter(per_minute=60, burst=2))
        assert client.post("/v1/tasks", json=[], headers=auth).status_code == 200
        assert client.post("/v1/tasks", json=[], headers=auth).status_code == 200
        response = client.post("/v1/tasks", json=[], headers=auth)
        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_reads_are_not_limited(self, client: TestClient):
        from trajectory_api import security

        security.set_limiter(RateLimiter(per_minute=60, burst=1))
        for _ in range(10):
            assert client.get("/v1/leaderboard").status_code == 200


class TestIngest:
    def test_a_bundle_is_stored(self, client: TestClient, auth):
        body = ingest(client, auth, [build_run(), build_run(seed=1)])
        assert body["accepted"] == 2
        assert body["duplicates"] == 0

    def test_ingest_is_idempotent_on_run_identifier(self, client: TestClient, auth):
        """Re-pushing after a timeout has to be safe, or nobody retries and results are lost."""
        run = build_run()
        first = ingest(client, auth, [run])
        second = ingest(client, auth, [run])
        assert first["accepted"] == 1
        assert second["accepted"] == 0
        assert second["duplicates"] == 1
        assert len(client.get("/v1/runs").json()) == 1

    def test_a_corrupted_bundle_is_rejected_whole(self, client: TestClient, auth):
        run = build_run()
        bundle = build_bundle([run])
        payload = json.loads(bundle.model_dump_json())
        payload["runs"][0]["steps"] = payload["runs"][0]["steps"][:1]
        response = client.post("/v1/runs", json=payload, headers=auth)
        assert response.status_code == 400
        assert "content hash" in response.json()["detail"]
        assert client.get("/v1/runs").json() == []

    def test_a_manifest_that_miscounts_its_runs_is_rejected(self, client: TestClient, auth):
        bundle = build_bundle([build_run()])
        payload = json.loads(bundle.model_dump_json())
        payload["manifest"]["run_count"] = 5
        payload["manifest"]["content_sha256"] = ""
        response = client.post("/v1/runs", json=payload, headers=auth)
        assert response.status_code == 400
        assert "declares 5 runs" in response.json()["detail"]

    def test_an_unknown_schema_version_is_rejected_with_a_readable_message(
        self, client: TestClient, auth
    ):
        bundle = build_bundle([build_run()])
        payload = json.loads(bundle.model_dump_json())
        payload["manifest"]["schema_version"] = 99
        payload["manifest"]["content_sha256"] = ""
        response = client.post("/v1/runs", json=payload, headers=auth)
        assert response.status_code == 400
        assert "schema version" in response.json()["detail"]

    def test_an_oversized_bundle_is_rejected(self, client: TestClient, auth, settings):
        runs = [build_run(seed=index) for index in range(4)]
        bundle = build_bundle(runs)
        payload = json.loads(bundle.model_dump_json())
        payload["manifest"]["content_sha256"] = ""
        settings.trajectory_max_bundle_runs = 2
        try:
            response = client.post("/v1/runs", json=payload, headers=auth)
            assert response.status_code == 400
            assert "ceiling for one request" in response.json()["detail"]
        finally:
            settings.trajectory_max_bundle_runs = 200

    def test_an_unknown_field_is_rejected_rather_than_dropped(self, client: TestClient, auth):
        """A bundle from a newer harness must fail loudly, not lose fields silently."""
        payload = json.loads(build_bundle([build_run()]).model_dump_json())
        payload["runs"][0]["a_field_from_the_future"] = True
        response = client.post("/v1/runs", json=payload, headers=auth)
        assert response.status_code == 422

    def test_local_backend_runs_are_stored_with_a_warning(self, client: TestClient, auth):
        body = ingest(client, auth, [build_run(backend=SandboxBackend.LOCAL)])
        assert body["accepted"] == 1
        assert "unisolated local sandbox" in body["message"]

    def test_container_runs_produce_no_warning(self, client: TestClient, auth):
        assert ingest(client, auth, [build_run()])["message"] == ""

    def test_the_bundle_manifest_is_kept_for_provenance(self, client: TestClient, auth):
        from sqlmodel import select

        from trajectory_api.tables import BundleRow

        body = ingest(client, auth, [build_run()], notes="python scripts/run_matrix.py")
        with Session(engine()) as session:
            rows = list(session.exec(select(BundleRow)))
        assert len(rows) == 1
        assert rows[0].id == body["bundle_id"]
        assert rows[0].notes == "python scripts/run_matrix.py"

    def test_steps_and_failure_hits_are_stored_as_rows(self, client: TestClient, auth):
        from sqlalchemy import func
        from sqlmodel import select

        from trajectory_api.tables import StepRow

        run = build_run(steps=6)
        ingest(client, auth, [run])
        with Session(engine()) as session:
            count = int(session.exec(select(func.count()).select_from(StepRow)).one())
        assert count == len(run.steps)


class TestReadRoutes:
    def test_tasks_never_expose_the_hidden_tests(self, client: TestClient, auth):
        """The security property of the benchmark, asserted at the API boundary."""
        register(client, auth, [build_task()])
        body = client.get("/v1/tasks").text
        assert "verify" not in body
        assert "verify_cmd" not in body
        assert "pytest" not in body

    def test_task_results_never_expose_the_hidden_tests(self, client: TestClient, auth):
        register(client, auth, [build_task()])
        ingest(client, auth, [build_run()])
        body = client.get("/v1/tasks/py-failing-suite-01/results").text
        assert "verify_cmd" not in body

    def test_index_summarises_the_dataset(self, client: TestClient, auth):
        ingest(client, auth, [build_run(), build_run(seed=1, solved=False)])
        body = client.get("/v1/index").json()
        assert body["run_count"] == 2
        assert body["solved_count"] == 1
        assert body["models"] == ["stub:methodical"]

    def test_the_index_note_flags_scripted_policies(self, client: TestClient, auth):
        ingest(client, auth, [build_run(model="stub:methodical")])
        assert "scripted offline policies" in client.get("/v1/index").json()["note"]

    def test_leaderboard_aggregates_across_seeds(self, client: TestClient, auth):
        runs = [build_run(seed=seed, solved=seed < 2) for seed in range(3)]
        ingest(client, auth, runs)
        body = client.get("/v1/leaderboard").json()
        assert len(body["rows"]) == 1
        row = body["rows"][0]
        assert row["runs"] == 3
        assert row["seeds"] == 3
        assert row["solve_rate"]["mean"] == pytest.approx(2 / 3)
        assert row["solve_rate"]["stdev"] > 0

    def test_leaderboard_never_merges_backends(self, client: TestClient, auth):
        """Averaging an unisolated run with a container run publishes an indefensible number."""
        ingest(
            client,
            auth,
            [build_run(), build_run(seed=1, backend=SandboxBackend.LOCAL, solved=False)],
        )
        rows = client.get("/v1/leaderboard").json()["rows"]
        assert len(rows) == 2
        assert {row["backend"] for row in rows} == {"docker", "local"}
        assert rows[0]["backend"] == "docker"

    def test_the_backend_filter_narrows_the_board(self, client: TestClient, auth):
        ingest(client, auth, [build_run(), build_run(seed=1, backend=SandboxBackend.LOCAL)])
        rows = client.get("/v1/leaderboard?backend=docker").json()["rows"]
        assert len(rows) == 1
        assert rows[0]["backend"] == "docker"

    def test_the_suite_filter_narrows_the_board(self, client: TestClient, auth):
        ingest(client, auth, [build_run()])
        assert client.get("/v1/leaderboard?suite=core-12").json()["rows"]
        assert client.get("/v1/leaderboard?suite=nope").json()["rows"] == []

    def test_the_leaderboard_matches_what_the_cli_computes(self, client: TestClient, auth):
        """One implementation of the arithmetic, so the terminal and the site agree."""
        runs = [build_run(seed=seed, solved=seed != 2) for seed in range(3)]
        ingest(client, auth, runs)
        with Session(engine()) as session:
            expected = leaderboard(reconstruct_runs(list_run_summaries(session)))
        served = client.get("/v1/leaderboard").json()["rows"]
        assert served[0]["solve_rate"]["mean"] == pytest.approx(expected[0].solve_rate.mean)
        assert served[0]["step_efficiency"]["mean"] == pytest.approx(
            expected[0].step_efficiency.mean
        )

    def test_a_missing_task_is_a_404(self, client: TestClient):
        assert client.get("/v1/tasks/nope/results").status_code == 404

    def test_a_missing_run_is_a_404(self, client: TestClient):
        assert client.get("/v1/runs/nope").status_code == 404
        assert client.get("/v1/runs/nope/trajectory").status_code == 404

    def test_a_run_round_trips_through_the_database(self, client: TestClient, auth):
        run = build_run(steps=5)
        ingest(client, auth, [run])
        body = client.get(f"/v1/runs/{run.id}").json()
        assert body["task_id"] == run.task_id
        assert len(body["steps"]) == len(run.steps)
        assert body["solved"] is True
        assert body["image_id"] == run.image_id
        assert body["runner_fingerprint"]["sandbox_backend"] == "docker"
        assert body["score"]["step_efficiency"] is not None

    def test_steps_can_be_omitted_for_a_lighter_response(self, client: TestClient, auth):
        run = build_run(steps=5)
        ingest(client, auth, [run])
        body = client.get(f"/v1/runs/{run.id}?include_steps=false").json()
        assert body["steps"] == []

    def test_a_trajectory_is_paginated(self, client: TestClient, auth):
        run = build_run(steps=7)
        ingest(client, auth, [run])
        page = client.get(f"/v1/runs/{run.id}/trajectory?offset=2&limit=3").json()
        assert page["total"] == len(run.steps)
        assert [step["index"] for step in page["steps"]] == [2, 3, 4]

    def test_timestamps_keep_their_timezone_through_the_database(self, client: TestClient, auth):
        """A naive timestamp column would silently drop the offset."""
        run = build_run()
        ingest(client, auth, [run])
        body = client.get(f"/v1/runs/{run.id}").json()
        assert body["started_at"].endswith("Z") or "+00:00" in body["started_at"]

    def test_failure_modes_are_returned_ranked(self, client: TestClient, auth):
        from trajectory_core.failure_modes import classify_rules
        from trajectory_core.testing import bash_steps as bs
        from trajectory_core.testing import finish_step as fs
        from trajectory_core.testing import make_run, make_verification

        run = make_run(
            [*bs("git reset --hard", "chmod -R 777 ."), fs(2)],
            verification=make_verification(passed=False, tests_passed=0, tests_total=8),
        )
        run.score = score(run, make_task())
        run.failure_modes = classify_rules(run, make_task())
        ingest(client, auth, [run])
        body = client.get(f"/v1/runs/{run.id}").json()
        ids = [hit["id"] for hit in body["failure_modes"]]
        assert "F08" in ids
        assert "F03" in ids


class TestFailureModeBreakdown:
    def test_taxonomy_is_always_complete(self, client: TestClient):
        body = client.get("/v1/failure-modes").json()
        assert len(body["taxonomy"]) == 10
        assert {entry["detection"] for entry in body["taxonomy"]} == {"rule", "judge"}

    def test_shares_use_the_right_denominator(self, client: TestClient, auth):
        from trajectory_core.models import Detector, FailureModeHit, FailureModeId

        solved = build_run(seed=0, solved=True)
        failed = build_run(seed=1, solved=False)
        failed.failure_modes = [
            FailureModeHit(
                id=FailureModeId.PREMATURE_SUCCESS,
                name="premature success",
                confidence=1.0,
                detector=Detector.RULE,
                evidence="e",
            )
        ]
        solved.failure_modes = [
            FailureModeHit(
                id=FailureModeId.TOOL_SCHEMA_VIOLATION,
                name="tool schema violation",
                confidence=1.0,
                detector=Detector.RULE,
                evidence="e",
            )
        ]
        ingest(client, auth, [solved, failed])
        body = client.get("/v1/failure-modes").json()
        assert body["unsolved_runs"] == 1
        assert body["solved_runs"] == 1
        assert [m["id"] for m in body["overall"]] == ["F03"]
        assert [m["id"] for m in body["on_solved_runs"]] == ["F01"]

    def test_the_population_can_be_chosen(self, client: TestClient, auth):
        from trajectory_core.models import Detector, FailureModeHit, FailureModeId

        run = build_run(solved=True)
        run.failure_modes = [
            FailureModeHit(
                id=FailureModeId.RETRY_LOOP,
                name="retry loop",
                confidence=1.0,
                detector=Detector.RULE,
                evidence="e",
            )
        ]
        ingest(client, auth, [run])
        unsolved_only = client.get("/v1/failure-modes?among=unsolved").json()
        everything = client.get("/v1/failure-modes?among=all").json()
        assert unsolved_only["by_model"]["stub:methodical"] == []
        assert everything["by_model"]["stub:methodical"][0]["id"] == "F04"

    def test_an_invalid_population_is_rejected(self, client: TestClient):
        assert client.get("/v1/failure-modes?among=sideways").status_code == 422

    def test_by_difficulty_uses_registered_task_metadata(self, client: TestClient, auth):
        from trajectory_core.models import Detector, FailureModeHit, FailureModeId

        register(client, auth, [build_task("py-failing-suite-01", difficulty=1)])
        run = build_run(solved=False)
        run.failure_modes = [
            FailureModeHit(
                id=FailureModeId.PREMATURE_SUCCESS,
                name="premature success",
                confidence=1.0,
                detector=Detector.RULE,
                evidence="e",
            )
        ]
        ingest(client, auth, [run])
        body = client.get("/v1/failure-modes").json()
        assert "1" in body["by_difficulty"]


class TestCors:
    def test_the_configured_origin_is_allowed(self, client: TestClient):
        response = client.get("/v1/tasks", headers={"Origin": "http://localhost:3000"})
        assert response.headers["access-control-allow-origin"] == "http://localhost:3000"

    def test_an_unknown_origin_is_not_allowed(self, client: TestClient):
        response = client.get("/v1/tasks", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in response.headers

    def test_preflight_is_answered_for_a_write(self, client: TestClient):
        response = client.options(
            "/v1/runs",
            headers={
                "Origin": "https://trajectory.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert response.status_code == 200
        assert "authorization" in response.headers["access-control-allow-headers"].lower()


class TestConfigurationSurface:
    """A setting the service reads and the example file omits is a deployment that fails."""

    def test_every_setting_appears_in_the_example_env_file(self):
        from pathlib import Path

        from trajectory_api.settings import Settings

        repo = Path(__file__).resolve().parents[3]
        example = (repo / ".env.example").read_text()
        documented = {
            line.split("=", 1)[0].strip()
            for line in example.splitlines()
            if "=" in line and not line.strip().startswith("#")
        }
        missing = {name.upper() for name in Settings.model_fields} - documented
        assert not missing, f".env.example does not mention {sorted(missing)}"

    def test_the_service_refuses_to_start_without_its_required_settings(self):
        import pydantic
        import pytest as pytest_module

        from trajectory_api.settings import Settings

        with pytest_module.raises(pydantic.ValidationError, match="database_url"):
            Settings(trajectory_api_key="x" * 12)  # type: ignore[call-arg]

    def test_a_non_postgres_url_is_refused_at_startup(self):
        """SQLite would appear to work and lose the data on the next deploy."""
        import pydantic
        import pytest as pytest_module

        from trajectory_api.settings import Settings

        with pytest_module.raises(pydantic.ValidationError, match="must be a Postgres URL"):
            Settings(database_url="sqlite:///results.db", trajectory_api_key="x" * 12)

    def test_a_short_api_key_is_refused_at_startup(self):
        import pydantic
        import pytest as pytest_module

        from trajectory_api.settings import Settings

        with pytest_module.raises(pydantic.ValidationError):
            Settings(database_url="postgresql+psycopg://a/b", trajectory_api_key="short")

    def test_a_bare_postgres_url_is_normalised_to_the_shipped_driver(self):
        from trajectory_api.settings import Settings

        settings = Settings(
            database_url="postgresql://user:pass@host/db", trajectory_api_key="x" * 12
        )
        assert settings.sqlalchemy_url.startswith("postgresql+psycopg://")

    def test_cors_origins_are_split_and_stripped(self):
        from trajectory_api.settings import Settings

        settings = Settings(
            database_url="postgresql+psycopg://a/b",
            trajectory_api_key="x" * 12,
            trajectory_cors_origins=" http://a.test , https://b.test ,, ",
        )
        assert settings.cors_origins == ["http://a.test", "https://b.test"]
