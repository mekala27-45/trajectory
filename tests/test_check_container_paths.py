"""Tests for the container path gate.

The bug it guards against passed `docker build`, passed ruff, passed mypy, passed 593
tests, and shipped. It only appeared when a container was actually started. So most of
these tests rebuild the broken shape on purpose and assert the gate names it, and the
first one pins the shape that actually shipped, copied out of git history.
"""

from __future__ import annotations

from pathlib import Path

import check_container_paths as gate
import pytest

ROOT = Path(__file__).resolve().parent.parent

# The runtime stage exactly as it shipped: WORKDIR /build in the builder, /app here.
AS_SHIPPED = """\
FROM python:3.12.8-slim-bookworm AS builder
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --package trajectory-api

FROM python:3.12.8-slim-bookworm AS runtime
WORKDIR /app
COPY --from=builder --chown=trajectory:trajectory /build/.venv /app/.venv
COPY --from=builder --chown=trajectory:trajectory /build/packages/api/src /app/packages/api/src
USER trajectory
"""

FIXED = """\
FROM python:3.12.8-slim-bookworm AS builder
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --package trajectory-api

FROM python:3.12.8-slim-bookworm AS runtime
WORKDIR /app
COPY --from=builder --chown=trajectory:trajectory /app/.venv /app/.venv
COPY --from=builder --chown=trajectory:trajectory /app/packages/api/src /app/packages/api/src
RUN python -c "import trajectory_api"
USER trajectory
"""


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "Dockerfile"
    path.write_text(text, encoding="utf-8")
    return path


class TestTheRepositoryAsCommitted:
    def test_the_api_dockerfile_passes(self) -> None:
        assert gate.check(ROOT / "packages" / "api" / "Dockerfile") == []

    def test_it_actually_found_the_api_dockerfile(self) -> None:
        # If a refactor moved or renamed it, the gate would silently check nothing.
        names = {p.relative_to(ROOT).as_posix() for p in gate.dockerfiles()}
        assert "packages/api/Dockerfile" in names

    def test_main_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert gate.main() == 0
        assert "consistent across stages" in capsys.readouterr().out


class TestTheShapeThatShipped:
    """Asserted rather than described, because it passed every other gate in the repo."""

    def test_it_is_caught(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        problems = gate.check(write(tmp_path, AS_SHIPPED))
        assert problems, "the gate missed the exact bug it was written for"

    def test_the_message_names_both_paths_and_the_remedy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        joined = " ".join(gate.check(write(tmp_path, AS_SHIPPED)))
        assert "/build/.venv" in joined
        assert "/app/.venv" in joined
        assert "cannot import its own code" in joined

    def test_the_workdir_mismatch_is_reported_separately(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        joined = " ".join(gate.check(write(tmp_path, AS_SHIPPED)))
        assert "WORKDIR" in joined

    def test_the_fixed_shape_is_clean(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        assert gate.check(write(tmp_path, FIXED)) == []


class TestEachRuleIndependently:
    def test_a_moved_package_source_is_caught_even_with_a_shared_workdir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        text = FIXED.replace(
            "/app/packages/api/src /app/packages/api/src",
            "/app/packages/api/src /app/src",
        )
        joined = " ".join(gate.check(write(tmp_path, text)))
        assert "/app/packages/api/src" in joined

    def test_a_missing_import_smoke_test_is_caught(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        text = FIXED.replace('RUN python -c "import trajectory_api"\n', "")
        joined = " ".join(gate.check(write(tmp_path, text)))
        assert "never imports from it at build time" in joined

    def test_a_stage_that_receives_no_venv_needs_no_smoke_test(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        text = """\
FROM alpine:3.21 AS build
WORKDIR /w
RUN echo hi > /w/out.txt

FROM alpine:3.21 AS final
WORKDIR /elsewhere
COPY --from=build /w/out.txt /elsewhere/out.txt
"""
        assert gate.check(write(tmp_path, text)) == []

    def test_a_copy_from_a_registry_image_is_not_a_stage_copy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The uv binary arrives this way. It is not a build stage and has no WORKDIR.
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        text = FIXED.replace(
            "WORKDIR /app\nCOPY pyproject.toml",
            "COPY --from=ghcr.io/astral-sh/uv:0.5.24 /uv /usr/local/bin/uv\n"
            "WORKDIR /app\nCOPY pyproject.toml",
        )
        assert gate.check(write(tmp_path, text)) == []

    def test_a_trailing_slash_is_not_a_difference(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        text = FIXED.replace("/app/.venv /app/.venv", "/app/.venv /app/.venv/")
        assert gate.check(write(tmp_path, text)) == []

    def test_main_exits_one_and_prints_to_stderr(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(gate, "ROOT", tmp_path)
        write(tmp_path, AS_SHIPPED)
        assert gate.main() == 1
        assert "problem(s)" in capsys.readouterr().err


class TestParsing:
    def test_a_backslash_continuation_is_one_instruction(self) -> None:
        text = "FROM alpine:3.21 AS a\nENV X=1 \\\n    Y=2\nWORKDIR /w\n"
        stages = gate.parse_stages(text)
        assert len(stages) == 1
        assert stages[0].workdir == "/w"

    def test_comments_and_blank_lines_are_ignored(self) -> None:
        text = "# a comment\n\nFROM alpine:3.21 AS a\n# another\nWORKDIR /w\n"
        assert gate.parse_stages(text)[0].workdir == "/w"

    def test_an_unnamed_stage_still_gets_an_identity(self) -> None:
        stages = gate.parse_stages("FROM alpine:3.21\nWORKDIR /w\n")
        assert stages[0].name == "stage-0"

    def test_the_last_workdir_in_a_stage_wins(self) -> None:
        text = "FROM alpine:3.21 AS a\nWORKDIR /first\nWORKDIR /second\n"
        assert gate.parse_stages(text)[0].workdir == "/second"

    @pytest.mark.parametrize(
        ("path", "coupled"),
        [
            ("/app/.venv", True),
            ("/app/.venv/", True),
            ("/build/packages/core/src", True),
            ("/build/packages/core/src/", True),
            ("/app/migrations", False),
            ("/app/alembic.ini", False),
            ("/usr/local/bin/uv", False),
        ],
    )
    def test_only_the_paths_the_venv_records_are_coupled(self, path: str, coupled: bool) -> None:
        assert gate.path_is_coupled(path) is coupled
