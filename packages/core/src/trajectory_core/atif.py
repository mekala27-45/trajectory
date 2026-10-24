"""ATIF, the Agent Trajectory Interchange Format, in both directions.

ATIF is the trajectory format Harbor reads and writes, and the one Claude Code, Codex,
mini-swe-agent and OpenHands emit when they run under it. Speaking it both ways turns this
harness from a silo into an analysis layer over other people's agent runs. A run recorded
here exports to ATIF that any ATIF tool can read, and an ATIF trajectory from any agent
imports and scores with the same ten metrics and eleven failure modes as a native run.

Written against Harbor's own pydantic models, and two of their rules shape this module.
Every model forbids undeclared keys, so everything specific to this harness travels in the
`extra` objects ATIF provides for exactly that. And an observation may refer only to tool
calls made in the same step, so each native step, which is one tool call and its output,
becomes one ATIF agent step that holds both.

One limit is structural. ATIF has no workspace snapshot, and three rules, F02 path
hallucination, F09 scope creep and F11 grader tampering, compare the workspace before and
after a run. This harness's own export carries both manifests in `extra`, so its round trip
is lossless. A foreign trajectory has none, so those three rules stay silent on it rather
than guess, and every other metric and mode applies in full.

An imported trajectory is scored, not certified. It carries an `atif:` model prefix the way
offline policies carry `stub:`, and it is stamped with the local backend, which keeps it off
every leaderboard: a trajectory recorded on someone else's machine is evidence about an
agent's process, not a measurement this harness made.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from trajectory_core.failure_modes import classify_rules
from trajectory_core.models import (
    Run,
    RunConfig,
    RunnerFingerprint,
    RunStatus,
    SandboxBackend,
    Step,
    Task,
    ToolName,
    Verification,
    WorkspaceManifest,
)
from trajectory_core.scoring import rank_failure_modes, score

ATIF_VERSION = "ATIF-v1.7"
"""The newest version every Harbor release from 1.7 on accepts."""

ACCEPTED_VERSIONS = frozenset(f"ATIF-v1.{minor}" for minor in range(9))

AGENT_NAME = "trajectory"
IMPORT_PREFIX = "atif:"

AGENT_ONLY_FIELDS = ("model_name", "reasoning_effort", "reasoning_content", "tool_calls", "metrics")

TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "trajectory_id",
        "agent",
        "steps",
        "notes",
        "final_metrics",
        "continued_trajectory_ref",
        "subagent_trajectories",
        "extra",
    }
)

TOOL_ALIASES: dict[str, str] = {
    "bash": "bash",
    "shell": "bash",
    "execute_bash": "bash",
    "run_command": "bash",
    "terminal": "bash",
    "read": "read_file",
    "read_file": "read_file",
    "view": "read_file",
    "write": "write_file",
    "write_file": "write_file",
    "create": "write_file",
    "edit": "write_file",
    "multiedit": "write_file",
    "str_replace_editor": "write_file",
    "ls": "list_dir",
    "list_dir": "list_dir",
    "glob": "list_dir",
    "finish": "finish",
    "submit": "finish",
    "task_complete": "finish",
}
"""Other agents' tool names, mapped to this harness's.

