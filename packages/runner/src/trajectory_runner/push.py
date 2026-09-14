"""Uploading a results bundle to the API.

The runner and the results service are deliberately separate processes, usually on
separate machines, so this is the seam between them. Three properties matter here.

Ingest is idempotent on run identifier. Re-pushing a bundle after a network failure has to
be safe, or nobody will retry and results will be lost instead.

The bundle carries a content hash over every run in identifier order, and the API
recomputes it. A truncated upload is rejected rather than half stored, which is the failure
that would otherwise show up weeks later as a suite that mysteriously has eleven tasks.

Large bundles are chunked. A hundred and forty four runs with full trajectories is tens of
megabytes, and a single request that size will be refused by something in the path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from trajectory_core.models import ResultsBundle, Run
from trajectory_runner.store import bundle_content_hash, bundle_from_runs

log = structlog.get_logger(__name__)

DEFAULT_CHUNK_RUNS = 12
DEFAULT_TIMEOUT_S = 120.0


class PushError(RuntimeError):
    """Raised when a bundle could not be uploaded."""


@dataclass(slots=True)
class PushResult:
    """What the API reported back."""

    accepted: int
    duplicates: int
    rejected: int
    chunks: int
    messages: list[str]

    @property
    def ok(self) -> bool:
        """True when nothing was rejected."""
        return self.rejected == 0


def _chunk(bundle: ResultsBundle, size: int) -> list[ResultsBundle]:
    """Split a bundle into smaller bundles, each with a correct manifest.

    Each chunk gets its own content hash over its own runs, so a chunk can be verified on
    arrival rather than only at the end.
    """
    if size <= 0 or len(bundle.runs) <= size:
        return [bundle]
    chunks: list[ResultsBundle] = []
    for start in range(0, len(bundle.runs), size):
        slice_runs = bundle.runs[start : start + size]
        part = bundle_from_runs(
            slice_runs,
            suite=bundle.manifest.suite,
            fingerprint=bundle.manifest.fingerprint,
            notes=bundle.manifest.notes,
        )
        chunks.append(part)
    return chunks


def push_bundle(
    bundle: ResultsBundle,
    *,
    api_url: str,
    api_key: str,
    chunk_runs: int = DEFAULT_CHUNK_RUNS,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    client: httpx.Client | None = None,
) -> PushResult:
    """Upload a bundle, in chunks, with the content hash verified by the server.

    Args:
        bundle: The bundle to upload.
        api_url: Base URL of the results API.
        api_key: Bearer token for the write route.
        chunk_runs: Runs per request.
        timeout_s: Per request timeout.
        client: Reuse an existing HTTP client, which the tests do.

    Returns:
        Counts of accepted, duplicate and rejected runs.

    Raises:
        PushError: On a non-2xx response that is not a duplicate.
    """
    expected = bundle_content_hash(bundle.runs)
    if bundle.manifest.content_sha256 and bundle.manifest.content_sha256 != expected:
        raise PushError(
            "the bundle manifest's content hash does not match its runs. Rebuild it rather "
            "than uploading a record that cannot be verified."
        )

    chunks = _chunk(bundle, chunk_runs)
    owned = client is None
    http = client or httpx.Client(timeout=timeout_s)
    accepted = duplicates = rejected = 0
    messages: list[str] = []

    try:
        for index, chunk in enumerate(chunks, start=1):
            log.info(
                "push.chunk",
                chunk=f"{index}/{len(chunks)}",
                runs=len(chunk.runs),
                suite=chunk.manifest.suite,
            )
            response = http.post(
                f"{api_url.rstrip('/')}/v1/runs",
                content=chunk.model_dump_json(),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            if response.status_code >= 400:
                raise PushError(
                    f"chunk {index} of {len(chunks)} was refused with "
                    f"{response.status_code}: {response.text[:500]}"
                )
            payload: dict[str, Any] = response.json()
            accepted += int(payload.get("accepted", 0))
            duplicates += int(payload.get("duplicates", 0))
            rejected += int(payload.get("rejected", 0))
            if payload.get("message"):
                messages.append(str(payload["message"]))
    except httpx.HTTPError as exc:
        raise PushError(f"uploading to {api_url} failed: {exc}") from exc
    finally:
        if owned:
            http.close()

    result = PushResult(
        accepted=accepted,
        duplicates=duplicates,
        rejected=rejected,
        chunks=len(chunks),
        messages=messages,
    )
    log.info(
        "push.done",
        accepted=result.accepted,
        duplicates=result.duplicates,
        rejected=result.rejected,
    )
    return result


def push_runs(
    runs: list[Run],
    *,
    suite: str,
    api_url: str,
    api_key: str,
    notes: str = "",
    chunk_runs: int = DEFAULT_CHUNK_RUNS,
    client: httpx.Client | None = None,
) -> PushResult:
    """Assemble a bundle from runs and upload it."""
    if not runs:
        raise PushError("there is nothing to push")
    bundle = bundle_from_runs(
        runs, suite=suite, fingerprint=runs[0].runner_fingerprint, notes=notes
    )
    return push_bundle(
        bundle, api_url=api_url, api_key=api_key, chunk_runs=chunk_runs, client=client
    )


def estimated_chunks(run_count: int, chunk_runs: int = DEFAULT_CHUNK_RUNS) -> int:
    """How many requests a push of this size will take."""
    return max(1, math.ceil(run_count / max(1, chunk_runs)))
