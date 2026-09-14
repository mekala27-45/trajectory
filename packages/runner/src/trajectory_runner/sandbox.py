"""Sandboxed execution.

One container per run, built from the task's own Dockerfile, torn down whatever happens.

The property that matters most here is that the hidden tests are not present in the
filesystem while the agent is running. An agent that can read the verification tests will
read the verification tests, and the benchmark becomes worthless the first time that
happens quietly. `verify/` is copied in only after the agent phase has ended, and there is
a test that asserts it is absent before that.

Two backends implement the same protocol.

`DockerSandbox` is the production backend and the only one whose results belong on a
leaderboard. It enforces a non-root user, memory, CPU and PID ceilings, a disabled
network by default, dropped capabilities and a per command timeout.

`LocalSandbox` runs commands in a temporary directory on the host. It exists because task
authoring should not require a Docker daemon, and because CI for the pure-Python parts of
this project should not need one either. It is a convenience with a seatbelt, not a
security boundary: it refuses to start unless `TRAJECTORY_ALLOW_LOCAL_SANDBOX=1` is set,
it blocks commands that reach at obvious host paths, and every run it produces is stamped
`sandbox_backend=local` so it can never be silently mixed into published numbers.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

import structlog

from trajectory_core.models import SandboxBackend, Task, WorkspaceManifest

log = structlog.get_logger(__name__)

WORKSPACE_CONTAINER_PATH = "/workspace"
VERIFY_CONTAINER_PATH = "/verify"
AGENT_USER = "agent"
ALLOW_LOCAL_ENV = "TRAJECTORY_ALLOW_LOCAL_SANDBOX"
MANIFEST_FILE_LIMIT = 4000

EXCLUDED_DIRS = (
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "vendor",
    ".pytest_cache",
    ".mypy_cache",
)

_RC = b"___TRAJ_RC___"
_OL = b"___TRAJ_OL___"
_EL = b"___TRAJ_EL___"
_OUT = b"___TRAJ_OUT___\n"
_ERR = b"\n___TRAJ_ERR___\n"

TIMEOUT_EXIT_CODE = 124

# Runs the agent's command with a hard timeout, captures both streams to files, then
# emits a fixed header followed by exactly the capped bytes of each stream. Emitting the
# true lengths in the header means the parser slices by byte count rather than searching
# for a marker, so output that happens to contain a marker cannot confuse it.
_WRAPPER = r"""
set +e
__o=$(mktemp); __e=$(mktemp)
timeout -k 2 "$TRAJ_TIMEOUT" bash -c "$TRAJ_CMD" >"$__o" 2>"$__e"
__rc=$?
__ol=$(( $(wc -c <"$__o") )); __el=$(( $(wc -c <"$__e") ))
printf '___TRAJ_RC___%s\n___TRAJ_OL___%s\n___TRAJ_EL___%s\n___TRAJ_OUT___\n' "$__rc" "$__ol" "$__el"
head -c "$TRAJ_CAP" "$__o"
printf '\n___TRAJ_ERR___\n'
head -c "$TRAJ_CAP" "$__e"
rm -f "$__o" "$__e"
"""

_MANIFEST_CMD = (
    "find . -type f "
    + " ".join(f"-not -path './{d}/*'" for d in EXCLUDED_DIRS)
    + " -print0 | LC_ALL=C sort -z | head -z -n "
    + str(MANIFEST_FILE_LIMIT)
    + " | xargs -0 -r sha256sum"
)

_COUNT_CMD = (
    "find . -type f " + " ".join(f"-not -path './{d}/*'" for d in EXCLUDED_DIRS) + " | wc -l"
)


class SandboxError(RuntimeError):
    """Raised when a sandbox cannot be created or a sandbox operation fails."""


@dataclass(slots=True)
class ExecResult:
    """The result of one command."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool
    timed_out: bool

    @property
    def combined(self) -> str:
        """Stdout and stderr joined the way a terminal would have shown them.

        A separator newline is added only when stdout does not already end with one, so
        the joined text matches what an operator would have seen rather than gaining a
        blank line that was never there.
        """
        if not self.stdout:
            return self.stderr
        if not self.stderr:
            return self.stdout
        separator = "" if self.stdout.endswith("\n") else "\n"
        return f"{self.stdout}{separator}{self.stderr}"


