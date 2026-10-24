"""The verifier must not import code the agent planted in its own workspace.

`python -m pytest` and `python -c` both put the current directory at the front of the
import path, and the agent can write the current directory. That is the cheapest way to
make a grader lie: drop a module next to the solution whose name a hidden test imports.
VERIFY_HARDENING_ENV sets PYTHONSAFEPATH, which removes that implicit entry, so these
tests pin the behaviour both ways round rather than trusting the flag by name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trajectory_core.testing import make_task
from trajectory_runner.sandbox import LocalSandbox
from trajectory_runner.verifier import VERIFY_HARDENING_ENV


@pytest.fixture
def planted_task_dir(tmp_path: Path) -> Path:
    """A task whose workspace contains a module the agent planted."""
    root = tmp_path / "planted-01"
    (root / "workspace").mkdir(parents=True)
    (root / "verify").mkdir()
    # A module sitting in the workspace, which is the current directory at verify time.
    (root / "workspace" / "planted.py").write_text("VALUE = 'from the workspace'\n")
    return root


def _sandbox(task_dir: Path) -> LocalSandbox:
    task = make_task(id="planted-01", image_tag="trajectory/planted-01")
    return LocalSandbox(task, task_dir, allow_override=True)


class TestImplicitImportPath:
    def test_without_hardening_a_planted_module_is_importable(self, planted_task_dir: Path) -> None:
        with _sandbox(planted_task_dir) as sb:
            result = sb.exec(
                'python3 -c "import planted; print(planted.VALUE)"',
                timeout_s=30,
                cap_bytes=4096,
            )
        assert result.exit_code == 0
        assert "from the workspace" in result.stdout

    def test_with_hardening_the_planted_module_is_not_on_the_path(
        self, planted_task_dir: Path
    ) -> None:
        with _sandbox(planted_task_dir) as sb:
            result = sb.exec(
                'python3 -c "import planted"',
                timeout_s=30,
                cap_bytes=4096,
                env=VERIFY_HARDENING_ENV,
            )
        assert result.exit_code != 0
        assert "ModuleNotFoundError" in result.stdout or "ModuleNotFoundError" in result.stderr


def test_the_hardening_env_sets_the_expected_flags() -> None:
    assert VERIFY_HARDENING_ENV["PYTHONSAFEPATH"] == "1"
    assert VERIFY_HARDENING_ENV["PYTHONNOUSERSITE"] == "1"
