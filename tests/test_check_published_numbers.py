"""Tests for the published number gate.

The gate exists because two hand-typed figures shipped wrong. A test that only asserts the
current documents pass would have passed before those figures were fixed as well, since
nothing was checking them, so most of what follows corrupts a document on purpose and
asserts the gate notices. Four tests check the tree as committed; the rest break something
deliberately, including one reproduction of each of the two bugs that shipped.
"""

from __future__ import annotations

from pathlib import Path

import check_published_numbers as gate
import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def runs() -> list[gate.Run]:
    """The committed run fixtures, read once for the module."""
    return gate.load_runs()


@pytest.fixture(scope="module")
def claims(runs: list[gate.Run]) -> list[gate.Claim]:
    """Every claim the gate derives from those runs."""
    return gate.build_claims(runs)


class TestTheRepositoryAsCommitted:
    """The documents in the tree have to satisfy the gate."""

    def test_every_claim_holds(self, claims: list[gate.Claim]) -> None:
        failures = gate.unmet(claims)
        assert failures == [], "published figures do not match the run records: " + ", ".join(
            f"{f.label} wants {f.text!r}" for f in failures
        )

    def test_the_gate_checks_a_meaningful_number_of_figures(self, claims: list[gate.Claim]) -> None:
        # A gate that checks three numbers is theatre. This is a floor, not a target: it
        # should fail loudly if someone deletes whole claim groups.
        assert len(claims) >= 50

    def test_both_documents_are_covered(self, claims: list[gate.Claim]) -> None:
        documents = {claim.document for claim in claims}
        assert gate.README in documents
        assert gate.RESULTS in documents

    def test_main_exits_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert gate.main() == 0
        assert "match the" in capsys.readouterr().out


class TestCorruptedDocuments:
    """A wrong figure in a document has to fail the gate."""

    @staticmethod
    def _redirected(
        tmp_path: Path, claims: list[gate.Claim], source: Path, old: str, new: str
    ) -> list[gate.Claim]:
        """Copy `source` with one substitution and repoint every claim against it."""
        text = source.read_text()
        assert old in text, f"anchor {old!r} is no longer in {source.name}"
        target = tmp_path / source.name
        target.write_text(text.replace(old, new, 1))
        return [
            gate.Claim(
                target if claim.document == source else claim.document,
                claim.label,
                claim.text,
                claim.present,
            )
            for claim in claims
        ]

    def test_a_changed_leaderboard_cell_is_caught(
        self, tmp_path: Path, claims: list[gate.Claim]
    ) -> None:
        # The exact shape of the bug that shipped: a metric cell that does not match.
        rebuilt = self._redirected(
            tmp_path, claims, gate.RESULTS, "0.493 +/- 0.000", "0.500 +/- 0.000"
        )
        failures = gate.unmet(rebuilt)
        assert any("redundant action rate" in failure.label for failure in failures)

    def test_a_changed_count_is_caught(self, tmp_path: Path, claims: list[gate.Claim]) -> None:
        rebuilt = self._redirected(
            tmp_path, claims, gate.RESULTS, "| Solved | 155 |", "| Solved | 154 |"
        )
        assert any(failure.label == "solved count" for failure in gate.unmet(rebuilt))

    def test_a_changed_prose_figure_is_caught(
        self, tmp_path: Path, claims: list[gate.Claim]
    ) -> None:
        # The other bug that shipped lived in a sentence, not a table.
        rebuilt = self._redirected(
            tmp_path, claims, gate.README, "fired 213 times", "fired 177 times"
        )
        assert any(failure.label == "hits on passing runs" for failure in gate.unmet(rebuilt))

    def test_reflowed_prose_still_passes(self, tmp_path: Path) -> None:
        # A claim spanning a line break must not fail on formatting. This is the property
        # that keeps the gate from becoming a nuisance that gets disabled.
        document = tmp_path / "doc.md"
        document.write_text("the count is\n213 hits in\ntotal, spread over runs\n")
        claim = gate.Claim(document, "wrapped prose", "the count is 213 hits in total")
        assert gate.unmet([claim]) == []

    def test_an_absent_claim_fails_while_the_text_remains(self, tmp_path: Path) -> None:
        # present=False is how a stale statement is caught, so it needs its own test.
        document = tmp_path / "doc.md"
        document.write_text("context_drift is null on all 180 runs\n")
        stale = gate.Claim(document, "stale judge claim", "null on all 180 runs", present=False)
        assert gate.unmet([stale]) == [stale]

        document.write_text("context_drift was measured on 36 runs\n")
        assert gate.unmet([stale]) == []


class TestClaimDerivation:
    """The claims themselves have to come from the records, not from constants."""

    def test_spend_claim_tracks_the_records(self, runs: list[gate.Run]) -> None:
        # Every fixture run is offline, so the published spend must be an exact zero.
        assert sum(run.total_cost_usd for run in runs) == 0.0
        assert any("0.00 USD" in claim.text for claim in gate.totals_claims(runs))

    def test_a_non_zero_spend_changes_the_claim(self, runs: list[gate.Run]) -> None:
        # Guards the branch that stops the documents claiming a measured zero once real
        # model runs are recorded. Mutating a copy keeps the fixtures untouched.
        spent = runs[0].model_copy(deep=True)
        spent.steps[0].cost_usd = 1.25
        claims = gate.totals_claims([spent, *runs[1:]])
        assert [c.label for c in claims] == ["total spend is no longer zero"]
        assert "1.25 USD" in claims[0].text

    def test_judge_claim_flips_once_the_judge_has_run(self, runs: list[gate.Run]) -> None:
        assert gate.judge_claims(runs)[0].present is True

        judged = runs[0].model_copy(deep=True)
        assert judged.score is not None
        judged.score.context_drift = 0.4
        flipped = gate.judge_claims([judged, *runs[1:]])
        assert flipped[0].present is False

    def test_seed_claims_cover_the_worst_model(self, runs: list[gate.Run]) -> None:
        labels = [claim.label for claim in gate.seed_claims(runs)]
        assert len(labels) == len({run.config.seed for run in runs})
        assert all("stub:hasty" in label for label in labels)