@runtime_checkable
class Sandbox(Protocol):
    """What the agent loop and the verifier need from an execution environment."""

    backend: SandboxBackend

    def exec(self, command: str, *, timeout_s: int, cap_bytes: int) -> ExecResult:
        """Run a shell command in the workspace."""
        ...

    def read_file(self, path: str, *, max_lines: int) -> tuple[str, bool]:
        """Read a workspace file, returning the text and whether it was truncated."""
        ...

    def write_file(self, path: str, content: str) -> None:
        """Write a workspace file, creating parent directories."""
        ...

    def list_dir(self, path: str) -> list[str]:
        """List one level of a workspace directory."""
        ...

    def manifest(self) -> WorkspaceManifest:
        """Hash every file in the workspace."""
        ...

    def install_verify(self) -> None:
        """Copy the hidden tests in. Only ever called after the agent phase."""
        ...

    def close(self) -> None:
        """Tear the sandbox down. Must be safe to call more than once."""
        ...


# ----------------------------------------------------------------------- helpers


def _parse_wrapper_output(raw: bytes, cap: int) -> tuple[int, str, str, bool]:
    """Decode the wrapper's framed output.

    Args:
        raw: Bytes the wrapper wrote.
        cap: Byte cap that was applied to each stream.

    Returns:
        Exit code, stdout, stderr, and whether either stream was cut.

    Raises:
        SandboxError: If the framing is missing, which means the wrapper never ran.
    """
    head_end = raw.find(_OUT)
    if head_end < 0 or not raw.startswith(_RC):
        preview = raw[:400].decode("utf-8", "replace")
        raise SandboxError(f"sandbox wrapper produced unframed output: {preview!r}")

    header = raw[:head_end].decode("utf-8", "replace")
    values: dict[bytes, int] = {}
    for marker in (_RC, _OL, _EL):
        token = marker.decode()
        line = next((s for s in header.splitlines() if s.startswith(token)), None)
        if line is None:
            raise SandboxError(f"sandbox wrapper header missing {token}")
        values[marker] = int(line[len(token) :] or 0)

    body = raw[head_end + len(_OUT) :]
    out_len = min(values[_OL], cap)
    stdout = body[:out_len]
    rest = body[out_len:]
    if not rest.startswith(_ERR):
        # Defensive: fall back to a marker search rather than losing the output entirely.
        idx = rest.find(_ERR)
        if idx < 0:
            raise SandboxError("sandbox wrapper output missing the stderr marker")
        stdout += rest[:idx]
        rest = rest[idx:]
    stderr = rest[len(_ERR) :][: min(values[_EL], cap)]

    truncated = values[_OL] > cap or values[_EL] > cap
    return (
        values[_RC],
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
        truncated,
    )


def _normalise_path(path: str) -> str:
    """Turn an agent supplied path into a workspace relative path.

    Models routinely guess the absolute container path, so a leading `/workspace/` is
    accepted and stripped rather than rejected. Anything else absolute, and anything that
    climbs out of the workspace, is refused: allowing it would mean a tool could read the
    hidden tests through a relative path once they are installed.

    Args:
        path: Path as the model supplied it.

    Returns:
        A workspace relative path with no leading separator.

    Raises:
        ValueError: If the path escapes the workspace.
    """
    cleaned = path.strip()
    if not cleaned:
        raise ValueError("path is empty")
    if cleaned == WORKSPACE_CONTAINER_PATH or cleaned.startswith(WORKSPACE_CONTAINER_PATH + "/"):
        cleaned = cleaned[len(WORKSPACE_CONTAINER_PATH) :].lstrip("/")
    elif cleaned.startswith("/"):
        raise ValueError(
            f"absolute paths are not allowed, use a path relative to the workspace: {path}"
        )
    if not cleaned:
        return "."
    parts: list[str] = []
    for part in cleaned.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ValueError(f"path escapes the workspace: {path}")
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts) or "."


