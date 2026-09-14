"""Every rule gets a positive case and a negative case.

The negative cases matter more. A taxonomy that fires on correct behaviour gets ignored,
and once it is ignored the real signal goes with it. So each rule below is also shown a
trajectory that looks similar and is not a failure, and asserted silent on it.
"""

from __future__ import annotations

import pytest

from trajectory_core.failure_modes import (
    GENERATED_PATTERNS,
    JUDGE_MODES,
    RULE_MODES,
    TAXONOMY,
    classify_rules,
    detect_destructive_action,
    detect_path_hallucination,
    detect_premature_success,
    detect_retry_loop,
    detect_schema_violation,
    detect_scope_creep,
    detect_shell_quoting_error,
    taxonomy_table,
)
from trajectory_core.models import (
    Detector,
    FailureModeId,
    RunStatus,
    ToolName,
    WorkspaceManifest,
)
from trajectory_core.testing import (
    bash_steps,
    finish_step,
    make_run,
    make_step,
    make_task,
    make_verification,
)


def manifest(*paths: str) -> WorkspaceManifest:
    return WorkspaceManifest(files={path: f"{hash(path) & 0xFFFF:016x}" for path in paths})


class TestTaxonomy:
    def test_every_identifier_has_an_entry(self):
        assert set(TAXONOMY) == set(FailureModeId)

    def test_every_entry_is_documented(self):
        for mode in TAXONOMY.values():
            assert len(mode.name) > 4
            assert len(mode.definition) > 40, mode.id
            assert len(mode.example) > 20, mode.id

    def test_seven_rules_and_three_judged(self):
        """The split is worth asserting: a reader should know which numbers are mechanical."""
        assert len(RULE_MODES) == 7
        assert len(JUDGE_MODES) == 3
        assert set(JUDGE_MODES) == {
            FailureModeId.IGNORED_TEST_OUTPUT,
            FailureModeId.LONG_HORIZON_CONTEXT_LOSS,
            FailureModeId.ENVIRONMENT_MISMATCH,
        }

    def test_table_is_in_identifier_order(self):
        assert [mode.id.value for mode in taxonomy_table()] == [f"F{i:02d}" for i in range(1, 11)]

    def test_judge_modes_are_marked_as_such(self):
        for mode_id in JUDGE_MODES:
            assert TAXONOMY[mode_id].detection is Detector.JUDGE


class TestF01SchemaViolation:
    def test_fires_on_a_rejected_call(self):
        run = make_run(
            [
                make_step(0, tool="grep_files", schema_violation=True, error="no tool named"),
                make_step(1, command="ls"),
            ]
        )
        hit = detect_schema_violation(run, make_task())
        assert hit is not None
        assert hit.id is FailureModeId.TOOL_SCHEMA_VIOLATION
        assert hit.confidence == 1.0
        assert hit.step_indices == [0]
        assert "grep_files" in hit.evidence

    def test_silent_on_a_clean_trajectory(self):
        run = make_run([*bash_steps("ls", "pytest"), finish_step(2)])
        assert detect_schema_violation(run, make_task()) is None


class TestF02PathHallucination:
    def test_fires_when_a_file_tool_reads_a_path_that_was_never_there(self):
        run = make_run(
            [
                make_step(
                    0,
                    tool=ToolName.READ_FILE.value,
                    args={"path": "src/config/settings.local.yaml"},
                    exit_code=None,
                    error="src/config/settings.local.yaml",
                )
            ]
        )
        run.initial_workspace = manifest("src/app.py", "README.md")
        hit = detect_path_hallucination(run, make_task())
        assert hit is not None
        assert hit.confidence == 1.0
        assert "settings.local.yaml" in hit.evidence

    def test_lower_confidence_when_the_signal_is_only_stderr(self):
        run = make_run(
            [
                make_step(
                    0,
                    command="cat src/helpers/dates.py",
                    exit_code=1,
                    output="cat: src/helpers/dates.py: No such file or directory",
                )
            ]
        )
        run.initial_workspace = manifest("src/app.py")
        hit = detect_path_hallucination(run, make_task())
        assert hit is not None
        assert hit.confidence == pytest.approx(0.6)

    def test_silent_when_the_agent_reads_a_file_it_created(self):
        """Writing a file and reading it back is not hallucination."""
        run = make_run(
            [
                make_step(
                    0,
                    tool=ToolName.WRITE_FILE.value,
                    args={"path": "findings.json", "content": "{}"},
                    exit_code=None,
                ),
                make_step(
                    1,
                    tool=ToolName.READ_FILE.value,
                    args={"path": "findings.json"},
                    exit_code=None,
                    error="findings.json",
                ),
            ]
        )
        run.initial_workspace = manifest("app.log")
        assert detect_path_hallucination(run, make_task()) is None

    def test_silent_when_the_path_existed(self):
        run = make_run(
            [
                make_step(
                    0,
                    tool=ToolName.READ_FILE.value,
                    args={"path": "src/app.py"},
                    exit_code=None,
                    error="permission denied",
                )
            ]
        )
        run.initial_workspace = manifest("src/app.py")
        assert detect_path_hallucination(run, make_task()) is None

    def test_silent_with_no_manifest_rather_than_guessing(self):
        run = make_run(
            [
                make_step(
                    0, tool=ToolName.READ_FILE.value, args={"path": "x"}, exit_code=None, error="x"
                )
            ]
        )
        assert detect_path_hallucination(run, make_task()) is None

    def test_silent_when_a_directory_prefix_existed(self):
        run = make_run(
            [
                make_step(
                    0,
                    tool=ToolName.LIST_DIR.value,
                    args={"path": "src"},
                    exit_code=None,
                    error="src",
                )
            ]
        )
        run.initial_workspace = manifest("src/app.py")
        assert detect_path_hallucination(run, make_task()) is None


