"""The seam between the runner and the results service."""

from __future__ import annotations

import json

import httpx
import pytest

from trajectory_core.testing import bash_steps, finish_step, make_run, make_verification
from trajectory_runner.push import (
    PushError,
    estimated_chunks,
    push_bundle,
    push_runs,
)
from trajectory_runner.store import bundle_content_hash, bundle_from_runs


def sample_runs(count: int):
    runs = []
    for index in range(count):
        run = make_run(
            [*bash_steps(f"cmd {index}"), finish_step(1)],
            verification=make_verification(),
            task_id=f"task-{index:02d}",
        )
        runs.append(run)
    return runs


def transport(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api.test")


def ok(accepted: int = 1, duplicates: int = 0, rejected: int = 0, message: str = ""):
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        count = len(payload["runs"])
        body = {
            "accepted": count if accepted else 0,
            "duplicates": duplicates,
            "rejected": rejected,
        }
        if message:
            body["message"] = message
        return httpx.Response(200, json=body)

    return handler


class TestPushBundle:
    def test_uploads_a_bundle_and_reports_counts(self):
        runs = sample_runs(3)
        bundle = bundle_from_runs(runs, suite="core-12", fingerprint=runs[0].runner_fingerprint)
        with transport(ok()) as client:
            result = push_bundle(bundle, api_url="http://api.test", api_key="k", client=client)
        assert result.accepted == 3
        assert result.ok is True
        assert result.chunks == 1

    def test_sends_the_bearer_token_on_the_write_route(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers["Authorization"]
            seen["path"] = request.url.path
            return httpx.Response(200, json={"accepted": 1, "duplicates": 0, "rejected": 0})

        runs = sample_runs(1)
        bundle = bundle_from_runs(runs, suite="core-12", fingerprint=runs[0].runner_fingerprint)
        with transport(handler) as client:
            push_bundle(bundle, api_url="http://api.test/", api_key="secret", client=client)
        assert seen["auth"] == "Bearer secret"
        assert seen["path"] == "/v1/runs"

    def test_chunks_a_large_bundle_and_each_chunk_carries_its_own_hash(self):
        """A single request with 144 full trajectories will be refused by something."""
        runs = sample_runs(10)
        bundle = bundle_from_runs(runs, suite="core-12", fingerprint=runs[0].runner_fingerprint)
        hashes: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            hashes.append(payload["manifest"]["content_sha256"])
            expected = bundle_content_hash(
                [type(runs[0]).model_validate(r) for r in payload["runs"]]
            )
            assert payload["manifest"]["content_sha256"] == expected
            return httpx.Response(
                200, json={"accepted": len(payload["runs"]), "duplicates": 0, "rejected": 0}
            )

        with transport(handler) as client:
            result = push_bundle(
                bundle, api_url="http://api.test", api_key="k", chunk_runs=4, client=client
            )
        assert result.chunks == 3
        assert result.accepted == 10
        assert len(set(hashes)) == 3

    def test_a_manifest_whose_hash_does_not_match_its_runs_is_refused_before_sending(self):
        runs = sample_runs(2)
        bundle = bundle_from_runs(runs, suite="core-12", fingerprint=runs[0].runner_fingerprint)
        bundle.manifest.content_sha256 = "0" * 64
        with pytest.raises(PushError, match="does not match its runs"):
            push_bundle(bundle, api_url="http://api.test", api_key="k")

    def test_a_server_error_is_reported_with_the_body(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, text="schema version 2 is not supported")

        runs = sample_runs(1)
        bundle = bundle_from_runs(runs, suite="core-12", fingerprint=runs[0].runner_fingerprint)
        with transport(handler) as client, pytest.raises(PushError, match="422"):
            push_bundle(bundle, api_url="http://api.test", api_key="k", client=client)

    def test_a_transport_failure_is_reported_with_the_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        runs = sample_runs(1)
        bundle = bundle_from_runs(runs, suite="core-12", fingerprint=runs[0].runner_fingerprint)
        with transport(handler) as client, pytest.raises(PushError, match="failed"):
            push_bundle(bundle, api_url="http://api.test", api_key="k", client=client)

    def test_duplicates_are_reported_and_are_not_a_failure(self):
        """Re-pushing after a network failure has to be safe, or nobody retries."""
        runs = sample_runs(2)
        bundle = bundle_from_runs(runs, suite="core-12", fingerprint=runs[0].runner_fingerprint)
        handler = ok(accepted=0, duplicates=2)
        with transport(handler) as client:
            result = push_bundle(bundle, api_url="http://api.test", api_key="k", client=client)
        assert result.duplicates == 2
        assert result.ok is True

    def test_rejections_make_the_result_not_ok(self):
        runs = sample_runs(1)
        bundle = bundle_from_runs(runs, suite="core-12", fingerprint=runs[0].runner_fingerprint)
        with transport(ok(rejected=1)) as client:
            result = push_bundle(bundle, api_url="http://api.test", api_key="k", client=client)
        assert result.ok is False

    def test_server_messages_are_surfaced(self):
        runs = sample_runs(1)
        bundle = bundle_from_runs(runs, suite="core-12", fingerprint=runs[0].runner_fingerprint)
        with transport(ok(message="2 local backend runs stored but excluded")) as client:
            result = push_bundle(bundle, api_url="http://api.test", api_key="k", client=client)
        assert result.messages == ["2 local backend runs stored but excluded"]


class TestPushRuns:
    def test_assembles_a_bundle_from_runs(self):
        with transport(ok()) as client:
            result = push_runs(
                sample_runs(2),
                suite="core-12",
                api_url="http://api.test",
                api_key="k",
                client=client,
            )
        assert result.accepted == 2

    def test_pushing_nothing_is_an_error(self):
        with pytest.raises(PushError, match="nothing to push"):
            push_runs([], suite="core-12", api_url="http://api.test", api_key="k")


def test_chunk_estimate():
    assert estimated_chunks(0) == 1
    assert estimated_chunks(12, 12) == 1
    assert estimated_chunks(13, 12) == 2
    assert estimated_chunks(144, 12) == 12
