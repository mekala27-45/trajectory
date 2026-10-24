"""The Harbor model check restates the hardening, so a test has to hold the two together.

The check imports nothing from this repository, which is the point of it: it runs where
only Harbor is installed. That means it cannot read VERIFY_HARDENING_ENV, so if the
verifier's hardening changed and the check's copy did not, the check would pass exports
that no longer carry what the native verifier runs with.
"""

from __future__ import annotations

import check_harbor_models as check

from trajectory_runner.verifier import VERIFY_HARDENING_ENV


def test_the_check_expects_exactly_the_native_hardening() -> None:
    assert check.HARDENING == VERIFY_HARDENING_ENV


def test_without_harbor_installed_it_says_so_and_fails(tmp_path, capsys) -> None:
    """In the repository environment Harbor is absent, and the check must not pass."""
    result = check.main(["--tasks", str(tmp_path), "--atif", str(tmp_path)])
    assert result == 1
    assert "Harbor is not installed" in capsys.readouterr().err