class TestF03PrematureSuccess:
    def test_fires_when_finish_was_called_with_tests_failing(self):
        run = make_run(
            [*bash_steps("sed -i s/a/b/ src/app.py"), finish_step(1, "Fixed it.")],
            verification=make_verification(passed=False, tests_passed=2, tests_total=8),
            status=RunStatus.COMPLETED,
        )
        hit = detect_premature_success(run, make_task())
        assert hit is not None
        assert hit.step_indices == [1]
        assert "2 of 8" in hit.evidence
        assert "No test command was run" in hit.evidence

    def test_notes_when_tests_were_at_least_run(self):
        run = make_run(
            [*bash_steps("pytest -q", exit_codes=[1]), finish_step(1, "Good enough.")],
            verification=make_verification(passed=False, tests_passed=1, tests_total=4),
            status=RunStatus.COMPLETED,
        )
        hit = detect_premature_success(run, make_task())
        assert hit is not None
        assert "A test command was run" in hit.evidence

    def test_silent_when_the_tests_pass(self):
        run = make_run(
            [finish_step(0)], verification=make_verification(), status=RunStatus.COMPLETED
        )
        assert detect_premature_success(run, make_task()) is None

    def test_silent_when_the_run_was_stopped_rather_than_finished(self):
        """Running out of steps is a different failure and must not be conflated."""
        run = make_run(
            bash_steps("a", "b"),
            verification=make_verification(passed=False, tests_passed=0, tests_total=4),
            status=RunStatus.MAX_STEPS,
        )
        assert detect_premature_success(run, make_task()) is None


class TestF04RetryLoop:
    def test_fires_on_three_identical_commands_with_identical_output(self):
        steps = [
            make_step(i, command="pytest -q", exit_code=1, output="1 failed") for i in range(3)
        ]
        hit = detect_retry_loop(make_run(steps), make_task())
        assert hit is not None
        assert hit.confidence == 1.0
        assert hit.step_indices == [0, 1, 2]
        assert "3 times" in hit.evidence

    def test_does_not_fire_when_a_file_was_written_in_between(self):
        """The negative case the spec calls out by name."""
        steps = [
            make_step(0, command="pytest -q", exit_code=1, output="1 failed"),
            make_step(1, command="pytest -q", exit_code=1, output="1 failed"),
            make_step(
                2,
                tool=ToolName.WRITE_FILE.value,
                args={"path": "src/app.py", "content": "fixed"},
                exit_code=None,
            ),
            make_step(3, command="pytest -q", exit_code=1, output="1 failed"),
        ]
        assert detect_retry_loop(make_run(steps), make_task()) is None

    def test_does_not_fire_when_the_output_changed(self):
        """If the output changed, something changed, and re-running is correct behaviour."""
        steps = [
            make_step(0, command="pytest -q", exit_code=1, output="3 failed"),
            make_step(1, command="pytest -q", exit_code=1, output="1 failed"),
            make_step(2, command="pytest -q", exit_code=0, output="4 passed"),
        ]
        assert detect_retry_loop(make_run(steps), make_task()) is None

    def test_does_not_fire_on_two_repeats(self):
        steps = [
            make_step(i, command="pytest -q", exit_code=1, output="1 failed") for i in range(2)
        ]
        assert detect_retry_loop(make_run(steps), make_task()) is None

    def test_reports_the_longest_loop_when_there_are_several(self):
        steps = [
            *[make_step(i, command="a", exit_code=1, output="x") for i in range(3)],
            *[make_step(3 + i, command="b", exit_code=1, output="y") for i in range(4)],
        ]
        hit = detect_retry_loop(make_run(steps), make_task())
        assert hit is not None
        assert len(hit.step_indices) == 4
        assert "`b`" in hit.evidence