The command based metrics, redundancy, retry loops, destructive actions and quoting errors,
read `bash` steps and their `command` argument. Claude Code calls the same tool `Bash` and
OpenHands calls it `execute_bash`. Without this map an imported trajectory would score as if
it had never run a command. Unknown names are kept, lowercased, rather than guessed at."""

PATH_ARGUMENT_ALIASES = ("file_path", "filepath", "filename")


class AtifError(ValueError):
    """Raised when a document is not a valid ATIF trajectory."""


# --------------------------------------------------------------------- validation


def validate(doc: dict[str, Any]) -> None:
    """Check the rules Harbor's own models enforce, without depending on Harbor.

    Sequential step ids from 1, a known source on every step, agent-only fields only on
    agent steps, every observation result pointing at a tool call in the same step, and no
    undeclared top-level keys. A document that passes this is one Harbor will load.

    Raises:
        AtifError: Naming the first rule the document breaks.
    """
    if not isinstance(doc, dict):
        raise AtifError("an ATIF trajectory is a JSON object")
    unknown = sorted(set(doc) - TOP_LEVEL_FIELDS)
    if unknown:
        raise AtifError(f"undeclared top-level field(s): {', '.join(unknown)}; use extra")
    if doc.get("schema_version") not in ACCEPTED_VERSIONS:
        raise AtifError(f"unsupported schema_version {doc.get('schema_version')!r}")
    agent = doc.get("agent")
    if not isinstance(agent, dict) or not agent.get("name") or not agent.get("version"):
        raise AtifError("agent must carry a name and a version")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise AtifError("steps must be a non-empty list")
    for position, step in enumerate(steps):
        _validate_step(step, position)


def _validate_step(step: Any, position: int) -> None:  # noqa: ANN401  untrusted JSON
    if not isinstance(step, dict):
        raise AtifError(f"steps[{position}] is not an object")
    if step.get("step_id") != position + 1:
        raise AtifError(
            f"steps[{position}].step_id: expected {position + 1} (sequential from 1), "
            f"got {step.get('step_id')!r}"
        )
    source = step.get("source")
    if source not in ("system", "user", "agent"):
        raise AtifError(f"steps[{position}].source must be system, user or agent")
    if "message" not in step:
        raise AtifError(f"steps[{position}] has no message")
    if source != "agent":
        for field in AGENT_ONLY_FIELDS:
            if step.get(field) is not None:
                raise AtifError(f"steps[{position}].{field} is only valid on agent steps")
    call_ids = {call.get("tool_call_id") for call in step.get("tool_calls") or []}
    for result in (step.get("observation") or {}).get("results", []):
        referenced = result.get("source_call_id")
        if referenced is not None and referenced not in call_ids:
            raise AtifError(
                f"steps[{position}] observation refers to {referenced!r}, which is not one "
                "of that step's tool calls"
            )


# ------------------------------------------------------------------------ export


def run_to_atif(
    run: Run,
    task: Task | None = None,
    *,
    tool_definitions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Render a native run as an ATIF trajectory.

    The first step is the task statement as the user turn, then one agent step per native
    step. The parts of a native step ATIF has no field for, its exit code, duration and
    the harness's own flags, travel in that step's `extra`, which is also what lets an
    exported run import back without losing the signal the recovery metric depends on.

    Args:
        run: The run to export.
        task: The task it attempted, for the user turn. Without one the user turn names
            the task by id.
        tool_definitions: The tools offered, in OpenAI function schema, when known.

    Returns:
        A JSON-ready ATIF document.
    """
    steps: list[dict[str, Any]] = [
        {
            "step_id": 1,
            "timestamp": run.started_at.isoformat(),
            "source": "user",
            "message": task.agent_prompt if task is not None else f"Task {run.task_id}.",
        }
    ]
    for native in run.steps:
        call_id = f"call-{native.index:04d}"
        step: dict[str, Any] = {
            "step_id": len(steps) + 1,
            "timestamp": native.timestamp.isoformat(),
            "source": "agent",
            "message": native.thought or "",
            "tool_calls": [
                {
                    "tool_call_id": call_id,
                    "function_name": native.tool_name,
                    "arguments": dict(native.tool_args),
                }
            ],
            "observation": {
                "results": [{"source_call_id": call_id, "content": native.tool_output}]
            },
        }
        if native.tokens_in or native.tokens_out or native.cost_usd:
            step["metrics"] = {
                "prompt_tokens": native.tokens_in,
                "completion_tokens": native.tokens_out,
                "cost_usd": native.cost_usd,
            }
            step["llm_call_count"] = 1
        carried = {
            "exit_code": native.exit_code,
            "duration_ms": native.duration_ms,
            "schema_violation": native.schema_violation or None,
            "truncated": native.truncated or None,
            "error": native.error,
        }
        extra = {key: value for key, value in carried.items() if value is not None}
        if extra:
            step["extra"] = extra
        steps.append(step)

    agent: dict[str, Any] = {
        "name": AGENT_NAME,
        "version": run.harness_version,
        "model_name": run.config.model,
    }
    if tool_definitions:
        agent["tool_definitions"] = tool_definitions

    verification = run.verification
    return {
        "schema_version": ATIF_VERSION,
        "session_id": run.id,
        "trajectory_id": run.id,
        "agent": agent,
        "steps": steps,
        "final_metrics": {
            "total_prompt_tokens": sum(step.tokens_in for step in run.steps),
            "total_completion_tokens": sum(step.tokens_out for step in run.steps),
            "total_cost_usd": run.total_cost_usd,
            "total_steps": len(steps),
        },
        "extra": {
            "trajectory": {
                "run_id": run.id,
                "task_id": run.task_id,
                "suite": run.suite,
                "status": run.status.value,
                "solved": run.solved,
                "tests_passed": verification.tests_passed if verification else None,
                "tests_total": verification.tests_total if verification else None,
                # The whole verification record, exit code and output tail included, so the
                # import restores it exactly instead of rebuilding an outcome from counts.
                "verification": (
                    verification.model_dump(mode="json", exclude={"partial_credit"})
                    if verification
                    else None
                ),
                "failure_modes": [hit.id.value for hit in run.failure_modes],
                "seed": run.config.seed,
                "temperature": run.config.temperature,
                "sandbox_backend": run.config.sandbox_backend.value,
                "context_compressed": run.context_compressed,
                "error": run.error,
                "started_at": run.started_at.isoformat(),
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                # ATIF has no workspace snapshot, and three rules compare the workspace
                # before and after. Carrying both manifests is what makes a round trip
                # lossless rather than quietly dropping those three failure modes.
                "initial_workspace": run.initial_workspace.model_dump(mode="json"),
                "final_workspace": run.final_workspace.model_dump(mode="json"),
            }
        },
    }


