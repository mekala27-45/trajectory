"""On disk run storage.

A run directory looks like this:

    runs/2026-09-14T00-56-12-core-12/
        bundle.json              manifest plus every sealed run, written at the end
        runs/<run-id>.json       one sealed run record
        partial/<run-id>.jsonl   steps as they happen, deleted when the run seals

The `partial/` file is the reason this module exists. Steps are appended and flushed as
they complete, so a process that dies at step 38 leaves 38 steps on disk instead of
nothing. A leftover `.jsonl` after a suite finishes is a signal, not litter: it means that
run crashed, and `trajectory score` will tell you so rather than quietly reporting a
smaller suite.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

import structlog

from trajectory_core.models import (
    HARNESS_VERSION,
    BundleManifest,
    ResultsBundle,
    Run,
    RunnerFingerprint,
    Step,
)

log = structlog.get_logger(__name__)

BUNDLE_FILE = "bundle.json"
RUNS_DIR = "runs"
PARTIAL_DIR = "partial"


def bundle_content_hash(runs: list[Run]) -> str:
    """Hash the canonical JSON of every run, in id order.

    The API recomputes this on ingest, so a truncated or tampered upload is rejected
    rather than half stored.

    Args:
        runs: Runs to hash.

    Returns:
        A hex SHA-256.
    """
    digest = hashlib.sha256()
    for run in sorted(runs, key=lambda r: r.id):
        digest.update(run.model_dump_json(exclude_none=False).encode())
    return digest.hexdigest()


def new_run_directory(root: Path, suite: str, *, label: str | None = None) -> Path:
    """Create a timestamped run directory.

    Args:
        root: Parent directory, usually `runs/`.
        suite: Suite being run, used in the directory name.
        label: Optional name used instead of a timestamp.

    Returns:
        The created directory.
    """
    if label:
        name = label
    else:
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%S")
        name = f"{stamp}-{suite}"
    directory = root / name
    (directory / RUNS_DIR).mkdir(parents=True, exist_ok=True)
    (directory / PARTIAL_DIR).mkdir(parents=True, exist_ok=True)
    return directory


class RunWriter:
    """Appends steps to disk as they happen, then seals the run record.

    Used as a context manager so the partial file survives an exception and is cleaned up
    on success.
    """

    def __init__(self, directory: Path, run_id: str) -> None:
        """Open the partial trajectory file for a run."""
        self.directory = directory
        self.run_id = run_id
        self.partial_path = directory / PARTIAL_DIR / f"{run_id}.jsonl"
        self.partial_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.partial_path.open("w", encoding="utf-8")

    def __enter__(self) -> Self:
        """Return the writer."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the handle, leaving the partial file in place on failure."""
        self.close()

    def append(self, step: Step) -> None:
        """Write one step and flush it to the operating system immediately."""
        self._handle.write(step.model_dump_json() + "\n")
        self._handle.flush()

    def close(self) -> None:
        """Close the partial file without deleting it."""
        if not self._handle.closed:
            self._handle.close()

    def seal(self, run: Run) -> Path:
        """Write the sealed run record and remove the partial file.

        Args:
            run: The completed run.

        Returns:
            Path of the sealed record.
        """
        self.close()
        target = self.directory / RUNS_DIR / f"{run.id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(run.model_dump_json(indent=2), encoding="utf-8")
        self.partial_path.unlink(missing_ok=True)
        log.debug("run.sealed", run_id=run.id, path=str(target))
        return target


def read_run(path: Path) -> Run:
    """Read one sealed run record."""
    return Run.model_validate_json(path.read_text(encoding="utf-8"))


def read_runs(directory: Path) -> list[Run]:
    """Read every sealed run in a run directory, sorted by identifier.

    Args:
        directory: A run directory, or the `runs/` subdirectory of one.

    Returns:
        The runs, in creation order because identifiers are UUIDv7.
    """
    runs_dir = directory / RUNS_DIR if (directory / RUNS_DIR).is_dir() else directory
    return sorted(
        (read_run(path) for path in runs_dir.glob("*.json")),
        key=lambda run: run.id,
    )


def read_partial_steps(path: Path) -> list[Step]:
    """Read the steps of a crashed run from its partial file.

    A trailing partial line is discarded, because a process killed mid-write leaves one.
    """
    steps: list[Step] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            steps.append(Step.model_validate_json(stripped))
        except ValueError:
            log.warning("run.partial_line_discarded", path=str(path))
    return steps


def find_crashed_runs(directory: Path) -> list[Path]:
    """Return the partial files left behind by runs that never sealed."""
    partial = directory / PARTIAL_DIR
    return sorted(partial.glob("*.jsonl")) if partial.is_dir() else []


def write_bundle(
    directory: Path,
    runs: list[Run],
    *,
    suite: str,
    fingerprint: RunnerFingerprint,
    notes: str = "",
) -> Path:
    """Write the results bundle for a run directory.

    Args:
        directory: The run directory.
        runs: Sealed runs to include.
        suite: Suite the runs belong to.
        fingerprint: Machine that produced them.
        notes: Free text, conventionally the command line used.

    Returns:
        Path of the written bundle.
    """
    manifest = BundleManifest(
        suite=suite,
        harness_version=HARNESS_VERSION,
        run_count=len(runs),
        models=sorted({run.config.model for run in runs}),
        content_sha256=bundle_content_hash(runs),
        fingerprint=fingerprint,
        notes=notes,
    )
    bundle = ResultsBundle(manifest=manifest, runs=sorted(runs, key=lambda r: r.id))
    target = directory / BUNDLE_FILE
    target.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    log.info("bundle.written", path=str(target), runs=len(runs))
    return target


def read_bundle(path: Path) -> ResultsBundle:
    """Read a results bundle, from a file or a run directory."""
    target = path / BUNDLE_FILE if path.is_dir() else path
    return ResultsBundle.model_validate_json(target.read_text(encoding="utf-8"))


def bundle_from_runs(
    runs: list[Run], *, suite: str, fingerprint: RunnerFingerprint, notes: str = ""
) -> ResultsBundle:
    """Assemble a bundle in memory without writing it."""
    return ResultsBundle(
        manifest=BundleManifest(
            suite=suite,
            harness_version=HARNESS_VERSION,
            run_count=len(runs),
            models=sorted({run.config.model for run in runs}),
            content_sha256=bundle_content_hash(runs),
            fingerprint=fingerprint,
            notes=notes,
        ),
        runs=sorted(runs, key=lambda r: r.id),
    )


def json_dumps(payload: object) -> str:
    """Serialise with stable key order, used for anything that gets hashed or diffed."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))