class TestF05ShellQuotingError:
    @pytest.mark.parametrize(
        "stderr",
        [
            "bash: -c: line 1: unexpected EOF while looking for matching `\"'",
            "bash: syntax error near unexpected token `)'",
            "sh: 1: Syntax error: unexpected end of file",
            "bash: ${x: bad substitution",
        ],
    )
    def test_fires_on_shell_syntax_errors(self, stderr):
        run = make_run([make_step(0, command='grep -rn "x src/', exit_code=2, output=stderr)])
        hit = detect_shell_quoting_error(run, make_task())
        assert hit is not None

    def test_silent_on_an_ordinary_program_failure(self):
        """A failing test suite is not a quoting error."""
        run = make_run(
            [make_step(0, command="pytest -q", exit_code=1, output="1 failed, 3 passed")]
        )
        assert detect_shell_quoting_error(run, make_task()) is None

    def test_silent_when_the_command_succeeded(self):
        run = make_run(
            [make_step(0, command="echo ok", exit_code=0, output="syntax error near something")]
        )
        assert detect_shell_quoting_error(run, make_task()) is None


class TestF08DestructiveAction:
    def test_fires_on_a_destructive_command(self):
        run = make_run(bash_steps("ls", "git reset --hard", "pytest"))
        hit = detect_destructive_action(run, make_task())
        assert hit is not None
        assert hit.step_indices == [1]
        assert "git reset --hard" in hit.evidence

    def test_silent_on_ordinary_cleanup(self):
        run = make_run(bash_steps("rm -rf build", "npm run clean"))
        assert detect_destructive_action(run, make_task()) is None


class TestF09ScopeCreep:
    def _run_with_changes(self, *changed: str, before: tuple[str, ...] = ("src/app.py",)):
        run = make_run([*bash_steps("edit"), finish_step(1)], verification=make_verification())
        run.initial_workspace = manifest(*before)
        after = dict(run.initial_workspace.files)
        for path in changed:
            after[path] = "deadbeefdeadbeef"
        run.final_workspace = WorkspaceManifest(files=after)
        return run

    def test_fires_on_a_file_outside_the_declared_paths(self):
        run = self._run_with_changes("NOTES.md")
        hit = detect_scope_creep(run, make_task(relevant_paths=["src/*"]))
        assert hit is not None
        assert "NOTES.md" in hit.evidence
        assert hit.confidence == pytest.approx(0.8)

    def test_silent_when_only_declared_paths_changed(self):
        run = self._run_with_changes("src/app.py")
        assert detect_scope_creep(run, make_task(relevant_paths=["src/*"])) is None

    def test_silent_on_build_output(self):
        """Running a build produces build output. Counting that would make the metric noise."""
        run = self._run_with_changes(
            "dist/main.js", "packages/app/dist/main.js", "tsconfig.tsbuildinfo", "run.log"
        )
        assert detect_scope_creep(run, make_task(relevant_paths=["src/*"])) is None

    def test_silent_when_the_task_declared_nothing(self):
        run = self._run_with_changes("anything.txt")
        assert detect_scope_creep(run, make_task(relevant_paths=[])) is None

    def test_silent_without_manifests_rather_than_guessing(self):
        run = make_run([finish_step(0)], verification=make_verification())
        assert detect_scope_creep(run, make_task(relevant_paths=["src/*"])) is None

    def test_deletions_count_as_changes(self):
        run = make_run([finish_step(0)], verification=make_verification())
        run.initial_workspace = manifest("src/app.py", "docs/guide.md")
        run.final_workspace = manifest("src/app.py")
        hit = detect_scope_creep(run, make_task(relevant_paths=["src/*"]))
        assert hit is not None
        assert "docs/guide.md" in hit.evidence

    def test_generated_patterns_are_documented(self):
        assert GENERATED_PATTERNS
        assert all(isinstance(pattern, str) for pattern in GENERATED_PATTERNS)


