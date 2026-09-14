"""Task discovery and validation.

A benchmark is only as good as its tasks, and the failure mode nobody admits to is a task
whose own reference solution stopped passing six weeks ago. Everything here exists to make
that impossible to miss: the loader refuses malformed definitions outright, the validator
reports everything else it can check statically, and CI replays every reference playbook
against its hidden tests on every push.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import structlog
import yaml

from trajectory_core.models import ReferencePlaybook, Task, ToolName

log = structlog.get_logger(__name__)

TASK_FILE = "task.yaml"
PLAYBOOK_FILE = "reference/playbook.yaml"
REQUIRED_DIRS = ("workspace", "verify", "reference")

_ABSOLUTE_WORKSPACE = re.compile(r"(?<![\w/])/workspace\b")
_UNPINNED_FROM = re.compile(r"^\s*FROM\s+(?!scratch\b)(\S+)", re.IGNORECASE | re.MULTILINE)


class Severity(StrEnum):
    """How much a validation finding matters."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class Issue:
    """One validation finding."""

    severity: Severity
    task_id: str
    message: str

    def __str__(self) -> str:
        """Render as a single line for the terminal."""
        return f"[{self.severity.value}] {self.task_id}: {self.message}"


class TaskLoadError(RuntimeError):
    """Raised when a task definition cannot be read at all."""


@dataclass(frozen=True, slots=True)
class LoadedTask:
    """A task definition together with its directory and reference playbook."""

    task: Task
    directory: Path
    playbook: ReferencePlaybook

    @property
    def id(self) -> str:
        """Task identifier."""
        return self.task.id


