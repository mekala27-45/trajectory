"""Task validation is the gate that keeps a broken task out of the suite.

Each check gets a task that trips it and, where the distinction matters, one that looks
similar and is fine. The expensive failure here is a false negative: a validator that
passes a task whose Dockerfile bakes the hidden tests into the image has quietly destroyed
the benchmark.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from trajectory_runner.loader import (
    Severity,
    TaskLoadError,
    default_tasks_root,
    discover,
    load_task,
    validate,
    validate_all,
)

REPO = Path(__file__).resolve().parents[3]

GOOD_TASK = {
    "title": "A task that validates",
    "description": "What is broken and what fixed means.",
    "language": "python",
    "difficulty": 2,
    "tags": ["demo"],
    "image_tag": "trajectory/demo-01",
    "agent_prompt": "Make the tests pass.",
    "max_steps": 30,
    "timeout_seconds": 600,
    "verify_cmd": 'python -m pytest -q "$VERIFY_DIR"',
    "verify_parser": "pytest",
    "reference_step_count": 2,
    "relevant_paths": ["src/**"],
}

GOOD_PLAYBOOK = {
    "steps": [
        {"tool": "bash", "args": {"command": "ls -la"}, "note": "Look first."},
        {"tool": "finish", "args": {"summary": "Done."}, "note": "Close out."},
    ]
}

GOOD_DOCKERFILE = """\
FROM python:3.12.8-slim-bookworm
ARG AGENT_USER=agent
RUN useradd --create-home --uid 10001 ${AGENT_USER}
WORKDIR /workspace
COPY workspace/ /workspace/
USER ${AGENT_USER}
"""


def write_task(
    root: Path,
    *,
    task: dict | None = None,
    playbook: dict | None = None,
    dockerfile: str | None = GOOD_DOCKERFILE,
    task_id: str = "demo-01",
    suite: str = "core-12",
    with_dirs: tuple[str, ...] = ("workspace", "verify", "reference"),
) -> Path:
    """Materialise a task directory on disk."""
    directory = root / suite / task_id
    directory.mkdir(parents=True, exist_ok=True)
    for name in with_dirs:
        (directory / name).mkdir(exist_ok=True)
    if "workspace" in with_dirs:
        (directory / "workspace" / "app.py").write_text("x = 1\n")
    if "verify" in with_dirs:
        (directory / "verify" / "test_app.py").write_text("def test_x():\n    assert True\n")
    if dockerfile is not None:
        (directory / "Dockerfile").write_text(dockerfile)
    (directory / "task.yaml").write_text(yaml.safe_dump({**GOOD_TASK, **(task or {})}))
    if "reference" in with_dirs:
        (directory / "reference").mkdir(exist_ok=True)
        (directory / "reference" / "playbook.yaml").write_text(
            yaml.safe_dump(playbook or GOOD_PLAYBOOK)
        )
    return directory


def errors(issues) -> list[str]:
    return [issue.message for issue in issues if issue.severity is Severity.ERROR]


def warnings(issues) -> list[str]:
    return [issue.message for issue in issues if issue.severity is Severity.WARNING]


class TestLoading:
    def test_a_good_task_loads(self, tmp_path: Path):
        loaded = load_task(write_task(tmp_path))
        assert loaded.id == "demo-01"
        assert loaded.task.suite == "core-12"
        assert loaded.playbook.step_count == 2

    def test_the_identifier_and_suite_come_from_the_path(self, tmp_path: Path):
        loaded = load_task(write_task(tmp_path, task_id="other-99", suite="extras"))
        assert loaded.task.id == "other-99"
        assert loaded.task.suite == "extras"

    def test_a_missing_task_file_is_reported(self, tmp_path: Path):
        with pytest.raises(TaskLoadError, match=r"has no task\.yaml"):
            load_task(tmp_path)

    def test_a_missing_playbook_is_reported(self, tmp_path: Path):
        directory = write_task(tmp_path)
        (directory / "reference" / "playbook.yaml").unlink()
        with pytest.raises(TaskLoadError, match=r"playbook\.yaml"):
            load_task(directory)

    def test_invalid_yaml_is_reported_with_the_path(self, tmp_path: Path):
        directory = write_task(tmp_path)
        (directory / "task.yaml").write_text("title: [unclosed\n")
        with pytest.raises(TaskLoadError, match="not valid YAML"):
            load_task(directory)

    def test_a_yaml_list_is_not_a_task(self, tmp_path: Path):
        directory = write_task(tmp_path)
        (directory / "task.yaml").write_text("- one\n- two\n")
        with pytest.raises(TaskLoadError, match="must contain a mapping"):
            load_task(directory)

    def test_an_invalid_task_names_the_field(self, tmp_path: Path):
        directory = write_task(tmp_path, task={"difficulty": 9})
        with pytest.raises(TaskLoadError, match="not a valid task"):
            load_task(directory)

    def test_a_playbook_that_never_finishes_is_rejected(self, tmp_path: Path):
        directory = write_task(
            tmp_path,
            playbook={"steps": [{"tool": "bash", "args": {"command": "ls"}}] * 2},
        )
        with pytest.raises(TaskLoadError, match="not a valid playbook"):
            load_task(directory)

    def test_discovery_finds_tasks_and_sorts_them(self, tmp_path: Path):
        write_task(tmp_path, task_id="b-02")
        write_task(tmp_path, task_id="a-01")
        assert [t.id for t in discover(tmp_path)] == ["a-01", "b-02"]

    def test_discovery_can_be_scoped_to_a_suite(self, tmp_path: Path):
        write_task(tmp_path, task_id="a-01", suite="core-12")
        write_task(tmp_path, task_id="b-02", suite="extras")
        assert [t.id for t in discover(tmp_path, suite="extras")] == ["b-02"]

    def test_discovery_ignores_directories_that_are_not_tasks(self, tmp_path: Path):
        write_task(tmp_path, task_id="a-01")
        (tmp_path / "core-12" / "notes").mkdir()
        assert len(discover(tmp_path)) == 1

    def test_discovery_rejects_a_missing_root(self, tmp_path: Path):
        with pytest.raises(TaskLoadError, match="not a directory"):
            discover(tmp_path / "nope")

    def test_discovery_rejects_a_missing_suite(self, tmp_path: Path):
        with pytest.raises(TaskLoadError, match="no suite directory"):
            discover(tmp_path, suite="nope")

    def test_the_tasks_root_is_found_by_walking_upward(self):
        assert default_tasks_root(REPO / "packages" / "core" / "src") == REPO / "tasks"

    def test_no_tasks_root_is_an_error_with_a_hint(self, tmp_path: Path):
        with pytest.raises(TaskLoadError, match="--tasks-root"):
            default_tasks_root(tmp_path)


class TestValidation:
    def test_a_good_task_produces_nothing(self, tmp_path: Path):
        assert validate(load_task(write_task(tmp_path))) == []

    def test_a_low_agent_uid_is_an_error(self, tmp_path: Path):
        """The bug that stopped two task images building at all.

        `node:*` creates its own `node` user at uid 1000, so `useradd --uid 1000` there
        fails with "UID 1000 is not unique" and the build dies on that RUN line. Every
        task in the suite shipped with `--uid 1000`, which was invisible until someone
        built the images on a machine with a Docker daemon.
        """
        dockerfile = GOOD_DOCKERFILE.replace("--uid 10001", "--uid 1000")
        issues = validate(load_task(write_task(tmp_path, dockerfile=dockerfile)))
        assert any("creates the agent at uid 1000" in message for message in errors(issues))

    def test_the_uid_boundary_is_inclusive(self, tmp_path: Path):
        dockerfile = GOOD_DOCKERFILE.replace("--uid 10001", "--uid 10000")
        issues = validate(load_task(write_task(tmp_path, dockerfile=dockerfile)))
        assert any("uid 10000" in message for message in errors(issues))

        dockerfile = GOOD_DOCKERFILE.replace("--uid 10001", "--uid 10001")
        assert validate(load_task(write_task(tmp_path, dockerfile=dockerfile))) == []

    def test_the_equals_form_is_caught_too(self, tmp_path: Path):
        # `--uid=1000` is the same instruction and a regex that only matched a space
        # would wave it through.
        dockerfile = GOOD_DOCKERFILE.replace("--uid 10001", "--uid=1000")
        issues = validate(load_task(write_task(tmp_path, dockerfile=dockerfile)))
        assert any("creates the agent at uid 1000" in message for message in errors(issues))

    def test_a_dockerfile_that_bakes_in_the_hidden_tests_is_an_error(self, tmp_path: Path):
        """The most expensive false negative there is. The benchmark dies silently."""
        dockerfile = GOOD_DOCKERFILE + "COPY verify/ /verify/\n"
        issues = validate(load_task(write_task(tmp_path, dockerfile=dockerfile)))
        assert any("hidden tests must never" in message for message in errors(issues))

    def test_a_dockerfile_that_bakes_in_the_reference_is_an_error(self, tmp_path: Path):
        dockerfile = GOOD_DOCKERFILE + "COPY reference/ /reference/\n"
        issues = validate(load_task(write_task(tmp_path, dockerfile=dockerfile)))
        assert any("leaks the solution" in message for message in errors(issues))

    def test_a_dockerfile_that_stays_root_is_an_error(self, tmp_path: Path):
        dockerfile = GOOD_DOCKERFILE.replace("USER ${AGENT_USER}\n", "")
        issues = validate(load_task(write_task(tmp_path, dockerfile=dockerfile)))
        assert any("non-root USER" in message for message in errors(issues))

    def test_a_floating_base_image_tag_is_an_error(self, tmp_path: Path):
        for image in ("python:latest", "python", "python:slim"):
            dockerfile = GOOD_DOCKERFILE.replace("python:3.12.8-slim-bookworm", image)
            issues = validate(load_task(write_task(tmp_path, dockerfile=dockerfile)))
            assert any("floating tag" in message for message in errors(issues)), image

    def test_a_digest_pinned_base_image_is_accepted(self, tmp_path: Path):
        dockerfile = GOOD_DOCKERFILE.replace(
            "python:3.12.8-slim-bookworm", "python@sha256:" + "a" * 64
        )
        issues = validate(load_task(write_task(tmp_path, dockerfile=dockerfile)))
        assert not any("floating tag" in message for message in errors(issues))

    def test_a_missing_dockerfile_is_an_error(self, tmp_path: Path):
        issues = validate(load_task(write_task(tmp_path, dockerfile=None)))
        assert "missing Dockerfile" in errors(issues)

    def test_a_missing_required_directory_is_an_error(self, tmp_path: Path):
        directory = write_task(tmp_path)
        for entry in (directory / "verify").iterdir():
            entry.unlink()
        (directory / "verify").rmdir()
        issues = validate(load_task(directory))
        assert any("missing verify/" in message for message in errors(issues))

    def test_an_empty_required_directory_is_an_error(self, tmp_path: Path):
        directory = write_task(tmp_path)
        for entry in (directory / "verify").iterdir():
            entry.unlink()
        issues = validate(load_task(directory))
        assert any("verify/ is empty" in message for message in errors(issues))

    def test_a_reference_step_count_that_disagrees_with_the_playbook_is_an_error(
        self, tmp_path: Path
    ):
        """Step efficiency divides by this, so it cannot be set by hand."""
        issues = validate(load_task(write_task(tmp_path, task={"reference_step_count": 7})))
        message = next(m for m in errors(issues) if "reference_step_count" in m)
        assert "cannot be set by hand" in message

    def test_a_bash_step_with_no_command_is_an_error(self, tmp_path: Path):
        playbook = {
            "steps": [
                {"tool": "bash", "args": {"command": "   "}},
                {"tool": "finish", "args": {"summary": "Done."}},
            ]
        }
        issues = validate(load_task(write_task(tmp_path, playbook=playbook)))
        assert any("no command" in message for message in errors(issues))

    def test_a_file_step_with_no_path_is_an_error(self, tmp_path: Path):
        playbook = {
            "steps": [
                {"tool": "read_file", "args": {}},
                {"tool": "finish", "args": {"summary": "Done."}},
            ]
        }
        issues = validate(load_task(write_task(tmp_path, playbook=playbook)))
        assert any("no path" in message for message in errors(issues))

    def test_an_absolute_workspace_path_is_a_warning_not_an_error(self, tmp_path: Path):
        """It works on Docker and breaks on the local backend, so it is portability advice."""
        playbook = {
            "steps": [
                {"tool": "bash", "args": {"command": "cat /workspace/app.py"}},
                {"tool": "finish", "args": {"summary": "Done."}},
            ]
        }
        issues = validate(load_task(write_task(tmp_path, playbook=playbook)))
        assert errors(issues) == []
        assert any("/workspace" in message for message in warnings(issues))

    def test_an_empty_relevant_paths_is_a_warning(self, tmp_path: Path):
        issues = validate(load_task(write_task(tmp_path, task={"relevant_paths": []})))
        assert any("scope creep" in message for message in warnings(issues))

    def test_network_access_is_a_warning_that_names_the_reason(self, tmp_path: Path):
        issues = validate(
            load_task(
                write_task(
                    tmp_path,
                    task={"network_allowed": True, "network_reason": "pulls a pinned wheel"},
                )
            )
        )
        assert any("pulls a pinned wheel" in message for message in warnings(issues))

    def test_a_verify_command_that_ignores_the_hidden_tests_is_a_warning(self, tmp_path: Path):
        issues = validate(load_task(write_task(tmp_path, task={"verify_cmd": "pytest -q tests"})))
        assert any("VERIFY_DIR" in message for message in warnings(issues))

    def test_a_reference_that_eats_the_step_budget_is_a_warning(self, tmp_path: Path):
        playbook = {
            "steps": [
                *[{"tool": "bash", "args": {"command": f"echo {i}"}} for i in range(8)],
                {"tool": "finish", "args": {"summary": "Done."}},
            ]
        }
        issues = validate(
            load_task(
                write_task(
                    tmp_path,
                    task={"max_steps": 10, "reference_step_count": 9},
                    playbook=playbook,
                )
            )
        )
        assert any("room to explore" in message for message in warnings(issues))

    def test_a_playbook_declaring_the_wrong_task_is_an_error(self, tmp_path: Path):
        playbook = {"task_id": "someone-else-01", **GOOD_PLAYBOOK}
        issues = validate(load_task(write_task(tmp_path, playbook=playbook)))
        assert any("declares task_id" in message for message in errors(issues))

    def test_duplicate_identifiers_across_suites_are_an_error(self, tmp_path: Path):
        write_task(tmp_path, task_id="same-01", suite="core-12")
        write_task(tmp_path, task_id="same-01", suite="extras")
        issues = validate_all(discover(tmp_path))
        assert any("duplicate task id" in message for message in errors(issues))


class TestShippedSuite:
    """The suite in this repository has to validate, or CI is reporting a lie."""

    def test_every_shipped_task_validates_with_no_errors(self):
        tasks = discover(REPO / "tasks")
        assert len(tasks) == 12
        assert errors(validate_all(tasks)) == []

    def test_every_shipped_task_declares_relevant_paths(self):
        for loaded in discover(REPO / "tasks"):
            assert loaded.task.relevant_paths, loaded.id

    def test_no_shipped_task_needs_the_network(self):
        """Every task offline is what makes the suite reproducible a year from now."""
        for loaded in discover(REPO / "tasks"):
            assert loaded.task.network_allowed is False, loaded.id

    def test_the_suite_covers_a_spread_of_difficulty(self):
        tiers = sorted({loaded.task.difficulty for loaded in discover(REPO / "tasks")})
        assert tiers == [1, 2, 3, 4, 5]

    def test_the_suite_covers_every_supported_language(self):
        languages = {loaded.task.language.value for loaded in discover(REPO / "tasks")}
        assert languages == {"python", "typescript", "go", "sql", "any"}