# ------------------------------------------------------------------------ import


def _text(content: Any) -> str:  # noqa: ANN401  string or a list of ATIF content parts
    """Flatten ATIF content, which is a string or a list of typed parts, to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [part.get("text", "") for part in content if isinstance(part, dict)]
        return "\n".join(part for part in parts if part)
    return str(content)


def _timestamp(value: Any, fallback: datetime) -> datetime:  # noqa: ANN401  untrusted JSON
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return fallback


def _normalise_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Rename the path argument other agents use to the one this harness's rules read."""
    normalised = dict(arguments)
    if "path" not in normalised:
        for alias in PATH_ARGUMENT_ALIASES:
            if alias in normalised:
                normalised["path"] = normalised.pop(alias)
                break
    return normalised


def _int(value: Any) -> int:  # noqa: ANN401  untrusted JSON
    return value if isinstance(value, int) and value >= 0 else 0


def atif_to_run(doc: dict[str, Any], task: Task, *, reward: float | None = None) -> Run:
    """Turn an ATIF trajectory into a native run, scored and classified.

    Agent steps with tool calls become native steps, one per call. Text-only agent turns
    carry no action to score and are skipped, except a foreign trajectory's closing turn,
    which is that agent's declaration that it is done and is read as a finish. A round trip
    of this harness's own export keeps its tool names, exit codes and outcome exactly; a
    foreign trajectory has its tool names mapped through `TOOL_ALIASES` and is marked with
    the `atif:` prefix.

    Args:
        doc: The ATIF document.
        task: The task the trajectory attempted. Its reference step count and declared
            paths are what the metrics are measured against.
        reward: The verifier's reward for the trial, when known. 1.0 or more counts as
            solved. Without it, an exported run's own outcome is used if present.

    Returns:
        A sealed, scored run stamped with the local backend.

    Raises:
        AtifError: If the document is not a valid ATIF trajectory.
    """
    validate(doc)
    agent = doc["agent"]
    native_export = agent.get("name") == AGENT_NAME
    model_name = str(agent.get("model_name") or agent["name"])
    model = model_name if native_export else f"{IMPORT_PREFIX}{model_name}"

    origin = datetime.now(UTC)
    first_stamp = _timestamp(doc["steps"][0].get("timestamp"), origin)
    steps: list[Step] = []
    last_stamp = first_stamp
    for atif_step in doc["steps"]:
        if atif_step.get("source") != "agent" or not atif_step.get("tool_calls"):
            continue
        results = {
            result.get("source_call_id"): result.get("content")
            for result in (atif_step.get("observation") or {}).get("results", [])
        }
        metrics = atif_step.get("metrics") or {}
        extra = atif_step.get("extra") or {}
        stamp = _timestamp(atif_step.get("timestamp"), last_stamp)
        last_stamp = stamp
        for position, call in enumerate(atif_step["tool_calls"]):
            raw_name = str(call.get("function_name", "")).strip()
            name = (
                raw_name if native_export else TOOL_ALIASES.get(raw_name.lower(), raw_name.lower())
            )
            arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            first = position == 0
            exit_code = extra.get("exit_code") if first else None
            steps.append(
                Step(
                    index=len(steps),
                    timestamp=stamp,
                    thought=(_text(atif_step.get("message")) or None) if first else None,
                    tool_name=name or "(unnamed tool)",
                    tool_args=_normalise_arguments(arguments),
                    tool_output=_text(results.get(call.get("tool_call_id"))),
                    exit_code=exit_code if isinstance(exit_code, int) else None,
                    duration_ms=_int(extra.get("duration_ms")) if first else 0,
                    tokens_in=_int(metrics.get("prompt_tokens")) if first else 0,
                    tokens_out=_int(metrics.get("completion_tokens")) if first else 0,
                    cost_usd=float(metrics.get("cost_usd") or 0.0) if first else 0.0,
                    truncated=bool(extra.get("truncated")) if first else False,
                    schema_violation=bool(extra.get("schema_violation")) if first else False,
                    error=extra.get("error")
                    if first and isinstance(extra.get("error"), str)
                    else None,
                )
            )

    if not native_export:
        closing = _closing_turn(doc["steps"])
        if closing is not None:
            stamp = _timestamp(closing.get("timestamp"), last_stamp)
            last_stamp = stamp
            metrics = closing.get("metrics") or {}
            summary = _text(closing.get("message"))
            steps.append(
                Step(
                    index=len(steps),
                    timestamp=stamp,
                    thought=summary or None,
                    tool_name=ToolName.FINISH.value,
                    tool_args={"summary": summary},
                    tool_output="",
                    exit_code=None,
                    duration_ms=0,
                    tokens_in=_int(metrics.get("prompt_tokens")),
                    tokens_out=_int(metrics.get("completion_tokens")),
                    cost_usd=float(metrics.get("cost_usd") or 0.0),
                )
            )

    recorded = (doc.get("extra") or {}).get("trajectory") or {}
    verification = _verification(reward, recorded, task.expected_tests)
    if verification is not None:
        # The same counting rule as a native run, so an import cannot be solved by tests
        # that never reported.
        verification = verification.counting_declared(task.expected_tests)
    # A foreign agent declares itself done by stopping, so the end of its trajectory reads
    # as completion. A native export says exactly why it stopped, and that is kept.
    known = {member.value for member in RunStatus}
    status = (
        RunStatus(recorded["status"]) if recorded.get("status") in known else RunStatus.COMPLETED
    )

    run = Run(
        task_id=task.id,
        suite=task.suite,
        config=RunConfig(
            model=model,
            temperature=float(recorded.get("temperature") or 0.0),
            max_steps=min(max(task.max_steps, len(steps), 1), 200),
            seed=_int(recorded.get("seed")),
            tools_enabled=list(ToolName),
            timeout_seconds=task.timeout_seconds,
            command_timeout_seconds=task.command_timeout_seconds,
            sandbox_backend=SandboxBackend.LOCAL,
        ),
        started_at=first_stamp,
        status=status,
        steps=steps,
        verification=verification,
        runner_fingerprint=RunnerFingerprint(
            os="imported",
            os_release=str(doc.get("schema_version")),
            arch="unknown",
            python_version="unknown",
            cpu_count=1,
            docker_version=None,
            sandbox_backend=SandboxBackend.LOCAL,
        ),
    )
    for field in ("initial_workspace", "final_workspace"):
        if isinstance(recorded.get(field), dict):
            setattr(run, field, WorkspaceManifest.model_validate(recorded[field]))
    run.context_compressed = bool(recorded.get("context_compressed"))
    if isinstance(recorded.get("error"), str):
        run.error = recorded["error"]
    # A native export knows when its run started and finished, which is after the last
    # step because verification follows it. A foreign trajectory ends at its last step.
    run.started_at = _timestamp(recorded.get("started_at"), first_stamp)
    finished = _timestamp(recorded.get("finished_at"), max(last_stamp, first_stamp))

    run.seal(finished_at=finished)
    run.failure_modes = rank_failure_modes(classify_rules(run, task))
    run.score = score(run, task)
    return run


