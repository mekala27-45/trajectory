"""Tests for the version consistency gate.

Every one of these failure modes is silent. A wheel built with the previous number still
installs, a run record with a stale `harness_version` still validates, and a release page
announcing the wrong version still renders. Nothing breaks, which is the whole reason this
is a check and not a habit.
"""

from __future__ import annotations

import check_version as gate
import pytest


class TestTheRepositoryAsCommitted:
    def test_every_source_agrees(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert gate.main([]) == 0
        assert "agrees across" in capsys.readouterr().out

    def test_it_reads_from_every_place_a_version_is_stated(self) -> None:
        found = gate.declared_versions()
        keys = " ".join(found)
        # Four pyprojects, the stamped constant, three lock entries, the release heading.
        assert "pyproject.toml" in keys
        assert "packages/api/pyproject.toml" in keys
        assert "HARNESS_VERSION" in keys
        assert "uv.lock" in keys
        assert "release-notes.md" in keys
        assert len(found) >= 9, found

    def test_the_stamped_constant_is_one_of_them(self) -> None:
        from trajectory_core.models import HARNESS_VERSION

        found = gate.declared_versions()
        assert HARNESS_VERSION in set(found.values())

    def test_a_matching_tag_passes(self) -> None:
        version = next(iter(set(gate.declared_versions().values())))
        assert gate.main([f"--tag=v{version}"]) == 0

    def test_a_tag_without_the_v_prefix_also_passes(self) -> None:
        version = next(iter(set(gate.declared_versions().values())))
        assert gate.main([f"--tag={version}"]) == 0


class TestTheKeysAreNotPlatformSpecific:
    """The bug that made this gate unusable on the machine that needed it.

    The keys were rendered with `str(Path(...))`, and the required source names were
    forward slash literals. On Windows the two never matched, so the check reported
    "could not read a version from: packages/core/pyproject.toml" about a file it had
    just read, and exited 1 no matter what the versions said. It was written the day
    before a release tag and failed the first time anyone ran it off Linux.

    Asserted here rather than fixed and forgotten, because this repository has now had
    seven bugs of this shape and the only durable answer is a check.
    """

    def test_no_key_contains_a_backslash(self) -> None:
        found = gate.declared_versions()
        offenders = [key for key in found if "\\" in key]
        assert offenders == [], offenders

    def test_every_required_source_is_keyed_exactly(self) -> None:
        found = gate.declared_versions()
        for name in gate.REQUIRED_SOURCES:
            assert name in found, f"{name!r} not among {sorted(found)}"

    def test_the_required_names_use_forward_slashes(self) -> None:
        for name in gate.REQUIRED_SOURCES:
            assert "\\" not in name, name

    def test_the_paths_would_render_the_same_on_windows(self) -> None:
        """as_posix() is separator independent, which str() is not."""
        from pathlib import PureWindowsPath

        for relative in gate.PYPROJECTS:
            windows = PureWindowsPath(relative)
            assert windows.as_posix() == relative.as_posix()
            # And the thing the old code did, which differs:
            assert str(windows) != relative.as_posix() or "/" not in relative.as_posix()


class TestDisagreement:
    def test_a_mismatched_tag_fails(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert gate.main(["--tag=v99.0.0"]) == 1
        assert "99.0.0" in capsys.readouterr().err

    def test_the_failure_names_every_source_and_its_version(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        gate.main(["--tag=v99.0.0"])
        err = capsys.readouterr().err
        assert "git tag v99.0.0" in err
        assert "HARNESS_VERSION" in err
        assert "release-notes.md" in err

    def test_a_stale_release_heading_is_caught(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The one that was actually wrong, and that nothing else would have noticed."""
        real = gate.declared_versions

        def stale() -> dict[str, str]:
            found = real()
            found[".github/release-notes.md (heading)"] = "0.1.0"
            return found

        monkeypatch.setattr(gate, "declared_versions", stale)
        assert gate.main([]) == 1
        assert "release-notes.md" in capsys.readouterr().err

    def test_a_stale_harness_version_is_caught(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        real = gate.declared_versions

        def stale() -> dict[str, str]:
            found = real()
            key = next(k for k in found if "HARNESS_VERSION" in k)
            found[key] = "0.0.9"
            return found

        monkeypatch.setattr(gate, "declared_versions", stale)
        assert gate.main([]) == 1
        err = capsys.readouterr().err
        assert "0.0.9" in err

    def test_an_unreadable_core_version_is_an_error_not_a_pass(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A gate that silently checks nothing is worse than no gate."""
        monkeypatch.setattr(gate, "declared_versions", dict)
        assert gate.main([]) == 1
        assert "could not read a version" in capsys.readouterr().err


class TestTheTagFromTheEnvironment:
    def test_a_tag_ref_is_used(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
        monkeypatch.setenv("GITHUB_REF_NAME", "v9.9.9")
        assert gate.tag_from_environment() == "v9.9.9"

    def test_a_branch_ref_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The push workflow runs this too, on a branch. It must check consistency only.
        monkeypatch.setenv("GITHUB_REF_TYPE", "branch")
        monkeypatch.setenv("GITHUB_REF_NAME", "main")
        assert gate.tag_from_environment() is None

    def test_no_ref_at_all_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GITHUB_REF_TYPE", raising=False)
        monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
        assert gate.tag_from_environment() is None

    def test_the_branch_case_passes_the_gate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_REF_TYPE", "branch")
        monkeypatch.setenv("GITHUB_REF_NAME", "main")
        assert gate.main([]) == 0

    def test_an_explicit_tag_beats_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
        monkeypatch.setenv("GITHUB_REF_NAME", "v99.0.0")
        version = next(iter(set(gate.declared_versions().values())))
        assert gate.main([f"--tag={version}"]) == 0
