"""Run storage exists so a crashed run leaves something behind."""

from __future__ import annotations

from pathlib import Path

from trajectory_core.testing import bash_steps, finish_step, make_run, make_step, make_verification
from trajectory_runner.store import (
    RunWriter,
    bundle_content_hash,
    find_crashed_runs,
    new_run_directory,
    read_bundle,
    read_partial_steps,
    read_run,
    read_runs,
    write_bundle,
)


def test_new_run_directory_is_timestamped_and_has_both_subdirectories(tmp_path: Path):
    directory = new_run_directory(tmp_path, "core-12")
    assert (directory / "runs").is_dir()
    assert (directory / "partial").is_dir()
    assert "core-12" in directory.name


def test_a_label_replaces_the_timestamp(tmp_path: Path):
    directory = new_run_directory(tmp_path, "core-12", label="nightly")
    assert directory.name == "nightly"


class TestRunWriter:
    def test_steps_are_flushed_as_they_arrive(self, tmp_path: Path):
        """The whole reason this module exists."""
        run = make_run([], verification=make_verification())
        writer = RunWriter(tmp_path, run.id)
        for index in range(3):
            writer.append(make_step(index, command=f"cmd {index}"))
            # Read the file from a separate handle while the writer is still open.
            assert writer.partial_path.read_text().count("\n") == index + 1
        writer.close()

    def test_sealing_writes_the_record_and_removes_the_partial(self, tmp_path: Path):
        run = make_run([*bash_steps("ls"), finish_step(1)], verification=make_verification())
        with RunWriter(tmp_path, run.id) as writer:
            for step in run.steps:
                writer.append(step)
            path = writer.seal(run)
        assert path.is_file()
        assert not writer.partial_path.exists()
        assert read_run(path).id == run.id

    def test_a_partial_file_survives_a_crash(self, tmp_path: Path):
        """A run that dies at step 38 has to leave 38 steps on disk."""
        run = make_run([], verification=None)
        writer = RunWriter(tmp_path, run.id)
        try:
            for index in range(38):
                writer.append(make_step(index, command=f"cmd {index}"))
            raise RuntimeError("the process died here")
        except RuntimeError:
            writer.close()

        crashed = find_crashed_runs(tmp_path)
        assert len(crashed) == 1
        assert len(read_partial_steps(crashed[0])) == 38

    def test_a_truncated_final_line_is_discarded_rather_than_fatal(self, tmp_path: Path):
        run = make_run([], verification=None)
        writer = RunWriter(tmp_path, run.id)
        writer.append(make_step(0, command="ls"))
        writer.close()
        with writer.partial_path.open("a") as handle:
            handle.write('{"index": 1, "tool_nam')
        assert len(read_partial_steps(writer.partial_path)) == 1


class TestBundles:
    def test_content_hash_is_order_independent(self):
        runs = [
            make_run([*bash_steps("a")], verification=make_verification(), task_id="t1"),
            make_run([*bash_steps("b")], verification=make_verification(), task_id="t2"),
        ]
        assert bundle_content_hash(runs) == bundle_content_hash(list(reversed(runs)))

    def test_content_hash_changes_when_a_run_changes(self):
        runs = [make_run([*bash_steps("a")], verification=make_verification())]
        before = bundle_content_hash(runs)
        runs[0].steps[0].tool_output = "different"
        assert bundle_content_hash(runs) != before

    def test_write_and_read_round_trip(self, tmp_path: Path):
        directory = new_run_directory(tmp_path, "core-12", label="x")
        runs = [
            make_run([*bash_steps("a"), finish_step(1)], verification=make_verification()),
            make_run([*bash_steps("b"), finish_step(1)], verification=make_verification()),
        ]
        for run in runs:
            with RunWriter(directory, run.id) as writer:
                writer.seal(run)
        write_bundle(
            directory, runs, suite="core-12", fingerprint=runs[0].runner_fingerprint, notes="n"
        )
        bundle = read_bundle(directory)
        assert bundle.manifest.run_count == 2
        assert bundle.manifest.content_sha256 == bundle_content_hash(runs)
        assert bundle.manifest.notes == "n"
        assert [r.id for r in bundle.runs] == sorted(r.id for r in runs)

    def test_read_runs_sorts_by_identifier(self, tmp_path: Path):
        directory = new_run_directory(tmp_path, "core-12", label="y")
        runs = [make_run([], verification=make_verification()) for _ in range(5)]
        for run in runs:
            with RunWriter(directory, run.id) as writer:
                writer.seal(run)
        loaded = read_runs(directory)
        assert [r.id for r in loaded] == sorted(r.id for r in runs)

    def test_no_crashed_runs_in_a_directory_that_never_had_any(self, tmp_path: Path):
        assert find_crashed_runs(tmp_path) == []