def _read_yaml(path: Path) -> dict[str, object]:
    """Read a YAML mapping, with a readable error when it is not one."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaskLoadError(f"{path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TaskLoadError(f"{path} must contain a mapping, found {type(data).__name__}")
    return data


def load_task(directory: Path) -> LoadedTask:
    """Load one task directory.

    Args:
        directory: Directory containing `task.yaml`.

    Returns:
        The task, its directory and its reference playbook.

    Raises:
        TaskLoadError: If any required file is missing or fails validation.
    """
    task_file = directory / TASK_FILE
    if not task_file.is_file():
        raise TaskLoadError(f"{directory} has no {TASK_FILE}")

    payload = _read_yaml(task_file)
    payload.setdefault("id", directory.name)
    payload.setdefault("suite", directory.parent.name)
    try:
        task = Task.model_validate(payload)
    except Exception as exc:
        raise TaskLoadError(f"{task_file} is not a valid task: {exc}") from exc

    playbook_file = directory / PLAYBOOK_FILE
    if not playbook_file.is_file():
        raise TaskLoadError(f"{directory} has no {PLAYBOOK_FILE}")
    playbook_payload = _read_yaml(playbook_file)
    playbook_payload.setdefault("task_id", task.id)
    try:
        playbook = ReferencePlaybook.model_validate(playbook_payload)
    except Exception as exc:
        raise TaskLoadError(f"{playbook_file} is not a valid playbook: {exc}") from exc

    return LoadedTask(task=task, directory=directory, playbook=playbook)


def discover(root: Path, *, suite: str | None = None) -> list[LoadedTask]:
    """Find every task under a tasks root.

    Args:
        root: The `tasks/` directory.
        suite: Restrict to one suite directory.

    Returns:
        Tasks sorted by identifier.

    Raises:
        TaskLoadError: If a directory that looks like a task cannot be loaded.
    """
    if not root.is_dir():
        raise TaskLoadError(f"{root} is not a directory")
    suites = [root / suite] if suite else sorted(p for p in root.iterdir() if p.is_dir())

    loaded: list[LoadedTask] = []
    for suite_dir in suites:
        if not suite_dir.is_dir():
            raise TaskLoadError(f"no suite directory at {suite_dir}")
        for task_dir in sorted(p for p in suite_dir.iterdir() if p.is_dir()):
            if (task_dir / TASK_FILE).is_file():
                loaded.append(load_task(task_dir))
    return sorted(loaded, key=lambda item: item.id)


def validate(loaded: LoadedTask) -> list[Issue]:
    """Check everything about a task that can be checked without running it.

    The one thing this cannot check is whether the reference playbook actually solves the
    task. That needs a sandbox, and it is what `trajectory tasks verify-references` and the
    CI job do on every push.

    Args:
        loaded: A loaded task.

    Returns:
        Findings, empty when the task is clean.
    """
    task, directory, playbook = loaded.task, loaded.directory, loaded.playbook
    issues: list[Issue] = []

    def error(message: str) -> None:
        issues.append(Issue(Severity.ERROR, task.id, message))

    def warn(message: str) -> None:
        issues.append(Issue(Severity.WARNING, task.id, message))

    if directory.name != task.id:
        error(f"directory is named {directory.name!r} but the task id is {task.id!r}")
    if directory.parent.name != task.suite:
        error(
            f"task is under suite directory {directory.parent.name!r} but declares {task.suite!r}"
        )

    for required in REQUIRED_DIRS:
        target = directory / required
        if not target.is_dir():
            error(f"missing {required}/ directory")
        elif not any(target.rglob("*")):
            error(f"{required}/ is empty")

    dockerfile = directory / "Dockerfile"
    if not dockerfile.is_file():
        error("missing Dockerfile")
    else:
        text = dockerfile.read_text(encoding="utf-8")
        if re.search(r"^\s*COPY\s+.*\bverify\b", text, re.IGNORECASE | re.MULTILINE):
            error(
                "the Dockerfile copies verify/ into the image. The hidden tests must never "
                "be in the image, or the agent can read them"
            )
        if re.search(r"^\s*COPY\s+.*\breference\b", text, re.IGNORECASE | re.MULTILINE):
            error("the Dockerfile copies reference/ into the image, which leaks the solution")
        # A USER instruction at the start of a line. A substring check matches
        # ARG AGENT_USER and would pass a Dockerfile that never drops root.
        if not re.search(r"^\s*USER\s+\S+", text, re.IGNORECASE | re.MULTILINE):
            error("the Dockerfile never switches to a non-root USER")
        for match in _UNPINNED_FROM.finditer(text):
            image = match.group(1)
            if "@sha256:" in image:
                continue
            tag = image.rsplit(":", 1)[-1] if ":" in image.rsplit("/", 1)[-1] else ""
            if not tag or tag == "latest" or not any(c.isdigit() for c in tag):
                error(
                    f"base image {image!r} uses a floating tag. Pin a version, or a digest. "
                    "The image a published number came from is recorded on every run, but a "
                    "floating tag means nobody can rebuild it"
                )

    if playbook.task_id != task.id:
        error(f"playbook declares task_id {playbook.task_id!r}")
    if playbook.step_count != task.reference_step_count:
        error(
            f"reference_step_count is {task.reference_step_count} but the playbook has "
            f"{playbook.step_count} steps. Step efficiency divides by this number, so it "
            "cannot be set by hand"
        )

    for index, step in enumerate(playbook.steps):
        if step.tool is ToolName.BASH and not str(step.args.get("command", "")).strip():
            error(f"playbook step {index} is a bash call with no command")
        for value in step.args.values():
            if isinstance(value, str) and _ABSOLUTE_WORKSPACE.search(value):
                warn(
                    f"playbook step {index} uses the absolute path /workspace. Relative paths "
                    "keep the task portable across sandbox backends"
                )
        needs_path = step.tool in (ToolName.READ_FILE, ToolName.WRITE_FILE, ToolName.LIST_DIR)
        if needs_path and not str(step.args.get("path", "")).strip():
            error(f"playbook step {index} is a {step.tool.value} call with no path")

    if not task.relevant_paths:
        warn("relevant_paths is empty, so scope creep (F09) cannot be detected for this task")
    if task.network_allowed:
        warn(f"network access is enabled: {task.network_reason}")
    if "$VERIFY_DIR" not in task.verify_cmd and "/verify" not in task.verify_cmd:
        warn(
            "verify_cmd does not reference $VERIFY_DIR, so the hidden tests may not be the "
            "thing being run"
        )
    if task.reference_step_count > task.max_steps * 0.75:
        warn(
            f"the reference takes {task.reference_step_count} of a {task.max_steps} step "
            "budget, which leaves an agent very little room to explore"
        )

    return issues


def validate_all(tasks: list[LoadedTask]) -> list[Issue]:
    """Validate a list of tasks and check for duplicate identifiers."""
    issues: list[Issue] = []
    seen: dict[str, Path] = {}
    for loaded in tasks:
        issues.extend(validate(loaded))
        if loaded.id in seen:
            issues.append(
                Issue(
                    Severity.ERROR,
                    loaded.id,
                    f"duplicate task id, also defined at {seen[loaded.id]}",
                )
            )
        seen[loaded.id] = loaded.directory
    return issues


def default_tasks_root(start: Path | None = None) -> Path:
    """Find the repository's `tasks/` directory by walking upward.

    Args:
        start: Directory to start from, defaulting to the working directory.

    Returns:
        The tasks directory.

    Raises:
        TaskLoadError: If no tasks directory is found.
    """
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        tasks = candidate / "tasks"
        if tasks.is_dir():
            return tasks
    raise TaskLoadError(
        "no tasks/ directory found. Run from inside a checkout, or pass --tasks-root."
    )
