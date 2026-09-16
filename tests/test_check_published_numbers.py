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


class TestTheFailureModeRowsAreDistinguishable:
    """A weakness the promotion tool exposed, now asserted.

    Three modes shared a count of 36 and a share of 23.2 percent. While a claim was just
    the two numeric cells, `| 36 | 23.2% |`, it matched any of those rows, so the gate
    would have accepted a table with two modes' figures swapped, which is precisely the
    kind of quiet error it exists to catch.
    """

    def test_each_failure_row_claim_pins_its_mode_id(self, claims: list[gate.Claim]) -> None:
        rows = [c for c in claims if " on solved runs" in c.label or " on unsolved runs" in c.label]
        assert rows, "no failure mode row claims found"
        for claim in rows:
            mode_id = claim.label.split()[0]
            assert claim.text.startswith(f"| {mode_id} |"), claim

    def test_two_modes_with_equal_counts_get_different_claims(
        self, claims: list[gate.Claim]
    ) -> None:
        rows = [c for c in claims if " on solved runs" in c.label]
        texts = [c.text for c in rows]
        assert len(set(texts)) == len(texts), "two failure rows render identically"

    def test_a_wrong_number_is_no_longer_masked_by_an_identical_correct_one(
        self, tmp_path: Path, claims: list[gate.Claim]
    ) -> None:
        """The precise bug the full row claim closes.

        F01, F02 and F05 all read `| 36 | 23.2% |`. With the claim being only those two
        cells, corrupting F01's numbers still left the string present on F02's row, so
        F01's claim passed while the document was wrong. The row now carries the id, so
        the corruption has nowhere to hide.
        """
        source = gate.RESULTS
        text = source.read_text()
        rows = [c for c in claims if c.label.endswith("on solved runs") and c.text in text]
        sharing = [c for c in rows if c.text.endswith("| 36 | 23.2% |")]
        assert len(sharing) >= 2, "the fixtures no longer have two rows with equal figures"

        victim = sharing[0]
        corrupted = victim.text.replace("| 36 | 23.2% |", "| 99 | 63.9% |")
        target = tmp_path / source.name
        target.write_text(text.replace(victim.text, corrupted, 1))

        # The old claim, two cells only, is still satisfied by its neighbours' rows.
        old_style = gate.Claim(target, victim.label, "| 36 | 23.2% |", True)
        assert gate.unmet([old_style]) == [], "the old claim should have been fooled"

        # The claim as it is now is not.
        current = gate.Claim(target, victim.label, victim.text, True)
        assert gate.unmet([current]) == [current]