def _closing_turn(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The text-only agent turn that ends a foreign trajectory, if it ends on one.

    This harness's agents end a run by calling `finish`, and F03 premature success is
    defined against that call. Claude Code, Codex and the rest have no such tool: an agent
    ends by writing a last message, usually some form of "done", and that message is its
    claim that the task is complete. Read as a finish, it lets F03 fire on a foreign agent
    that stopped with the hidden tests still red, which is the same behaviour a native run
    is charged for. Only a closing turn counts. A text turn followed by more tool calls is
    the agent thinking aloud, not declaring victory.
    """
    for step in reversed(steps):
        if step.get("source") != "agent":
            continue
        return None if step.get("tool_calls") else step
    return None


def _verification(
    reward: float | None, recorded: dict[str, Any], declared: int | None
) -> Verification | None:
    """The outcome: from the reward when one is given, else from a native export's record.

    A reward says solved or not and nothing about counts, so it is spread over the task's
    declared test count: all of them on a reward of 1, none otherwise. A native export
    carries its whole verification record and gets it back exactly. An older export that
    carried only the counts is rebuilt from them.
    """
    if reward is not None:
        solved = reward >= 1.0
        total = declared or 1
        return Verification(
            passed=solved,
            tests_passed=total if solved else 0,
            tests_total=total,
            stderr_tail="",
            duration_ms=0,
            exit_code=0 if solved else 1,
            parse_ok=True,
        )
    stored = recorded.get("verification")
    if isinstance(stored, dict):
        try:
            return Verification.model_validate(
                {key: value for key, value in stored.items() if key != "partial_credit"}
            )
        except ValidationError:
            pass  # a record this version cannot read falls back to the counts below
    passed, total = recorded.get("tests_passed"), recorded.get("tests_total")
    if isinstance(passed, int) and isinstance(total, int) and total > 0:
        return Verification(
            passed=passed == total,
            tests_passed=passed,
            tests_total=total,
            stderr_tail="",
            duration_ms=0,
            exit_code=0 if passed == total else 1,
            parse_ok=True,
        )
    return None
