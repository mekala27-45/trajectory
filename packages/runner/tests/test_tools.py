"""A malformed tool call is a measurement, never an exception."""

from __future__ import annotations

import pytest

from trajectory_core.models import ToolName
from trajectory_runner.sandbox import LocalSandbox
from trajectory_runner.tools import (
    LIST_DIR_MAX_ENTRIES,
    READ_FILE_MAX_LINES,
    BashArgs,
    dispatch,
    tool_specs,
)

ALL_TOOLS = list(ToolName)


@pytest.fixture
def sandbox(sample_task, task_dir, allow_local):
    with LocalSandbox(sample_task, task_dir) as sb:
        yield sb


def call(sandbox, name, args, **kwargs):
    return dispatch(
        sandbox,
        name,
        args,
        enabled=kwargs.pop("enabled", ALL_TOOLS),
        command_timeout_s=kwargs.pop("command_timeout_s", 20),
        cap_bytes=kwargs.pop("cap_bytes", 8192),
        **kwargs,
    )


class TestSchemas:
    def test_every_tool_is_advertised_with_a_schema(self):
        specs = tool_specs(ALL_TOOLS)
        assert {s.name for s in specs} == {t.value for t in ALL_TOOLS}
        for spec in specs:
            assert spec.parameters["type"] == "object"
            assert spec.parameters["additionalProperties"] is False
            assert len(spec.description) > 40

    def test_schema_comes_from_the_validating_model(self):
        """One definition, so the advertised schema and the validation cannot drift."""
        spec = next(s for s in tool_specs(ALL_TOOLS) if s.name == "bash")
        assert set(spec.parameters["properties"]) == set(BashArgs.model_fields)
        assert spec.parameters["required"] == ["command"]

    def test_narrowing_the_tool_set_narrows_what_is_advertised(self):
        specs = tool_specs([ToolName.BASH, ToolName.FINISH])
        assert {s.name for s in specs} == {"bash", "finish"}


class TestViolations:
    def test_an_unknown_tool_is_a_violation_with_a_helpful_message(self, sandbox):
        result = call(sandbox, "grep_files", {"pattern": "x"})
        assert result.schema_violation is True
        assert "no tool named 'grep_files'" in result.output
        assert "bash" in result.output

    def test_a_parse_error_is_a_violation(self, sandbox):
        result = call(sandbox, "bash", {}, parse_error="Expecting value: line 1 column 14")
        assert result.schema_violation is True
        assert "could not be parsed" in result.output

    def test_missing_arguments_are_a_violation(self, sandbox):
        result = call(sandbox, "bash", {})
        assert result.schema_violation is True
        assert "command" in result.output
        assert "Expected fields: command" in result.output

    def test_unknown_arguments_are_a_violation(self, sandbox):
        result = call(sandbox, "bash", {"command": "ls", "shell": "zsh"})
        assert result.schema_violation is True
        assert "shell" in result.output

    def test_wrong_argument_types_are_a_violation(self, sandbox):
        result = call(sandbox, "write_file", {"path": "a.txt", "content": 42})
        assert result.schema_violation is True

    def test_a_disabled_tool_is_a_violation(self, sandbox):
        result = call(sandbox, "bash", {"command": "ls"}, enabled=[ToolName.FINISH])
        assert result.schema_violation is True
        assert "no tool named 'bash'" in result.output

    def test_violations_never_reach_the_sandbox(self, sandbox):
        before = sandbox.manifest()
        call(sandbox, "write_file", {"path": "x.txt"})
        assert sandbox.manifest().changed_against(before) == set()


