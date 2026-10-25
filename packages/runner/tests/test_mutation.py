"""Mutation testing: the operators, the sample, the playbook surgery, and a run end to end."""

from __future__ import annotations

import ast

import pytest

from trajectory_core.models import PlaybookStep, ReferencePlaybook, SandboxBackend, ToolName
from trajectory_runner.loader import LoadedTask
from trajectory_runner.mutation import (
    KILLED,
    KILLED_BY_CRASH,
    NOT_RUN,
    SURVIVED,
    UNSTABLE,
    Mutant,
    MutantResult,
    TaskMutation,
    confirm,
    mutants_for,
    mutate_task,
    mutated_playbook,
    python_mutants,
    sample,
    solution_files,
    suite_record,
    text_mutants,
)


def _playbook(*steps: PlaybookStep) -> ReferencePlaybook:
    return ReferencePlaybook(
        task_id="demo-task-01",
        steps=[*steps, PlaybookStep(tool=ToolName.FINISH, args={"summary": "done"})],
    )


def _write(path: str, content: str) -> PlaybookStep:
    return PlaybookStep(tool=ToolName.WRITE_FILE, args={"path": path, "content": content})


def _bash(command: str) -> PlaybookStep:
    return PlaybookStep(tool=ToolName.BASH, args={"command": command})


class TestPythonOperators:
    SOURCE = (
        "def f(a, b, flag=True):\n    if a < b and not flag:\n        return a + 1\n    return b\n"
    )

    def test_each_operator_class_is_found(self):
        found = {(m.operator, m.before, m.after) for m in python_mutants("f.py", self.SOURCE)}
        assert ("compare", "<", "<=") in found
        assert ("logic", "and", "or") in found
        assert ("negation", "not x", "x") in found
        assert ("arithmetic", "+", "-") in found
        assert ("constant", "1", "2") in found
        assert ("boolean", "True", "False") in found

    def test_every_mutant_parses_and_differs_from_the_original_alone(self):
        original = ast.dump(ast.parse(self.SOURCE))
        mutants = python_mutants("f.py", self.SOURCE)
        assert mutants
        dumps = set()
        for mutant in mutants:
            dump = ast.dump(ast.parse(mutant.content))
            assert dump != original
            dumps.add(dump)
        # No change leaks into the next mutant: every one is distinct.
        assert len(dumps) == len(mutants)

    def test_a_file_that_does_not_parse_has_no_mutants(self):
        assert python_mutants("broken.py", "def (:\n") == []


class TestTextOperators:
    def test_strings_and_comments_are_left_alone(self):
        source = 'x := a < b // a < b\ns := "a < b"\n/* a < b */\n'
        mutants = text_mutants("m.go", source)
        assert [m.before for m in mutants if m.operator == "compare"] == ["<"]
        (only,) = [m for m in mutants if m.operator == "compare"]
        assert only.content.startswith("x := a <= b // a < b")

    def test_a_generic_type_argument_is_not_a_comparison(self):
        mutants = text_mutants("m.ts", "const m: Map<string, number> = new Map();\n")
        assert [m for m in mutants if m.operator == "compare"] == []

    def test_sql_keywords_and_nulls(self):
        source = "SELECT 1 FROM t WHERE a IS NULL AND b > 2; -- a OR b\n"
        found = {(m.operator, m.before, m.after) for m in text_mutants("x.sql", source)}
        assert ("logic", "AND", "OR") in found
        assert ("null", "IS NULL", "IS NOT NULL") in found
        assert ("compare", ">", ">=") in found
        assert not any(m.before == "OR" for m in text_mutants("x.sql", source))

    def test_version_pins(self):
        found = {(m.operator, m.before, m.after) for m in text_mutants("r.txt", "core==2.1\n")}
        assert ("pin", "==", "!=") in found
        assert ("version", "2", "3") in found

    def test_code_inside_a_template_literal_is_code(self):
        source = 'const s = `${list.length === 0 ? "none" : list.join(", ")} a < b`;\n'
        found = [(m.operator, m.before, m.after) for m in text_mutants("m.ts", source)]
        assert ("equality", "===", "!==") in found
        assert ("constant", "0", "1") in found
        # The text around the interpolation is still a string.
        assert not any(op == "compare" for op, _, _ in found)

    def test_template_literals_nest(self):
        source = "const s = `outer ${ `inner ${x === 1}` } tail < 2`;\nconst y = a < b;\n"
        mutants = text_mutants("m.ts", source)
        assert {(m.before, m.line) for m in mutants if m.operator == "equality"} == {("===", 1)}
        assert {(m.before, m.line) for m in mutants if m.operator == "compare"} == {("<", 2)}
        for mutant in mutants:
            assert mutant.content.count("`") == 4

    def test_a_brace_inside_an_interpolation_does_not_close_it(self):
        source = "const s = `${f({ a: 1 }) === 2 ? 'x' : 'y'}`;\n"
        found = {(m.operator, m.before) for m in text_mutants("m.ts", source)}
        assert ("equality", "===") in found
        assert ("constant", "2") in found

    def test_a_go_raw_string_has_no_interpolation(self):
        source = "x := `${a == b}`\ny := c == d\n"
        mutants = [m for m in text_mutants("m.go", source) if m.operator == "equality"]
        assert [m.line for m in mutants] == [2]


