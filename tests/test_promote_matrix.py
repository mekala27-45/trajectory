"""Tests for the matrix promotion tool.

This script edits two published documents and replaces the committed run fixtures, so the
risk is not that it fails loudly, it is that it succeeds wrongly. The tests that matter are
the ones checking it declines to guess: a figure it cannot locate, a figure that appears
twice, and prose whose meaning changed rather than whose value did.
"""

from __future__ import annotations

from pathlib import Path

import check_published_numbers as gate
import promote_matrix as promote
import pytest

from trajectory_core.models import Run, SandboxBackend

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def runs() -> list[Run]:
    return gate.load_runs()


def claim(document: Path, label: str, text: str) -> gate.Claim:
    return gate.Claim(document, label, text, True)


class TestFlexibleMatching:
    def test_it_matches_across_a_line_break(self) -> None:
        pattern = promote.flexible_pattern("213 hits spread over 106")
        assert pattern.search("...fired **213 hits** spread\nover 106 of the...") is None
        assert pattern.search("...213 hits spread\nover 106 of...") is not None

    def test_it_matches_a_single_line_normally(self) -> None:
        assert promote.flexible_pattern("| Runs | 180 |").search("| Runs | 180 |")

    def test_regex_characters_in_a_figure_are_literal(self) -> None:
        # Claims are full of pipes, brackets, plus and dots. None are operators here.
        text = "100.0% +/- 0.0% | `stub:hasty` [x]"
        assert promote.flexible_pattern(text).search(text)


class TestApplyEdits:
    def _doc(self, tmp_path: Path, body: str) -> Path:
        path = tmp_path / "DOC.md"
        path.write_text(body, encoding="utf-8")
        return path

    def test_an_unchanged_figure_is_left_alone(self, tmp_path: Path) -> None:
        doc = self._doc(tmp_path, "| Runs | 180 |\n")
        before = [claim(doc, "run count", "| Runs | 180 |")]
        edits = promote.apply_edits(before, list(before))
        assert edits == []
        assert doc.read_text() == "| Runs | 180 |\n"

    def test_a_moved_figure_is_replaced(self, tmp_path: Path) -> None:
        doc = self._doc(tmp_path, "intro\n| Runs | 180 |\noutro\n")
        before = [claim(doc, "run count", "| Runs | 180 |")]
        after = [claim(doc, "run count", "| Runs | 177 |")]
        edits = promote.apply_edits(before, after)
        assert [e.outcome for e in edits] == [promote.REPLACED]
        assert "| Runs | 177 |" in doc.read_text()
        assert "180" not in doc.read_text()

    def test_a_figure_wrapped_across_lines_is_replaced_and_flagged(self, tmp_path: Path) -> None:
        doc = self._doc(tmp_path, "the count was 106 of the\n155 solved runs, which is\n")
        before = [claim(doc, "carrying a hit", "106 of the 155")]
        after = [claim(doc, "carrying a hit", "106 of the 152")]
        edits = promote.apply_edits(before, after)
        assert [e.outcome for e in edits] == [promote.REFLOWED]
        assert "106 of the 152" in doc.read_text()

    def test_a_figure_appearing_twice_is_refused(self, tmp_path: Path) -> None:
        doc = self._doc(tmp_path, "| F01 | 36 |\n| F02 | 36 |\n")
        before = [claim(doc, "F01", "| 36 |")]
        after = [claim(doc, "F01", "| 99 |")]
        edits = promote.apply_edits(before, after)
        assert [e.outcome for e in edits] == [promote.AMBIGUOUS]
        assert "99" not in doc.read_text(), "an ambiguous figure must not be guessed at"

    def test_a_figure_that_is_not_there_is_reported(self, tmp_path: Path) -> None:
        doc = self._doc(tmp_path, "nothing relevant here\n")
        before = [claim(doc, "run count", "| Runs | 180 |")]
        after = [claim(doc, "run count", "| Runs | 177 |")]
        edits = promote.apply_edits(before, after)
        assert [e.outcome for e in edits] == [promote.NOT_FOUND]

    def test_a_claim_that_must_be_absent_is_never_written(self, tmp_path: Path) -> None:
        # `present=False` means "this text must be gone". Inserting it would be backwards.
        doc = self._doc(tmp_path, "clean\n")
        before = [gate.Claim(doc, "stale note", "is `null` on all 180 runs", False)]
        after = [gate.Claim(doc, "stale note", "is `null` on all 177 runs", False)]
        assert promote.apply_edits(before, after) == []
        assert doc.read_text() == "clean\n"

    def test_a_document_with_no_edits_is_not_rewritten(self, tmp_path: Path) -> None:
        doc = self._doc(tmp_path, "| Runs | 180 |\n")
        mtime = doc.stat().st_mtime_ns
        promote.apply_edits(
            [claim(doc, "x", "| Runs | 180 |")], [claim(doc, "x", "| Runs | 180 |")]
        )
        assert doc.stat().st_mtime_ns == mtime

    def test_two_figures_in_one_document_are_both_replaced(self, tmp_path: Path) -> None:
        doc = self._doc(tmp_path, "| Runs | 180 |\n| Solved | 155 |\n")
        before = [claim(doc, "a", "| Runs | 180 |"), claim(doc, "b", "| Solved | 155 |")]
        after = [claim(doc, "a", "| Runs | 177 |"), claim(doc, "b", "| Solved | 152 |")]
        edits = promote.apply_edits(before, after)
        assert all(e.outcome == promote.REPLACED for e in edits)
        assert doc.read_text() == "| Runs | 177 |\n| Solved | 152 |\n"