class TestProvenanceIsPinnedToTheRecords:
    """The harness version in the header describes the data, not the checkout.

    A version bump must not change it: the matrix was measured with 0.1.0 and always will
    have been. `check_version.py` therefore ignores this line, which left it the one
    version statement in the repository with no gate at all until 0.1.1 made the
    distinction matter.
    """

    def test_the_recorded_harness_version_is_claimed(self, runs: list[gate.Run]) -> None:
        claims = gate.provenance_claims(runs)
        labels = {c.label for c in claims}
        assert "recorded harness version" in labels
        assert "recorded schema version" in labels
        assert "measurement date" in labels

    def test_the_date_comes_from_the_records(self, runs: list[gate.Run]) -> None:
        """Unguarded until a re-record made it wrong, which is how it was found."""
        earliest = min(run.started_at for run in runs).date()
        claim = next(c for c in gate.provenance_claims(runs) if c.label == "measurement date")
        assert str(earliest.day) in claim.text
        assert earliest.strftime("%B") in claim.text
        assert str(earliest.year) in claim.text

    def test_a_re_recorded_matrix_moves_the_date(self, runs: list[gate.Run]) -> None:
        from datetime import timedelta

        moved = [run.model_copy(deep=True) for run in runs[:4]]
        for run in moved:
            run.started_at = run.started_at + timedelta(days=40)
        before = gate.measured_on(runs)
        after = gate.measured_on(moved)
        assert before != after

    def test_a_single_day_matrix_names_one_date(self, runs: list[gate.Run]) -> None:
        assert " to " not in gate.measured_on(runs)

    def test_a_matrix_spanning_midnight_names_both_ends(self, runs: list[gate.Run]) -> None:
        from datetime import timedelta

        spanning = [run.model_copy(deep=True) for run in runs[:4]]
        spanning[-1].started_at = spanning[-1].started_at + timedelta(days=1)
        text = gate.measured_on(spanning)
        assert " to " in text, text

    def test_it_tracks_the_records_not_the_current_version(self, runs: list[gate.Run]) -> None:
        from trajectory_core.models import HARNESS_VERSION

        recorded = {run.harness_version for run in runs}
        assert len(recorded) == 1
        claim = next(c for c in gate.provenance_claims(runs) if "harness" in c.label)
        assert claim.text == f"with harness `{next(iter(recorded))}`"
        if next(iter(recorded)) != HARNESS_VERSION:
            assert HARNESS_VERSION not in claim.text, (
                "the provenance claim must not drift to the checkout's version"
            )

    def test_a_changed_harness_version_in_the_records_moves_the_claim(
        self, runs: list[gate.Run]
    ) -> None:
        moved = [run.model_copy(deep=True) for run in runs[:4]]
        for run in moved:
            run.harness_version = "9.9.9"
        claim = next(c for c in gate.provenance_claims(moved) if "harness" in c.label)
        assert claim.text == "with harness `9.9.9`"

    def test_a_mixed_set_of_records_is_reported_rather_than_averaged(
        self, runs: list[gate.Run]
    ) -> None:
        mixed = [run.model_copy(deep=True) for run in runs[:4]]
        mixed[0].harness_version = "9.9.9"
        claims = gate.provenance_claims(mixed)
        assert len(claims) == 1
        assert "one harness version" in claims[0].label
        # The claim cannot hold, which is the point: a two harness matrix is not one
        # measurement and must not quietly publish either number.
        assert gate.unmet(claims) == claims


class TestTheLeaderboardIsFullyCovered:
    """The gap the Docker re-record exposed.

    `leaderboard_claims` said it covered "every metric cell" and covered six of nine. The
    mean wall clock column was left describing the previous machine, so its five cells
    summed to the previous total while the total row beside them carried the new one. The
    document contradicted itself and every gate passed.
    """

    def test_every_model_has_a_wall_clock_claim(self, runs: list[gate.Run]) -> None:
        from trajectory_core import aggregate

        models = {row.model for row in aggregate.leaderboard(runs)}
        labelled = {
            c.label.rsplit(" mean wall clock", 1)[0]
            for c in gate.leaderboard_claims(runs)
            if c.label.endswith("mean wall clock")
        }
        assert labelled == models

    def test_every_model_has_a_premature_termination_claim(self, runs: list[gate.Run]) -> None:
        from trajectory_core import aggregate

        models = {row.model for row in aggregate.leaderboard(runs)}
        labelled = {
            c.label.rsplit(" premature termination", 1)[0]
            for c in gate.leaderboard_claims(runs)
            if c.label.endswith("premature termination")
        }
        assert labelled == models

    def test_the_wall_clock_claim_tracks_the_records(self, runs: list[gate.Run]) -> None:
        from trajectory_core import aggregate

        row = aggregate.leaderboard(runs)[0]
        claim = next(
            c for c in gate.leaderboard_claims(runs) if c.label == f"{row.model} mean wall clock"
        )
        assert claim.text == f"{row.mean_wall_clock_s:.1f} s"

    def test_a_stale_wall_clock_column_is_caught(
        self, tmp_path: Path, claims: list[gate.Claim]
    ) -> None:
        """Exactly what happened: the cells describe a machine that did not run this."""
        source = gate.RESULTS
        text = source.read_text()
        claim = next(c for c in claims if c.label.endswith("mean wall clock") and c.text in text)
        target = tmp_path / source.name
        target.write_text(text.replace(claim.text, "99.9 s", 1))
        repointed = gate.Claim(target, claim.label, claim.text, True)
        assert gate.unmet([repointed]) == [repointed]

    def test_the_destructive_column_is_covered_elsewhere(self, runs: list[gate.Run]) -> None:
        """Documented exception. Four of its five cells are `0`, which pins nothing."""
        labels = {c.label for c in gate.leaderboard_claims(runs)}
        assert not any("destructive" in label for label in labels)
        elsewhere = {c.label for c in gate.destructive_claims(runs)}
        assert "destructive command total" in elsewhere


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