class TestThePlaybook:
    def test_solution_files_leave_out_tests_and_data(self):
        playbook = _playbook(
            _write("src/app.py", "x = 1\n"),
            _write("tests/test_app.py", "x = 1\n"),
            _write("src/app.test.mjs", "x = 1\n"),
            _write("sample.log", "x = 1\n"),
        )
        assert set(solution_files(playbook)) == {"src/app.py"}

    def test_the_last_write_of_a_file_is_the_one_mutated(self):
        playbook = _playbook(
            _write("src/app.py", "x = 1\n"),
            _bash("python -m pytest -q"),
            _write("src/app.py", "x = 1 + 2\n"),
        )
        mutants = mutants_for(playbook)
        assert {m.step for m in mutants} == {2}
        changed = mutated_playbook(playbook, mutants[0])
        assert changed.steps[0].args["content"] == "x = 1\n"
        assert changed.steps[2].args["content"] == mutants[0].content
        assert playbook.steps[2].args["content"] == "x = 1 + 2\n"

    def test_a_sed_edit_has_its_replacement_mutated(self):
        playbook = _playbook(_bash("sed -i 's/range(n)/range(n + 1)/' src/core.py"))
        mutants = mutants_for(playbook)
        commands = {m.content for m in mutants}
        assert "sed -i 's/range(n)/range(n - 1)/' src/core.py" in commands
        assert "sed -i 's/range(n)/range(n + 2)/' src/core.py" in commands
        assert all(m.field == "command" and m.path == "src/core.py" for m in mutants)

    def test_the_sample_is_fixed_for_a_task(self):
        mutants = python_mutants("f.py", TestPythonOperators.SOURCE)
        first = sample(mutants, 3, seed="demo-task-01")
        assert first == sample(mutants, 3, seed="demo-task-01")
        assert len(first) == 3
        assert sample(mutants, 100, seed="x") == mutants


class TestEndToEnd:
    def test_the_sample_task_kills_its_mutants(self, task_dir, sample_task, allow_local):
        """add(a, b) with its plus flipped, or returned as None, fails the hidden tests."""
        loaded = LoadedTask(
            task=sample_task,
            directory=task_dir,
            playbook=_playbook(_write("src/app.py", "def add(a, b):\n    return a + b\n")),
        )
        report = mutate_task(
            loaded, backend=SandboxBackend.LOCAL, per_task=10, allow_local_override=True
        )
        assert report.control_passed
        assert report.results
        assert {r.outcome for r in report.results} <= {KILLED, KILLED_BY_CRASH, SURVIVED}
        assert report.killed == len(report.results)
        assert report.score == 1.0
        record = suite_record(
            [report],
            suite="core-12",
            backend=SandboxBackend.LOCAL,
            per_task=10,
            harness_version="test",
            generated_at="now",
        )
        assert record["score"] == 1.0
        assert record["scored"] == len(report.results)

    def test_a_task_with_nothing_to_mutate_says_so(self, task_dir, sample_task, allow_local):
        loaded = LoadedTask(task=sample_task, directory=task_dir, playbook=_playbook(_bash("ls")))
        report = mutate_task(
            loaded, backend=SandboxBackend.LOCAL, per_task=10, allow_local_override=True
        )
        assert report.sites == 0
        assert report.score is None
        assert "with commands" in report.control_detail

    def test_a_reference_that_does_not_pass_is_not_scored(self, task_dir, sample_task, allow_local):
        loaded = LoadedTask(
            task=sample_task,
            directory=task_dir,
            playbook=_playbook(_write("src/app.py", "def add(a, b):\n    return a * b\n")),
        )
        report = mutate_task(
            loaded, backend=SandboxBackend.LOCAL, per_task=10, allow_local_override=True
        )
        assert report.control_passed is False
        assert report.results == []
        assert report.score is None


def _mutant() -> Mutant:
    return Mutant(path="f.py", line=1, operator="compare", before="<", after="<=", content="")


class TestAKillHasToHappenTwice:
    def test_a_kill_that_happens_again_stands(self):
        first = MutantResult(_mutant(), KILLED, tests_passed=3, tests_total=4)
        again = MutantResult(_mutant(), KILLED_BY_CRASH)
        assert confirm(result=first, replay=lambda: again) is first

    def test_a_kill_that_does_not_happen_again_is_unstable(self):
        first = MutantResult(_mutant(), KILLED, tests_passed=9, tests_total=10)
        again = MutantResult(_mutant(), SURVIVED, tests_passed=10, tests_total=10)
        result = confirm(result=first, replay=lambda: again)
        assert result.outcome == UNSTABLE
        assert (result.tests_passed, result.tests_total) == (9, 10)
        assert "failed 1 of 10" in result.detail

    def test_a_confirming_replay_that_did_not_finish_leaves_no_verdict(self):
        first = MutantResult(_mutant(), KILLED, tests_passed=1, tests_total=2)
        again = MutantResult(_mutant(), NOT_RUN, detail="SandboxError: no docker")
        result = confirm(result=first, replay=lambda: again)
        assert result.outcome == NOT_RUN
        assert "no docker" in result.detail

    def test_a_survivor_is_not_replayed(self):
        survivor = MutantResult(_mutant(), SURVIVED, tests_passed=2, tests_total=2)

        def replay() -> MutantResult:
            raise AssertionError("a survivor needs no second look")

        assert confirm(result=survivor, replay=replay) is survivor

    def test_an_unstable_mutant_is_scored_and_not_killed(self):
        report = TaskMutation(
            task_id="t",
            results=[
                MutantResult(_mutant(), KILLED),
                MutantResult(_mutant(), UNSTABLE),
                MutantResult(_mutant(), SURVIVED),
                MutantResult(_mutant(), NOT_RUN),
            ],
        )
        assert (report.killed, report.unstable, report.survived) == (1, 1, 1)
        assert report.scored == 3
        assert report.score == round(1 / 3, 6)


@pytest.mark.parametrize("count", [0, 1])
def test_sample_of_small_lists(count: int):
    mutants = python_mutants("f.py", "x = 1\n")[:count]
    assert sample(mutants, 5, seed="s") == mutants