class TestBackendProse:
    def test_no_advisory_when_the_backend_is_unchanged(self, runs: list[Run]) -> None:
        assert promote.backend_prose_to_review(runs, runs) == []

    def test_a_backend_change_lists_the_prose_that_still_says_local(self, runs: list[Run]) -> None:
        moved = [run.model_copy(deep=True) for run in runs[:5]]
        for run in moved:
            run.config.sandbox_backend = SandboxBackend.DOCKER
        lines = promote.backend_prose_to_review(runs, moved)
        assert lines, "re-recording on Docker must flag the prose about the local backend"
        assert any("RESULTS.md" in line for line in lines)
        assert any("README.md" in line for line in lines)

    def test_the_advisory_names_files_and_line_numbers(self, runs: list[Run]) -> None:
        moved = [run.model_copy(deep=True) for run in runs[:1]]
        moved[0].config.sandbox_backend = SandboxBackend.DOCKER
        for line in promote.backend_prose_to_review(runs, moved):
            head = line.strip().split(":", 2)
            assert head[0].endswith(".md")
            assert head[1].isdigit()


class TestLoadingRuns:
    def test_an_empty_directory_says_what_to_point_at(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="runs/ subdirectory"):
            promote.load_new_runs(tmp_path)

    def test_a_flat_directory_of_records_is_accepted(self) -> None:
        # The fixtures directory has no runs/ subdirectory, and is a valid input.
        assert len(promote.load_new_runs(gate.FIXTURES)) == 180

    def test_a_runs_subdirectory_is_preferred(self, tmp_path: Path) -> None:
        (tmp_path / "runs").mkdir()
        source = sorted(gate.FIXTURES.glob("*.json"))[0]
        (tmp_path / "runs" / source.name).write_text(source.read_text(), encoding="utf-8")
        # A decoy at the top level must be ignored in favour of runs/.
        (tmp_path / "ignored.json").write_text(source.read_text(), encoding="utf-8")
        assert len(promote.load_new_runs(tmp_path)) == 1


class TestTheRepositoryAsCommitted:
    def test_promoting_the_current_fixtures_changes_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The identity case. If this moves a figure, the tool is not idempotent."""
        assert promote.main([str(gate.FIXTURES), "--dry-run"]) == 0
        assert "0 of 64 published figures would change" in capsys.readouterr().out

    def test_the_paths_it_guards_include_both_documents(self) -> None:
        assert "README.md" in promote.TOUCHED
        assert "RESULTS.md" in promote.TOUCHED
        assert "fixtures/recorded-runs" in promote.TOUCHED