def _parse_manifest(text: str, *, total_files: int) -> WorkspaceManifest:
    """Turn `sha256sum` output into a workspace manifest."""
    files: dict[str, str] = {}
    for line in text.splitlines():
        if "  " not in line:
            continue
        digest, _, name = line.partition("  ")
        rel = name.strip()
        rel = rel[2:] if rel.startswith("./") else rel
        if rel:
            files[rel] = digest.strip()[:16]
    return WorkspaceManifest(files=files, truncated=total_files > MANIFEST_FILE_LIMIT)


def _tar_bytes(source: Path, arcname: str) -> bytes:
    """Pack a directory into an uncompressed tar archive in memory."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        tar.add(source, arcname=arcname)
    return buffer.getvalue()


def directory_content_hash(paths: list[Path]) -> str:
    """Hash a set of files and directories so image builds can be cached.

    `verify/` and `reference/` are deliberately not passed in by the caller: changing a
    hidden test should not invalidate a built image, because the hidden tests are never in
    the image to begin with.

    Args:
        paths: Files and directories to include.

    Returns:
        A hex SHA-256 over the sorted relative paths and their contents.
    """
    digest = hashlib.sha256()
    for root in sorted(paths):
        if root.is_file():
            digest.update(root.name.encode())
            digest.update(root.read_bytes())
        elif root.is_dir():
            for file in sorted(p for p in root.rglob("*") if p.is_file()):
                digest.update(str(file.relative_to(root.parent)).encode())
                digest.update(file.read_bytes())
    return digest.hexdigest()


# ------------------------------------------------------------------------ docker


def docker_server_version(client: Any | None = None) -> str | None:  # noqa: ANN401  docker sdk is untyped
    """Return the Docker server version, or None when no daemon is reachable."""
    try:
        import docker

        resolved = client or docker.from_env()
        version = resolved.version()
        return str(version.get("Version")) if version else None
    except Exception as exc:  # noqa: BLE001  any failure here means "no usable daemon"
        log.debug("docker.unavailable", error=str(exc)[:200])
        return None


def docker_available() -> bool:
    """True when a Docker daemon is reachable."""
    return docker_server_version() is not None


def build_task_image(
    task: Task,
    task_dir: Path,
    *,
    client: Any = None,  # noqa: ANN401  docker sdk is untyped
    rebuild: bool = False,
) -> str:
    """Build the task image, reusing a cached build when nothing relevant changed.

    The cache key is a content hash of the Dockerfile and the agent workspace. Hidden
    tests and the reference playbook are excluded on purpose, because neither is ever
    baked into the image and rebuilding on every test edit would make task authoring
    miserable.

    Args:
        task: The task definition.
        task_dir: Directory holding the Dockerfile and workspace.
        client: Docker client, created from the environment when omitted.
        rebuild: Build even when a matching tag already exists.

    Returns:
        The fully qualified image tag that was built or reused.

    Raises:
        SandboxError: If no daemon is reachable or the build fails.
    """
    import docker
    from docker.errors import BuildError, DockerException, ImageNotFound

    try:
        resolved = client or docker.from_env()
    except DockerException as exc:
        raise SandboxError(f"no Docker daemon reachable: {exc}") from exc

    dockerfile = task_dir / "Dockerfile"
    if not dockerfile.is_file():
        raise SandboxError(f"task {task.id} has no Dockerfile at {dockerfile}")

    content_hash = directory_content_hash([dockerfile, task_dir / "workspace"])[:12]
    tag = f"{task.image_tag}:{content_hash}"

    if not rebuild:
        try:
            resolved.images.get(tag)
            log.debug("image.cached", task=task.id, tag=tag)
            return tag
        except ImageNotFound:
            pass

    log.info("image.build", task=task.id, tag=tag)
    started = time.monotonic()
    try:
        resolved.images.build(
            path=str(task_dir),
            dockerfile="Dockerfile",
            tag=tag,
            rm=True,
            forcerm=True,
            pull=False,
            buildargs={"AGENT_USER": AGENT_USER},
        )
    except BuildError as exc:
        detail = "\n".join(str(line.get("stream", "")).rstrip() for line in exc.build_log)
        raise SandboxError(f"building {task.id} failed: {exc}\n{detail[-4000:]}") from exc
    except DockerException as exc:
        raise SandboxError(f"building {task.id} failed: {exc}") from exc

    log.info("image.built", task=task.id, tag=tag, seconds=round(time.monotonic() - started, 1))
    return tag


class DockerSandbox:
    """One container per run, with limits enforced and cleanup guaranteed.

    Used as a context manager. The container is removed on exit whether the body returned
    or raised, which is the only behaviour that survives a benchmark run being interrupted
    at three in the morning.
    """

    backend = SandboxBackend.DOCKER

    def __init__(
        self,
        task: Task,
        task_dir: Path,
        *,
        image_tag: str | None = None,
        client: Any = None,  # noqa: ANN401  docker sdk is untyped
    ) -> None:
        """Configure the sandbox without starting anything."""
        self.task = task
        self.task_dir = task_dir
        self._image_tag = image_tag
        self._client = client
        self._container: Any = None
        self._verify_installed = False

    def __enter__(self) -> Self:
        """Build the image if needed and start the container."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Always remove the container, including when the body raised."""
        self.close()

    def start(self) -> None:
        """Create and start the container."""
        import docker
        from docker.errors import DockerException

        try:
            client = self._client or docker.from_env()
        except DockerException as exc:
            raise SandboxError(f"no Docker daemon reachable: {exc}") from exc
        self._client = client

        tag = self._image_tag or build_task_image(self.task, self.task_dir, client=client)
        log.info("sandbox.start", task=self.task.id, image=tag)

        try:
            self._container = client.containers.run(
                tag,
                command=["sleep", "infinity"],
                detach=True,
                user=AGENT_USER,
                working_dir=WORKSPACE_CONTAINER_PATH,
                mem_limit=f"{self.task.memory_mb}m",
                memswap_limit=f"{self.task.memory_mb}m",
                nano_cpus=int(self.task.cpus * 1_000_000_000),
                pids_limit=self.task.pids_limit,
                network_disabled=not self.task.network_allowed,
                network_mode="none" if not self.task.network_allowed else "bridge",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                tmpfs={"/tmp": "rw,noexec,nosuid,size=256m"},  # noqa: S108  container tmpfs, not a host path
                environment={
                    "HOME": f"/home/{AGENT_USER}",
                    "TERM": "dumb",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONUNBUFFERED": "1",
                },
                labels={"trajectory.task": self.task.id, "trajectory.role": "sandbox"},
                auto_remove=False,
            )
        except DockerException as exc:
            raise SandboxError(f"starting a container for {self.task.id} failed: {exc}") from exc

        if self.task.setup_cmd:
            result = self.exec(
                self.task.setup_cmd,
                timeout_s=self.task.command_timeout_seconds,
                cap_bytes=16384,
            )
            if result.exit_code != 0:
                raise SandboxError(
                    f"setup_cmd for {self.task.id} exited {result.exit_code}: "
                    f"{result.combined[-2000:]}"
                )

    def close(self) -> None:
        """Remove the container. Safe to call more than once."""
        container, self._container = self._container, None
        if container is None:
            return
        try:
            container.remove(force=True, v=True)
            log.debug("sandbox.removed", task=self.task.id)
        except Exception as exc:  # noqa: BLE001  cleanup must never mask the original error
            log.warning("sandbox.cleanup_failed", task=self.task.id, error=str(exc)[:200])

    def _require(self) -> Any:  # noqa: ANN401  docker sdk is untyped
        if self._container is None:
            raise SandboxError("sandbox is not running")
        return self._container

    def exec(self, command: str, *, timeout_s: int, cap_bytes: int) -> ExecResult:
        """Run a shell command in the workspace."""
        container = self._require()
        started = time.monotonic()
        exit_code, raw = container.exec_run(
            cmd=["bash", "-c", _WRAPPER],
            workdir=WORKSPACE_CONTAINER_PATH,
            user=AGENT_USER,
            environment={
                "TRAJ_CMD": command,
                "TRAJ_TIMEOUT": str(timeout_s),
                "TRAJ_CAP": str(cap_bytes),
                "VERIFY_DIR": VERIFY_CONTAINER_PATH,
            },
            demux=False,
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        if exit_code != 0 and not raw.startswith(_RC):
            raise SandboxError(
                f"exec wrapper failed with {exit_code}: {raw[:400].decode('utf-8', 'replace')!r}"
            )
        rc, stdout, stderr, truncated = _parse_wrapper_output(raw, cap_bytes)
        return ExecResult(
            exit_code=rc,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=truncated,
            timed_out=rc == TIMEOUT_EXIT_CODE,
        )

    def read_file(self, path: str, *, max_lines: int) -> tuple[str, bool]:
        """Read a workspace file."""
        rel = _normalise_path(path)
        result = self.exec(
            f"test -f {rel!r} && head -n {max_lines + 1} -- {rel!r}",
            timeout_s=30,
            cap_bytes=262_144,
        )
        if result.exit_code != 0:
            raise FileNotFoundError(rel)
        lines = result.stdout.splitlines()
        truncated = len(lines) > max_lines or result.truncated
        return "\n".join(lines[:max_lines]), truncated

    def write_file(self, path: str, content: str) -> None:
        """Write a workspace file, creating parent directories."""
        rel = _normalise_path(path)
        container = self._require()
        buffer = io.BytesIO()
        data = content.encode()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            info = tarfile.TarInfo(name=rel)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        parent = str(Path(rel).parent)
        if parent not in (".", ""):
            self.exec(f"mkdir -p {parent!r}", timeout_s=30, cap_bytes=4096)
        if not container.put_archive(WORKSPACE_CONTAINER_PATH, buffer.getvalue()):
            raise SandboxError(f"writing {rel} failed")

    def list_dir(self, path: str) -> list[str]:
        """List one level of a workspace directory."""
        rel = _normalise_path(path)
        result = self.exec(
            f"test -d {rel!r} && LC_ALL=C ls -A1p -- {rel!r}", timeout_s=30, cap_bytes=65_536
        )
        if result.exit_code != 0:
            raise FileNotFoundError(rel)
        return [line for line in result.stdout.splitlines() if line]

    def manifest(self) -> WorkspaceManifest:
        """Hash every file in the workspace."""
        listing = self.exec(_MANIFEST_CMD, timeout_s=120, cap_bytes=4_000_000)
        count = self.exec(_COUNT_CMD, timeout_s=60, cap_bytes=1024)
        total = int(count.stdout.strip() or 0) if count.exit_code == 0 else 0
        return _parse_manifest(listing.stdout, total_files=total)

    def install_verify(self) -> None:
        """Copy the hidden tests into the container.

        Called by the verifier once the agent phase has ended, and never before.
        """
        if self._verify_installed:
            return
        container = self._require()
        verify_dir = self.task_dir / "verify"
        if not verify_dir.is_dir():
            raise SandboxError(f"task {self.task.id} has no verify/ directory")
        archive = _tar_bytes(verify_dir, arcname=Path(VERIFY_CONTAINER_PATH).name)
        if not container.put_archive("/", archive):
            raise SandboxError("installing the hidden tests failed")
        self._verify_installed = True
        log.debug("sandbox.verify_installed", task=self.task.id)

    def verify_present(self) -> bool:
        """True when the hidden tests exist in the container filesystem."""
        result = self.exec(f"test -d {VERIFY_CONTAINER_PATH}", timeout_s=15, cap_bytes=1024)
        return result.exit_code == 0


# ------------------------------------------------------------------------- local


_HOST_PATH_GUARD = re.compile(
    r"""(?x)
    (^|[\s;&|(])                       # start of a command word
    (rm|rmdir|mv|cp|chmod|chown|dd|mkfs|shred|truncate|tee)
    \b[^;&|]*?                         # its flags and earlier arguments
    \s(/(etc|usr|bin|sbin|lib|boot|dev|proc|sys|var|root|opt|srv|home|mnt|media)\b|/\s|/$|~)
    """
)


class LocalSandbox:
    """Runs commands in a temporary directory on the host.

    This is a development affordance, not an isolation boundary. It exists so that
    authoring a task, iterating on a metric, or running the unit suite does not require a
    Docker daemon, which is a real barrier for contributors and for CI jobs that have no
    business pulling images.

    Three things keep it honest. It refuses to start unless the operator explicitly opts
    in through an environment variable. It blocks commands that reach at obvious host
    paths, which catches the accident rather than the attack. And it reports
    `SandboxBackend.LOCAL`, which the results API uses to keep these runs off the
    leaderboard.

    Be clear about what it does not give you. The host filesystem is visible, so the
    hidden tests for the task being run are reachable from inside it by absolute path.
    The file tools refuse to leave the workspace, but a shell command does not have to use
    the file tools. That is the second, independent reason no local run belongs on a
    leaderboard: not only is it unisolated, its central guarantee does not hold.
    """

    backend = SandboxBackend.LOCAL

    def __init__(self, task: Task, task_dir: Path, *, allow_override: bool = False) -> None:
        """Configure the sandbox without creating anything.

        Args:
            task: The task definition.
            task_dir: Directory holding `workspace/` and `verify/`.
            allow_override: Bypass the environment opt in. Used only by this project's
                own tests, which run in a disposable container anyway.

        Raises:
            SandboxError: If the operator has not opted in.
        """
        if not allow_override and os.environ.get(ALLOW_LOCAL_ENV) != "1":
            raise SandboxError(
                "the local sandbox is not isolated and is off by default. Set "
                f"{ALLOW_LOCAL_ENV}=1 to use it for task authoring, and be aware that runs "
                "produced this way are stamped sandbox_backend=local and are refused by the "
                "results API leaderboard."
            )
        self.task = task
        self.task_dir = task_dir
        self._root: Path | None = None
        self._verify_installed = False

    @property
    def root(self) -> Path:
        """Sandbox root, holding `workspace/`, `verify/` and scratch space."""
        if self._root is None:
            raise SandboxError("sandbox is not running")
        return self._root

    @property
    def workspace(self) -> Path:
        """The agent visible working tree."""
        return self.root / "workspace"

    def __enter__(self) -> Self:
        """Materialise the workspace."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Always remove the temporary tree, including when the body raised."""
        self.close()

    def start(self) -> None:
        """Copy the task workspace into a fresh temporary directory."""
        self._root = Path(tempfile.mkdtemp(prefix=f"trajectory-{self.task.id}-"))
        (self._root / "tmp").mkdir()
        source = self.task_dir / "workspace"
        if not source.is_dir():
            raise SandboxError(f"task {self.task.id} has no workspace/ directory")
        shutil.copytree(source, self.workspace, symlinks=False)
        log.info("sandbox.start", task=self.task.id, backend="local", root=str(self._root))

        if self.task.setup_cmd:
            result = self.exec(
                self.task.setup_cmd,
                timeout_s=self.task.command_timeout_seconds,
                cap_bytes=16384,
            )
            if result.exit_code != 0:
                raise SandboxError(
                    f"setup_cmd for {self.task.id} exited {result.exit_code}: "
                    f"{result.combined[-2000:]}"
                )

    def close(self) -> None:
        """Remove the temporary tree. Safe to call more than once."""
        root, self._root = self._root, None
        if root is not None and root.exists():
            shutil.rmtree(root, ignore_errors=True)
            log.debug("sandbox.removed", task=self.task.id, backend="local")

    def _env(self) -> dict[str, str]:
        """Environment for commands, pointed at the sandbox rather than the real home."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "GOCACHE", "GOMODCACHE", "GOPATH")
        }
        env.update(
            {
                "HOME": str(self.root),
                "TMPDIR": str(self.root / "tmp"),
                "TERM": "dumb",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "VERIFY_DIR": str(self.root / "verify"),
                "GIT_CONFIG_GLOBAL": str(self.root / ".gitconfig"),
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
        return env

    def exec(self, command: str, *, timeout_s: int, cap_bytes: int) -> ExecResult:
        """Run a shell command in the workspace, refusing obvious host path accidents."""
        if _HOST_PATH_GUARD.search(command):
            return ExecResult(
                exit_code=126,
                stdout="",
                stderr=(
                    "refused by the local sandbox: this command writes outside the sandbox "
                    "root. Run with the Docker backend if the task genuinely needs it."
                ),
                duration_ms=0,
                truncated=False,
                timed_out=False,
            )

        env = self._env()
        env.update(
            {"TRAJ_CMD": command, "TRAJ_TIMEOUT": str(timeout_s), "TRAJ_CAP": str(cap_bytes)}
        )
        started = time.monotonic()
        try:
            completed = subprocess.run(  # noqa: S603  fixed wrapper, command arrives by environment
                ["bash", "-c", _WRAPPER],  # noqa: S607
                cwd=self.workspace,
                env=env,
                capture_output=True,
                timeout=timeout_s + 15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                exit_code=TIMEOUT_EXIT_CODE,
                stdout="",
                stderr=f"command exceeded {timeout_s}s and was killed",
                duration_ms=int((time.monotonic() - started) * 1000),
                truncated=False,
                timed_out=True,
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        rc, stdout, stderr, truncated = _parse_wrapper_output(completed.stdout, cap_bytes)
        return ExecResult(
            exit_code=rc,
            stdout=stdout,
            stderr=stderr,
            duration_ms=duration_ms,
            truncated=truncated,
            timed_out=rc == TIMEOUT_EXIT_CODE,
        )

    def _resolve(self, path: str) -> Path:
        """Resolve a workspace relative path, refusing anything that escapes."""
        rel = _normalise_path(path)
        target = (self.workspace / rel).resolve()
        root = self.workspace.resolve()
        if target != root and root not in target.parents:
            raise ValueError(f"path escapes the workspace: {path}")
        return target

    def read_file(self, path: str, *, max_lines: int) -> tuple[str, bool]:
        """Read a workspace file."""
        target = self._resolve(path)
        if not target.is_file():
            raise FileNotFoundError(_normalise_path(path))
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[:max_lines]), len(lines) > max_lines

    def write_file(self, path: str, content: str) -> None:
        """Write a workspace file, creating parent directories."""
        target = self._resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def list_dir(self, path: str) -> list[str]:
        """List one level of a workspace directory."""
        target = self._resolve(path)
        if not target.is_dir():
            raise FileNotFoundError(_normalise_path(path))
        return sorted(f"{p.name}/" if p.is_dir() else p.name for p in target.iterdir())

    def manifest(self) -> WorkspaceManifest:
        """Hash every file in the workspace."""
        listing = self.exec(_MANIFEST_CMD, timeout_s=120, cap_bytes=4_000_000)
        count = self.exec(_COUNT_CMD, timeout_s=60, cap_bytes=1024)
        total = int(count.stdout.strip() or 0) if count.exit_code == 0 else 0
        return _parse_manifest(listing.stdout, total_files=total)

    def install_verify(self) -> None:
        """Copy the hidden tests into the sandbox root, next to the workspace."""
        if self._verify_installed:
            return
        source = self.task_dir / "verify"
        if not source.is_dir():
            raise SandboxError(f"task {self.task.id} has no verify/ directory")
        shutil.copytree(source, self.root / "verify", symlinks=False)
        self._verify_installed = True
        log.debug("sandbox.verify_installed", task=self.task.id, backend="local")

    def verify_present(self) -> bool:
        """True when the hidden tests exist inside the sandbox."""
        return (self.root / "verify").is_dir()


# ----------------------------------------------------------------------- factory


def build_sandbox(
    task: Task,
    task_dir: Path,
    backend: SandboxBackend,
    *,
    image_tag: str | None = None,
    allow_local_override: bool = False,
) -> Sandbox:
    """Create the sandbox for a run.

    Args:
        task: The task definition.
        task_dir: Directory holding the Dockerfile, workspace and hidden tests.
        backend: Which implementation to use.
        image_tag: Pre-built image tag, so a suite run builds each image once.
        allow_local_override: Bypass the local backend opt in, for this project's tests.

    Returns:
        A sandbox that has not been started yet.
    """
    if backend is SandboxBackend.DOCKER:
        return DockerSandbox(task, task_dir, image_tag=image_tag)
    return LocalSandbox(task, task_dir, allow_override=allow_local_override)