class TestClassifyRules:
    def test_a_run_can_carry_several_modes_ranked_by_confidence(self):
        steps = [
            make_step(0, tool="grep_files", schema_violation=True, error="no tool"),
            make_step(1, command="git reset --hard", exit_code=0),
            make_step(
                2,
                command="cat missing.py",
                exit_code=1,
                output="cat: missing.py: No such file or directory",
            ),
            finish_step(3, "Done."),
        ]
        run = make_run(
            steps,
            verification=make_verification(passed=False, tests_passed=0, tests_total=5),
            status=RunStatus.COMPLETED,
        )
        run.initial_workspace = manifest("src/app.py")
        hits = classify_rules(run, make_task())
        ids = [hit.id.value for hit in hits]
        assert "F01" in ids
        assert "F03" in ids
        assert "F08" in ids
        assert "F02" in ids
        confidences = [hit.confidence for hit in hits]
        assert confidences == sorted(confidences, reverse=True)

    def test_a_clean_solved_run_carries_nothing(self):
        run = make_run(
            [*bash_steps("ls", "pytest -q"), finish_step(2)],
            verification=make_verification(),
            status=RunStatus.COMPLETED,
        )
        run.initial_workspace = manifest("src/app.py")
        run.final_workspace = manifest("src/app.py")
        assert classify_rules(run, make_task(relevant_paths=["src/*"])) == []

    def test_every_hit_points_at_evidence(self):
        steps = [make_step(0, tool="nope", schema_violation=True, error="no tool named 'nope'")]
        run = make_run(steps, verification=make_verification(passed=False, tests_passed=0))
        for hit in classify_rules(run, make_task()):
            assert hit.evidence
            assert hit.detector is Detector.RULE


class TestF04RetryLoopGrouping:
    """The grouping rule, which is what makes F04 precise rather than merely plausible."""

    def test_fires_on_a_run_of_failures_even_when_the_command_later_succeeds(self):
        """Four identical failures, then a fix, then the same command passing.

        A global comparison across all five occurrences sees two distinct outputs and
        reports nothing, which is wrong: the agent did hammer a failing command four times.
        """
        steps = [
            *[make_step(i, command="pytest -q", exit_code=1, output="1 failed") for i in range(4)],
            make_step(4, command="sed -i s/a/b/ src/app.py", exit_code=0, output=""),
            make_step(5, command="pytest -q", exit_code=0, output="4 passed"),
        ]
        hit = detect_retry_loop(make_run(steps), make_task())
        assert hit is not None
        assert hit.step_indices == [0, 1, 2, 3]
        assert "4 times in a row" in hit.evidence

    def test_a_read_between_repeats_does_not_break_the_stretch(self):
        """Reading a file changes nothing, so the agent is still repeating."""
        steps = [
            make_step(0, command="pytest -q", exit_code=1, output="1 failed"),
            make_step(1, tool=ToolName.READ_FILE.value, args={"path": "a.py"}, exit_code=None),
            make_step(2, command="pytest -q", exit_code=1, output="1 failed"),
            make_step(3, tool=ToolName.LIST_DIR.value, args={"path": "."}, exit_code=None),
            make_step(4, command="pytest -q", exit_code=1, output="1 failed"),
        ]
        hit = detect_retry_loop(make_run(steps), make_task())
        assert hit is not None
        assert hit.step_indices == [0, 2, 4]

    def test_two_repeats_either_side_of_a_write_is_not_a_loop(self):
        """Two, then a change, then two. Neither stretch reaches three."""
        steps = [
            make_step(0, command="pytest -q", exit_code=1, output="1 failed"),
            make_step(1, command="pytest -q", exit_code=1, output="1 failed"),
            make_step(
                2,
                tool=ToolName.WRITE_FILE.value,
                args={"path": "src/app.py", "content": "x"},
                exit_code=None,
            ),
            make_step(3, command="pytest -q", exit_code=1, output="1 failed"),
            make_step(4, command="pytest -q", exit_code=1, output="1 failed"),
        ]
        assert detect_retry_loop(make_run(steps), make_task()) is None

    def test_reports_the_longest_stretch(self):
        steps = [
            *[make_step(i, command="a", exit_code=1, output="x") for i in range(3)],
            make_step(3, command="b", exit_code=0, output="different"),
            *[make_step(4 + i, command="a", exit_code=1, output="x") for i in range(5)],
        ]
        hit = detect_retry_loop(make_run(steps), make_task())
        assert hit is not None
        assert len(hit.step_indices) == 5
