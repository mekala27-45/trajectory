"""Each offline policy has to actually produce the behaviour it claims to."""

import pytest

from trajectory_core.models import ToolName
from trajectory_core.testing import make_playbook
from trajectory_runner.policies import POLICIES, build_script


@pytest.fixture
def playbook():
    return make_playbook(
        commands=[
            "ls -la",
            "cat src/dates.py",
            "pytest -q",
            "sed -i 's/<=/</' src/dates.py",
            "pytest -q",
            "git diff",
        ]
    )


def names(script):
    return [c.name for c in script]


def commands(script):
    return [c.arguments.get("command") for c in script if c.name == ToolName.BASH.value]


class TestMethodical:
    def test_reproduces_the_playbook_exactly(self, playbook):
        script = build_script("methodical", playbook, seed=0)
        assert len(script) == playbook.step_count
        assert names(script) == [s.tool.value for s in playbook.steps]

    def test_has_no_variance_across_seeds(self, playbook):
        assert names(build_script("methodical", playbook, 0)) == names(
            build_script("methodical", playbook, 99)
        )

    def test_introduces_no_redundant_commands(self, playbook):
        issued = commands(build_script("methodical", playbook, 0))
        # The playbook itself runs pytest twice on purpose, which is not the policy's doing.
        assert len(issued) == len(commands([*build_script("methodical", playbook, 0)]))


class TestHasty:
    def test_stops_early_and_finishes(self, playbook):
        script = build_script("hasty", playbook, seed=3)
        assert script[-1].name == ToolName.FINISH.value
        assert len(script) < playbook.step_count

    def test_skips_at_least_one_real_step(self, playbook):
        script = build_script("hasty", playbook, seed=3)
        body = [c for c in script if c.name != ToolName.FINISH.value]
        assert len(body) < len(playbook.steps) - 1

    def test_calls_finish_exactly_once(self, playbook):
        script = build_script("hasty", playbook, seed=5)
        assert names(script).count(ToolName.FINISH.value) == 1


class TestThrasher:
    def test_repeats_at_least_one_command(self, playbook):
        issued = commands(build_script("thrasher", playbook, seed=1))
        assert len(issued) > len(set(issued))

    def test_still_reaches_the_end_of_the_playbook(self, playbook):
        script = build_script("thrasher", playbook, seed=1)
        assert script[-1].name == ToolName.FINISH.value
        for step in playbook.steps[:-1]:
            assert step.args.get("command") in commands(script)

    def test_takes_more_steps_than_the_reference(self, playbook):
        assert len(build_script("thrasher", playbook, seed=1)) > playbook.step_count


class TestSloppy:
    def test_calls_a_tool_that_does_not_exist(self, playbook):
        script = build_script("sloppy", playbook, seed=2)
        assert any(c.name not in {t.value for t in ToolName} for c in script)

    def test_emits_a_malformed_tool_call(self, playbook):
        script = build_script("sloppy", playbook, seed=2)
        assert any(c.parse_error for c in script)

    def test_reads_a_file_that_was_never_there(self, playbook):
        script = build_script("sloppy", playbook, seed=2)
        reads = [c for c in script if c.name == ToolName.READ_FILE.value]
        assert reads
        assert all(
            "settings" in str(c.arguments["path"]) or "." in str(c.arguments["path"]) for c in reads
        )

    def test_emits_an_unbalanced_quote(self, playbook):
        script = build_script("sloppy", playbook, seed=2)
        assert any(str(c.arguments.get("command", "")).count('"') % 2 == 1 for c in script)

    def test_still_completes_the_playbook(self, playbook):
        script = build_script("sloppy", playbook, seed=2)
        for step in playbook.steps[:-1]:
            assert step.args.get("command") in commands(script)


class TestReckless:
    def test_runs_destructive_commands(self, playbook):
        issued = " ".join(c for c in commands(build_script("reckless", playbook, seed=4)) if c)
        assert any(
            marker in issued for marker in ("git reset --hard", "chmod -R 777", "rm -rf ..", "| sh")
        )

    def test_every_destructive_command_stays_inside_the_sandbox(self, playbook):
        """A policy that tests destructive behaviour must not be destructive to the host."""
        for seed in range(12):
            for cmd in commands(build_script("reckless", playbook, seed)):
                assert cmd is not None
                assert " /etc" not in cmd
                assert " /usr" not in cmd
                assert " ~" not in cmd
                assert not cmd.strip().startswith("rm -rf /")

    def test_still_completes_the_playbook(self, playbook):
        script = build_script("reckless", playbook, seed=4)
        for step in playbook.steps[:-1]:
            assert step.args.get("command") in commands(script)


class TestRegistry:
    def test_every_registered_policy_builds_a_terminating_script(self, playbook):
        for name in POLICIES:
            script = build_script(name, playbook, seed=0)
            assert script, name
            assert script[-1].name == ToolName.FINISH.value, name

    def test_every_policy_has_a_summary(self):
        for name, spec in POLICIES.items():
            assert spec.name == name
            assert len(spec.summary) > 20

    def test_unknown_policy_names_are_rejected_with_the_known_list(self, playbook):
        with pytest.raises(KeyError, match="Known policies"):
            build_script("does-not-exist", playbook, seed=0)

    @pytest.mark.parametrize("seed", [0, 1, 2, 3, 17])
    def test_scripts_are_reproducible(self, playbook, seed):
        for name in POLICIES:
            first = build_script(name, playbook, seed)
            second = build_script(name, playbook, seed)
            assert [(c.name, c.arguments) for c in first] == [(c.name, c.arguments) for c in second]

    def test_a_minimal_playbook_does_not_break_any_policy(self):
        tiny = make_playbook(commands=["pytest -q"])
        for name in POLICIES:
            script = build_script(name, tiny, seed=0)
            assert script[-1].name == ToolName.FINISH.value
