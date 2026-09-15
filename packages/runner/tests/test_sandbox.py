"""The sandbox holds the one property that makes the benchmark mean anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from trajectory_core.models import SandboxBackend
from trajectory_core.testing import make_task
from trajectory_runner.sandbox import (
    TIMEOUT_EXIT_CODE,
    TMPFS_MOUNTS,
    DockerSandbox,
    LocalSandbox,
    Sandbox,
    SandboxError,
    _normalise_path,
    _parse_manifest,
    _parse_wrapper_output,
    build_sandbox,
    directory_content_hash,
    local_verify_runnable,
    sanitised_path,
)


class TestWrapperParsing:
    """The framing has to survive output that looks like the framing."""

    def _frame(self, rc: int, out: bytes, err: bytes, cap: int) -> bytes:
        header = (
            f"___TRAJ_RC___{rc}\n___TRAJ_OL___{len(out)}\n___TRAJ_EL___{len(err)}\n___TRAJ_OUT___\n"
        ).encode()
        return header + out[:cap] + b"\n___TRAJ_ERR___\n" + err[:cap]

    def test_parses_a_normal_result(self):
        raw = self._frame(0, b"hello\n", b"", 1024)
        rc, out, err, truncated = _parse_wrapper_output(raw, 1024)
        assert (rc, out, err, truncated) == (0, "hello\n", "", False)

    def test_parses_both_streams(self):
        raw = self._frame(2, b"out", b"boom", 1024)
        rc, out, err, _ = _parse_wrapper_output(raw, 1024)
        assert (rc, out, err) == (2, "out", "boom")

    def test_flags_truncation_when_a_stream_exceeded_the_cap(self):
        raw = self._frame(0, b"x" * 5000, b"", 100)
        _, out, _, truncated = _parse_wrapper_output(raw, 100)
        assert truncated is True
        assert len(out) == 100

    def test_output_containing_the_stderr_marker_does_not_confuse_the_parser(self):
        """Slicing by the declared byte length is what makes this safe."""
        payload = b"before\n___TRAJ_ERR___\nafter"
        raw = self._frame(0, payload, b"real stderr", 4096)
        _, out, err, _ = _parse_wrapper_output(raw, 4096)
        assert out == payload.decode()
        assert err == "real stderr"

    def test_rejects_unframed_output(self):
        with pytest.raises(SandboxError, match="unframed output"):
            _parse_wrapper_output(b"bash: command not found", 1024)

    def test_survives_invalid_utf8(self):
        raw = self._frame(0, b"\xff\xfe bad bytes", b"", 1024)
        _, out, _, _ = _parse_wrapper_output(raw, 1024)
        assert "bad bytes" in out


class TestPathNormalisation:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("src/app.py", "src/app.py"),
            ("./src/app.py", "src/app.py"),
            ("/workspace/src/app.py", "src/app.py"),
            ("/workspace", "."),
            ("a/b/../c.py", "a/c.py"),
            ("  src/app.py  ", "src/app.py"),
        ],
    )
    def test_accepts_and_normalises(self, given, expected):
        assert _normalise_path(given) == expected

    @pytest.mark.parametrize(
        "given", ["/etc/passwd", "../secrets", "a/../../b", "/verify/test_x.py"]
    )
    def test_refuses_anything_that_escapes(self, given):
        with pytest.raises(ValueError, match=r"escapes the workspace|absolute paths"):
            _normalise_path(given)

    def test_refuses_an_empty_path(self):
        with pytest.raises(ValueError, match="empty"):
            _normalise_path("   ")


class TestManifestParsing:
    def test_reads_sha256sum_output(self):
        text = "abc123def4567890  ./src/app.py\nfeed0000beef1111  ./README.md\n"
        manifest = _parse_manifest(text, total_files=2)
        assert manifest.files == {"src/app.py": "abc123def4567890", "README.md": "feed0000beef1111"}
        assert manifest.truncated is False

    def test_flags_truncation_past_the_file_limit(self):
        manifest = _parse_manifest("aaaa  ./x\n", total_files=99_999)
        assert manifest.truncated is True

    def test_ignores_malformed_lines(self):
        assert _parse_manifest("garbage\n", total_files=0).files == {}

    def test_change_detection(self):
        before = _parse_manifest("1111  ./a\n2222  ./b\n", total_files=2)
        after = _parse_manifest("1111  ./a\n3333  ./b\n4444  ./c\n", total_files=3)
        assert after.changed_against(before) == {"b", "c"}

    def test_change_detection_notices_deletions(self):
        before = _parse_manifest("1111  ./a\n2222  ./b\n", total_files=2)
        after = _parse_manifest("1111  ./a\n", total_files=1)
        assert after.changed_against(before) == {"b"}


class TestImageCacheKey:
    def test_same_contents_hash_the_same(self, tmp_path: Path):
        for name in ("one", "two"):
            root = tmp_path / name
            (root / "workspace").mkdir(parents=True)
            (root / "Dockerfile").write_text("FROM python:3.12-slim\n")
            (root / "workspace" / "a.py").write_text("x = 1\n")
        left = directory_content_hash(
            [tmp_path / "one" / "Dockerfile", tmp_path / "one" / "workspace"]
        )
        right = directory_content_hash(
            [tmp_path / "two" / "Dockerfile", tmp_path / "two" / "workspace"]
        )
        assert left == right

    def test_a_workspace_change_changes_the_hash(self, tmp_path: Path):
        root = tmp_path / "t"
        (root / "workspace").mkdir(parents=True)
        (root / "Dockerfile").write_text("FROM python:3.12-slim\n")
        (root / "workspace" / "a.py").write_text("x = 1\n")
        before = directory_content_hash([root / "Dockerfile", root / "workspace"])
        (root / "workspace" / "a.py").write_text("x = 2\n")
        assert directory_content_hash([root / "Dockerfile", root / "workspace"]) != before


class TestSanitisedPath:
    """The PATH a local-backend task sees, which had no tests until it caused an outage.

    A task's commands must not resolve the harness's own interpreter. If they do, the task
    sees every package this repository installs, passes locally against packages the task
    image never had, and fails anywhere the host differs. That is not a hypothetical: the
    first CI run of this repository failed five tests on exactly this.
    """

    def test_removes_the_active_virtualenv(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/work/.venv")
        monkeypatch.setenv("PATH", "/work/.venv/bin:/usr/local/bin:/usr/bin:/bin")
        assert sanitised_path() == "/usr/local/bin:/usr/bin:/bin"

    def test_removes_the_virtualenv_directory_itself(self, monkeypatch):
        monkeypatch.setenv("VIRTUAL_ENV", "/work/.venv")
        monkeypatch.setenv("PATH", "/work/.venv:/usr/bin")
        assert sanitised_path() == "/usr/bin"

    def test_keeps_a_directory_that_merely_shares_a_prefix(self, monkeypatch):
        # `/work/.venv-other` starts with the same characters as `/work/.venv` and is a
        # different directory. A substring check would delete it.
        monkeypatch.setenv("VIRTUAL_ENV", "/work/.venv")
        monkeypatch.setenv("PATH", "/work/.venv-other/bin:/work/.venv/bin:/usr/bin")
        assert sanitised_path() == "/work/.venv-other/bin:/usr/bin"

    def test_keeps_the_system_path_when_no_virtualenv_is_active(self, monkeypatch):
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin:/bin")
        assert sanitised_path() == "/usr/local/bin:/usr/bin:/bin"

    def test_falls_back_rather_than_returning_an_empty_path(self, monkeypatch):
        # Stripping the venv can empty PATH entirely. An empty PATH makes every command
        # fail with "not found", which reads as a broken task rather than a broken PATH.
        monkeypatch.setenv("VIRTUAL_ENV", "/work/.venv")
        monkeypatch.setenv("PATH", "/work/.venv/bin")
        assert sanitised_path() == "/usr/local/bin:/usr/bin:/bin"

    def test_drops_empty_entries(self, monkeypatch):
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.setenv("PATH", "/usr/bin::/bin:")
        assert sanitised_path() == "/usr/bin:/bin"

    def test_a_task_cannot_reach_the_harness_interpreter(
        self, sample_task, task_dir, allow_local, monkeypatch
    ):
        # The regression itself, stated as behaviour rather than as a string comparison:
        # the environment handed to a task must not contain the harness virtualenv.
        with LocalSandbox(sample_task, task_dir) as sandbox:
            monkeypatch.setenv("VIRTUAL_ENV", "/work/.venv")
            monkeypatch.setenv("PATH", "/work/.venv/bin:/usr/bin:/bin")
            assert sandbox._env()["PATH"] == "/usr/bin:/bin"


class TestLocalVerifyRunnable:
    """The probe the `local_verify` skip marker and the CI gate both rely on."""

    def test_false_when_python_cannot_import_pytest(self, monkeypatch, tmp_path):
        # A directory holding a `python` that exits non-zero, standing in for an
        # interpreter without pytest installed.
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "python"
        stub.write_text("#!/bin/sh\nexit 1\n")
        stub.chmod(0o755)
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.setenv("PATH", str(stub_dir))
        assert local_verify_runnable() is False

    def test_false_when_there_is_no_python_at_all(self, monkeypatch, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.setenv("PATH", str(empty))
        assert local_verify_runnable() is False

    def test_true_when_python_runs_pytest(self, monkeypatch, tmp_path):
        stub_dir = tmp_path / "bin"
        stub_dir.mkdir()
        stub = stub_dir / "python"
        stub.write_text("#!/bin/sh\necho 'pytest 8.3.4'\nexit 0\n")
        stub.chmod(0o755)
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.setenv("PATH", str(stub_dir))
        assert local_verify_runnable() is True


class TestLocalSandboxOptIn:
    def test_refuses_to_start_without_an_explicit_opt_in(self, sample_task, task_dir, monkeypatch):
        monkeypatch.delenv("TRAJECTORY_ALLOW_LOCAL_SANDBOX", raising=False)
        with pytest.raises(SandboxError, match="not isolated and is off by default"):
            LocalSandbox(sample_task, task_dir)

    def test_starts_once_the_operator_opts_in(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            assert sandbox.backend is SandboxBackend.LOCAL

    def test_factory_selects_the_backend(self, sample_task, task_dir):
        docker_sandbox = build_sandbox(sample_task, task_dir, SandboxBackend.DOCKER)
        local_sandbox = build_sandbox(
            sample_task, task_dir, SandboxBackend.LOCAL, allow_local_override=True
        )
        assert isinstance(docker_sandbox, DockerSandbox)
        assert isinstance(local_sandbox, LocalSandbox)
        assert isinstance(local_sandbox, Sandbox)


class TestLocalSandboxBehaviour:
    def test_hidden_tests_are_absent_from_the_sandbox_root(
        self, sample_task, task_dir, allow_local
    ):
        """What the local backend can promise: nothing under the sandbox root.

        It cannot promise more, because the host filesystem is visible to a shell command.
        The equivalent Docker test below asserts the real property, that the hidden tests
        do not exist anywhere in the filesystem the agent can see.
        """
        with LocalSandbox(sample_task, task_dir) as sandbox:
            assert sandbox.verify_present() is False
            assert sandbox.exec("ls ../verify", timeout_s=10, cap_bytes=4096).exit_code != 0
            found = sandbox.exec(
                f"find {sandbox.root} -name 'test_app.py' 2>/dev/null",
                timeout_s=30,
                cap_bytes=65536,
            )
            assert found.stdout.strip() == ""

    def test_hidden_tests_appear_only_after_the_agent_phase(
        self, sample_task, task_dir, allow_local
    ):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            sandbox.install_verify()
            assert sandbox.verify_present() is True
            result = sandbox.exec(sample_task.verify_cmd, timeout_s=90, cap_bytes=16384)
            assert result.exit_code != 0  # the workspace is still broken

    def test_installing_verify_twice_is_harmless(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            sandbox.install_verify()
            sandbox.install_verify()
            assert sandbox.verify_present() is True

    @pytest.mark.local_verify
    def test_a_fixed_workspace_passes_the_hidden_tests(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            sandbox.write_file("src/app.py", "def add(a, b):\n    return a + b\n")
            sandbox.install_verify()
            result = sandbox.exec(sample_task.verify_cmd, timeout_s=90, cap_bytes=16384)
            assert result.exit_code == 0
            assert "2 passed" in result.combined

    def test_command_timeout_is_reported_not_raised(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            result = sandbox.exec("sleep 30", timeout_s=1, cap_bytes=1024)
            assert result.exit_code == TIMEOUT_EXIT_CODE
            assert result.timed_out is True

    def test_output_is_capped_and_flagged(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            result = sandbox.exec(
                "head -c 50000 /dev/zero | tr '\\0' 'a'", timeout_s=20, cap_bytes=256
            )
            assert result.truncated is True
            assert len(result.stdout) == 256

    def test_nonzero_exit_codes_are_preserved(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            assert sandbox.exec("exit 42", timeout_s=10, cap_bytes=1024).exit_code == 42

    def test_stderr_is_captured_separately(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            result = sandbox.exec("echo out; echo err >&2", timeout_s=10, cap_bytes=1024)
            assert result.stdout.strip() == "out"
            assert result.stderr.strip() == "err"
            assert result.combined.splitlines() == ["out", "err"]

    def test_file_tools_round_trip(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            sandbox.write_file("deeply/nested/new.txt", "hello\n")
            text, truncated = sandbox.read_file("deeply/nested/new.txt", max_lines=10)
            assert text == "hello"
            assert truncated is False
            assert "new.txt" in sandbox.list_dir("deeply/nested")

    def test_read_file_truncates_and_says_so(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            sandbox.write_file("big.txt", "\n".join(str(i) for i in range(500)))
            text, truncated = sandbox.read_file("big.txt", max_lines=10)
            assert truncated is True
            assert len(text.splitlines()) == 10

    def test_missing_files_raise_a_recognisable_error(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            with pytest.raises(FileNotFoundError):
                sandbox.read_file("nope/not-here.py", max_lines=10)
            with pytest.raises(FileNotFoundError):
                sandbox.list_dir("nope")

    def test_file_tools_cannot_escape_the_workspace(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            sandbox.install_verify()
            with pytest.raises(ValueError, match="escapes the workspace"):
                sandbox.read_file("../verify/test_app.py", max_lines=10)

    def test_manifest_tracks_changes(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            before = sandbox.manifest()
            assert "src/app.py" in before.files
            sandbox.write_file("src/app.py", "def add(a, b):\n    return a + b\n")
            sandbox.write_file("scratch.txt", "notes")
            after = sandbox.manifest()
            assert after.changed_against(before) == {"src/app.py", "scratch.txt"}

    def test_host_path_guard_refuses_obvious_accidents(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            for command in [
                "rm -rf /etc",
                "rm -rf /",
                "chmod -R 777 /usr/bin",
                "mv important /var/tmp",
                "rm -rf ~",
            ]:
                result = sandbox.exec(command, timeout_s=5, cap_bytes=2048)
                assert result.exit_code == 126, command
                assert "refused by the local sandbox" in result.stderr

    def test_host_path_guard_allows_contained_destructive_commands(
        self, sample_task, task_dir, allow_local
    ):
        """The reckless policy has to be able to run, or it cannot be measured."""
        with LocalSandbox(sample_task, task_dir) as sandbox:
            for command in ["rm -rf ../.cache", "chmod -R 777 .", "git reset --hard"]:
                assert sandbox.exec(command, timeout_s=10, cap_bytes=2048).exit_code != 126

    def test_home_is_redirected_into_the_sandbox(self, sample_task, task_dir, allow_local):
        with LocalSandbox(sample_task, task_dir) as sandbox:
            home = sandbox.exec("echo $HOME", timeout_s=10, cap_bytes=1024).stdout.strip()
            assert home == str(sandbox.root)

    def test_cleanup_happens_when_the_body_raises(self, sample_task, task_dir, allow_local):
        """The test that matters: an exception must not leave the sandbox behind."""
        captured: list[Path] = []
        with (
            pytest.raises(RuntimeError, match="boom"),
            LocalSandbox(sample_task, task_dir) as sandbox,
        ):
            captured.append(sandbox.root)
            raise RuntimeError("boom")
        assert captured[0].exists() is False

    def test_close_is_idempotent(self, sample_task, task_dir, allow_local):
        sandbox = LocalSandbox(sample_task, task_dir)
        sandbox.start()
        sandbox.close()
        sandbox.close()

    def test_operations_after_close_fail_clearly(self, sample_task, task_dir, allow_local):
        sandbox = LocalSandbox(sample_task, task_dir)
        sandbox.start()
        sandbox.close()
        with pytest.raises(SandboxError, match="not running"):
            sandbox.exec("true", timeout_s=5, cap_bytes=1024)

    def test_setup_command_runs_before_the_agent(self, task_dir, allow_local):
        task = make_task(
            id="sample-task-01",
            image_tag="trajectory/sample-task-01",
            setup_cmd="echo prepared > .setup-marker",
            verify_cmd='python -m pytest -q "$VERIFY_DIR"',
        )
        with LocalSandbox(task, task_dir) as sandbox:
            text, _ = sandbox.read_file(".setup-marker", max_lines=5)
            assert text.strip() == "prepared"

    def test_a_failing_setup_command_fails_the_run(self, task_dir, allow_local):
        task = make_task(
            id="sample-task-01",
            image_tag="trajectory/sample-task-01",
            setup_cmd="exit 3",
            verify_cmd='python -m pytest -q "$VERIFY_DIR"',
        )
        with (
            pytest.raises(SandboxError, match=r"setup_cmd .* exited 3"),
            LocalSandbox(task, task_dir),
        ):
            pass

    def test_a_missing_workspace_is_reported_clearly(self, sample_task, tmp_path, allow_local):
        with (
            pytest.raises(SandboxError, match="no workspace/"),
            LocalSandbox(sample_task, tmp_path),
        ):
            pass

    def test_a_missing_verify_directory_is_reported_clearly(
        self, sample_task, tmp_path, allow_local
    ):
        (tmp_path / "workspace").mkdir()
        with (
            LocalSandbox(sample_task, tmp_path) as sandbox,
            pytest.raises(SandboxError, match="no verify/"),
        ):
            sandbox.install_verify()


class TestTheTmpfsMount:
    """The mount options are a correctness property, so they are asserted without Docker.

    `noexec` on /tmp broke go-race-01 completely and reported it as `0/1`, which is the
    same thing the harness prints when a task's every test fails. `go test` links the test
    binary into $GOTMPDIR, defaulting to /tmp, and then executes it.
    """

    def test_tmp_is_a_size_capped_tmpfs(self):
        assert "/tmp" in TMPFS_MOUNTS
        assert "size=" in TMPFS_MOUNTS["/tmp"]

    def test_tmp_is_not_noexec(self):
        # Removing this flag is deliberate. See the comment on TMPFS_MOUNTS: it is not a
        # security boundary, because /workspace is writable and exec capable by design,
        # and it breaks every toolchain that stages an executable in TMPDIR.
        assert "noexec" not in TMPFS_MOUNTS["/tmp"]

    def test_nosuid_is_kept(self):
        assert "nosuid" in TMPFS_MOUNTS["/tmp"]


@pytest.mark.docker
@pytest.mark.slow
class TestDockerSandbox:
    """Runs only where a Docker daemon exists. CI always has one."""

    def test_hidden_tests_are_absent_during_the_agent_phase(self, sample_task, task_dir):
        with DockerSandbox(sample_task, task_dir) as sandbox:
            assert sandbox.verify_present() is False
            found = sandbox.exec(
                "find / -name 'test_app.py' -not -path '/proc/*' 2>/dev/null",
                timeout_s=60,
                cap_bytes=65536,
            )
            assert found.stdout.strip() == ""

    def test_the_agent_is_not_root(self, sample_task, task_dir):
        with DockerSandbox(sample_task, task_dir) as sandbox:
            assert sandbox.exec("id -u", timeout_s=20, cap_bytes=1024).stdout.strip() != "0"
            assert sandbox.exec("whoami", timeout_s=20, cap_bytes=1024).stdout.strip() == "agent"

    def test_a_real_binary_copied_into_tmpdir_can_be_executed(self, sample_task, task_dir):
        """The property go-race-01 needed and did not have.

        Every compiled language in this suite links a binary into TMPDIR and then runs it,
        so this copies a real ELF binary and executes it rather than writing a shell
        script: a script would go through the interpreter and could pass on a mount that
        refuses to exec the file itself. Nothing here is Go specific, which is the point.
        The fixture image is the Python one and the property still has to hold.
        """
        with DockerSandbox(sample_task, task_dir) as sandbox:
            result = sandbox.exec(
                'set -e; d="${TMPDIR:-/tmp}"; cp /bin/sh "$d/traj_probe"; '
                '"$d/traj_probe" -c "echo ran from tmpdir"',
                timeout_s=30,
                cap_bytes=4096,
            )
        assert result.exit_code == 0, (
            "could not execute a binary from TMPDIR, which is what noexec on the tmpfs "
            f"did to go-race-01: {result.combined!r}"
        )
        assert "ran from tmpdir" in result.stdout

    def test_tmp_is_still_size_capped(self, sample_task, task_dir):
        """Dropping noexec must not have dropped the cap that stops a disk filling up.

        Parsed here rather than with awk, which is not guaranteed to be in a slim image
        and would turn a real regression into a confusing failure about a missing tool.
        """
        with DockerSandbox(sample_task, task_dir) as sandbox:
            result = sandbox.exec("df -m /tmp", timeout_s=30, cap_bytes=4096)
        assert result.exit_code == 0, result.combined
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        assert len(lines) >= 2, f"unexpected df output: {result.stdout!r}"
        total_mb = int(lines[-1].split()[1])
        assert total_mb <= 256, f"/tmp is {total_mb} MB, so the size cap is gone"

    def test_the_network_is_off_by_default(self, sample_task, task_dir):
        with DockerSandbox(sample_task, task_dir) as sandbox:
            result = sandbox.exec(
                "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"",
                timeout_s=30,
                cap_bytes=8192,
            )
            assert result.exit_code != 0

    def test_memory_and_pid_ceilings_are_applied(self, sample_task, task_dir):
        with DockerSandbox(sample_task, task_dir) as sandbox:
            attrs = sandbox._require().attrs["HostConfig"]
            assert attrs["Memory"] == sample_task.memory_mb * 1024 * 1024
            assert attrs["PidsLimit"] == sample_task.pids_limit
            assert attrs["NanoCpus"] == int(sample_task.cpus * 1_000_000_000)

    def test_full_cycle_solves_the_task(self, sample_task, task_dir):
        with DockerSandbox(sample_task, task_dir) as sandbox:
            before = sandbox.manifest()
            sandbox.write_file("src/app.py", "def add(a, b):\n    return a + b\n")
            after = sandbox.manifest()
            assert "src/app.py" in after.changed_against(before)
            sandbox.install_verify()
            assert sandbox.verify_present() is True
            result = sandbox.exec(sample_task.verify_cmd, timeout_s=180, cap_bytes=16384)
            assert result.exit_code == 0
            assert "2 passed" in result.combined

    def test_the_container_is_removed_when_the_body_raises(self, sample_task, task_dir):
        import docker

        client = docker.from_env()
        container_id = None
        with (
            pytest.raises(RuntimeError, match="boom"),
            DockerSandbox(sample_task, task_dir) as sandbox,
        ):
            container_id = sandbox._require().id
            raise RuntimeError("boom")
        assert container_id is not None
        with pytest.raises(docker.errors.NotFound):
            client.containers.get(container_id)

    def test_command_timeout_is_reported(self, sample_task, task_dir):
        with DockerSandbox(sample_task, task_dir) as sandbox:
            result = sandbox.exec("sleep 30", timeout_s=2, cap_bytes=1024)
            assert result.timed_out is True

    def test_image_build_is_cached(self, sample_task, task_dir):
        from trajectory_runner.sandbox import build_task_image

        first = build_task_image(sample_task, task_dir)
        second = build_task_image(sample_task, task_dir)
        assert first == second