class TestBash:
    def test_captures_output_and_exit_code(self, sandbox):
        result = call(sandbox, "bash", {"command": "echo hello; exit 3"})
        assert result.exit_code == 3
        assert "hello" in result.output
        assert result.schema_violation is False

    def test_reports_a_timeout_in_the_output(self, sandbox):
        result = call(sandbox, "bash", {"command": "sleep 20"}, command_timeout_s=1)
        assert "exceeded 1s and was killed" in result.output

    def test_reports_truncation_in_the_output(self, sandbox):
        result = call(
            sandbox, "bash", {"command": "head -c 9000 /dev/zero | tr '\\0' 'a'"}, cap_bytes=128
        )
        assert result.truncated is True
        assert "output truncated at 128 bytes" in result.output

    def test_empty_output_is_labelled_rather_than_blank(self, sandbox):
        assert call(sandbox, "bash", {"command": "true"}).output == "[no output]"

    def test_an_empty_command_is_a_violation(self, sandbox):
        assert call(sandbox, "bash", {"command": ""}).schema_violation is True


class TestFileTools:
    def test_write_then_read(self, sandbox):
        written = call(sandbox, "write_file", {"path": "notes/a.txt", "content": "one\ntwo\n"})
        assert "Wrote" in written.output
        read = call(sandbox, "read_file", {"path": "notes/a.txt"})
        assert read.output == "one\ntwo"

    def test_read_accepts_the_absolute_container_path(self, sandbox):
        """Models guess /workspace constantly. Accepting it is fairer than failing them."""
        call(sandbox, "write_file", {"path": "a.txt", "content": "x"})
        assert call(sandbox, "read_file", {"path": "/workspace/a.txt"}).output == "x"

    def test_read_refuses_to_leave_the_workspace(self, sandbox):
        sandbox.install_verify()
        result = call(sandbox, "read_file", {"path": "../verify/test_app.py"})
        assert result.error is not None
        assert "escapes the workspace" in result.output

    def test_reading_a_missing_file_is_an_error_not_a_crash(self, sandbox):
        result = call(sandbox, "read_file", {"path": "nope.py"})
        assert result.error is not None
        assert result.schema_violation is False

    def test_read_truncates_long_files_and_says_so(self, sandbox):
        body = "\n".join(str(i) for i in range(READ_FILE_MAX_LINES + 50))
        call(sandbox, "write_file", {"path": "big.txt", "content": body})
        result = call(sandbox, "read_file", {"path": "big.txt"}, cap_bytes=1_000_000)
        assert result.truncated is True
        assert "truncated at" in result.output

    def test_empty_files_are_labelled(self, sandbox):
        call(sandbox, "write_file", {"path": "empty.txt", "content": ""})
        assert call(sandbox, "read_file", {"path": "empty.txt"}).output == "[empty file]"

    def test_list_dir_marks_directories(self, sandbox):
        result = call(sandbox, "list_dir", {"path": "."})
        assert "src/" in result.output
        assert "README.md" in result.output

    def test_list_dir_defaults_to_the_working_directory(self, sandbox):
        assert (
            call(sandbox, "list_dir", {}).output == call(sandbox, "list_dir", {"path": "."}).output
        )

    def test_list_dir_caps_very_large_directories(self, sandbox):
        call(
            sandbox,
            "bash",
            {"command": f"mkdir -p many && cd many && touch f{{1..{LIST_DIR_MAX_ENTRIES + 25}}}"},
        )
        result = call(sandbox, "list_dir", {"path": "many"})
        assert result.truncated is True
        assert "more entries not shown" in result.output

    def test_list_dir_labels_an_empty_directory(self, sandbox):
        call(sandbox, "bash", {"command": "mkdir -p hollow"})
        assert call(sandbox, "list_dir", {"path": "hollow"}).output == "[empty directory]"

    def test_listing_a_missing_directory_is_an_error(self, sandbox):
        assert call(sandbox, "list_dir", {"path": "nope"}).error is not None


class TestFinish:
    def test_finish_is_flagged(self, sandbox):
        result = call(sandbox, "finish", {"summary": "Fixed the off by one."})
        assert result.is_finish is True
        assert result.schema_violation is False

    def test_finish_requires_a_summary(self, sandbox):
        assert call(sandbox, "finish", {}).schema_violation is True
        assert call(sandbox, "finish", {"summary": ""}).schema_violation is True
