"""The data contract for the whole harness.

Every value that crosses a module boundary is one of these models. Trajectory records are
the product of this project, so they are validated at the boundary rather than trusted:
a malformed record discovered three days after an expensive evaluation run is far worse
than a validation error raised at the moment it was written.

Everything here is Pydantic v2 with `extra="forbid"`, which means an unknown field in a
results bundle produced by a newer harness version fails loudly instead of being dropped
on the floor.
"""

from __future__ import annotations

import platform
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from trajectory_core.ids import uuid7_str

HARNESS_VERSION = "0.1.0"

SCHEMA_VERSION = 1
"""Bumped whenever a change to these models is not backward compatible.

The results API rejects bundles whose schema version it does not understand, which is
the only way to keep a leaderboard honest across harness upgrades.
"""

TASK_ID_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
WORKSPACE_PATH = "/workspace"
"""Absolute path of the agent visible working tree inside the sandbox.

Fixed rather than configurable. Task authors write prompts and hidden tests against a
known path, and a configurable value would only produce tasks that work on one machine.
"""

VERIFY_PATH = "/verify"
"""Absolute path the hidden tests are copied to, after the agent phase has ended."""


class StrictModel(BaseModel):
    """Base for every model in the contract.

    Unknown fields are rejected rather than dropped, so a bundle written by a newer
    harness fails loudly instead of silently losing data on ingest.

    Whitespace is deliberately not stripped from strings. `Step.tool_output` holds
    verbatim process output and trailing newlines are part of what an agent actually saw;
    normalising them would quietly change the record the whole project exists to produce.

    Computed fields are serialised on the way out and ignored on the way in. Without
    that, `Model.model_validate_json(model.model_dump_json())` fails against
    `extra="forbid"`, which would make every round trip through the API a special case.
    """

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        use_enum_values=False,
        frozen=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _ignore_computed_fields(cls, data: Any) -> Any:  # noqa: ANN401  validator input is arbitrary
        """Drop read-only computed values so a serialised model validates again."""
        computed = cls.model_computed_fields
        if computed and isinstance(data, dict):
            overlap = computed.keys() & data.keys()
            if overlap:
                return {k: v for k, v in data.items() if k not in overlap}
        return data


# --------------------------------------------------------------------------- enums


class Language(StrEnum):
    """Primary language a task is written in. Drives nothing but reporting and filters."""

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    GO = "go"
    SQL = "sql"
    ANY = "any"


class ToolName(StrEnum):
    """The fixed tool surface exposed to the agent.

    Deliberately small. Every additional tool is another thing that behaves differently
    between models, and the point of the harness is to compare models rather than to
    compare how well each one was prompted for a bespoke tool.
    """

    BASH = "bash"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    LIST_DIR = "list_dir"
    FINISH = "finish"


class RunStatus(StrEnum):
    """Why the agent loop stopped.

    `COMPLETED` means the agent called `finish`. It says nothing about whether the task
    was solved, which is what `Verification.passed` is for. Conflating the two is the
    single most common bug in harnesses of this kind.
    """

    COMPLETED = "completed"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    BUDGET_EXCEEDED = "budget_exceeded"
    ERROR = "error"


class VerifyParser(StrEnum):
    """How to turn verification output into a passed and total test count."""

    PYTEST = "pytest"
    GO_TEST = "go_test"
    NODE_TAP = "node_tap"
    JSON_REPORT = "json_report"
    EXIT_CODE = "exit_code"


class SandboxBackend(StrEnum):
    """Which sandbox produced a run.

    `DOCKER` is the only backend whose results are accepted onto a leaderboard. `LOCAL`
    exists so a task author without a Docker daemon can still iterate, and every run it
    produces carries this marker so it can never be quietly mixed into published numbers.
    """

    DOCKER = "docker"
    LOCAL = "local"


class Detector(StrEnum):
    """Whether a failure mode was found by a deterministic rule or by the rubric judge."""

    RULE = "rule"
    JUDGE = "judge"


class FailureModeId(StrEnum):
    """Identifiers for the failure taxonomy.

    Definitions, detection methods and worked examples live in
    `trajectory_core.failure_modes`. The identifiers live here because they are part of
    the wire format and the API schema.
    """

    TOOL_SCHEMA_VIOLATION = "F01"
    PATH_HALLUCINATION = "F02"
    PREMATURE_SUCCESS = "F03"
    RETRY_LOOP = "F04"
    SHELL_QUOTING_ERROR = "F05"
    IGNORED_TEST_OUTPUT = "F06"
    LONG_HORIZON_CONTEXT_LOSS = "F07"
    DESTRUCTIVE_ACTION = "F08"
    SCOPE_CREEP = "F09"
    ENVIRONMENT_MISMATCH = "F10"


# ----------------------------------------------------------------------- task model


class Task(StrictModel):
    """A single evaluation task.

    Loaded from `tasks/<suite>/<id>/task.yaml`. The directory also holds a `Dockerfile`,
    a `workspace/` tree the agent starts from, a `verify/` tree of hidden tests the agent
    never sees, and a `reference/` solution that must pass those tests in CI.
    """

    id: Annotated[str, Field(pattern=TASK_ID_PATTERN, max_length=64)] = Field(
        description="Stable identifier, also the task directory name. Lowercase and hyphenated."
    )
    suite: str = Field(
        default="core-12",
        description="Suite the task belongs to. Leaderboards are always scoped to a suite.",
    )
    title: str = Field(max_length=120, description="One line human readable name.")
    description: str = Field(
        description="What is broken and what 'fixed' means, for humans reading the docs."
    )
    language: Language = Field(description="Primary language of the task workspace.")
    difficulty: int = Field(
        ge=1,
        le=5,
        description=(
            "1 is entry level with everything visible, 5 needs a correct mental model of a "
            "tool's internal state. Justify the rating against the reference step count."
        ),
    )
    tags: list[str] = Field(
        default_factory=list, description="Free form labels used for filtering and reporting."
    )

    image_tag: str = Field(
        description="Local image name built from the task Dockerfile, without the content hash."
    )
    setup_cmd: str | None = Field(
        default=None,
        description=(
            "Command run inside the container before the agent starts, for anything that "
            "cannot be baked into the image. Must be deterministic and offline."
        ),
    )

    agent_prompt: str = Field(
        description=(
            "The task statement handed to the agent. Written to be unambiguous about the "
            "success criteria without naming the hidden tests."
        )
    )

    max_steps: int = Field(
        ge=1, le=200, description="Hard ceiling on agent steps before the run is stopped."
    )
    timeout_seconds: int = Field(
        ge=30, le=3600, description="Wall clock ceiling for the whole agent phase."
    )
    command_timeout_seconds: int = Field(
        default=120, ge=1, le=900, description="Ceiling for any single command the agent runs."
    )

    network_allowed: bool = Field(
        default=False,
        description=(
            "Container network access during the agent phase. Off by default. A task that "
            "turns this on is not reproducible once the network it depends on changes."
        ),
    )
    network_reason: str | None = Field(
        default=None,
        description="Why this task needs the network. Required when network_allowed is true.",
    )

    verify_cmd: str = Field(
        description=(
            "Command that runs the hidden tests, executed after the agent phase with "
            f"{VERIFY_PATH} mounted. Runs with {WORKSPACE_PATH} as the working directory."
        )
    )
    verify_parser: VerifyParser = Field(
        default=VerifyParser.PYTEST,
        description="How to read a passed and total count out of the verification output.",
    )
    verify_timeout_seconds: int = Field(
        default=300, ge=5, le=1800, description="Ceiling for the verification phase."
    )

    reference_step_count: int = Field(
        ge=1,
        description=(
            "Steps in the reference playbook, including its closing finish call. This is the "
            "numerator of step efficiency, so an inflated value flatters every agent. Task "
            "validation refuses to accept a value that does not equal the length of "
            "reference/playbook.yaml, which means the only way to raise it is to write the "
            "extra steps and keep the solution passing."
        ),
    )

    relevant_paths: list[str] = Field(
        default_factory=list,
        description=(
            "Glob patterns, relative to the workspace, naming the files this task is about. "
            "Anything the agent changes outside them is scope creep, which is how F09 is "
            "detected without a heuristic. An empty list disables that check, and task "
            "validation warns when it is empty."
        ),
    )

    memory_mb: int = Field(default=2048, ge=256, le=8192, description="Container memory ceiling.")
    cpus: float = Field(default=2.0, gt=0, le=8, description="Container CPU ceiling in cores.")
    pids_limit: int = Field(default=256, ge=16, le=4096, description="Container PID ceiling.")

    @model_validator(mode="after")
    def _network_needs_a_reason(self) -> Self:
        """Refuse a task that opens the network without saying why."""
        if self.network_allowed and not self.network_reason:
            raise ValueError(
                "network_allowed is true but network_reason is empty. Every task that needs "
                "the network has to justify it, because it is the fastest way to make a "
                "benchmark stop being reproducible."
            )
        return self

    @model_validator(mode="after")
    def _reference_must_be_plausible(self) -> Self:
        """Refuse a reference step count that cannot fit inside the step budget."""
        if self.reference_step_count > self.max_steps:
            raise ValueError(
                f"reference_step_count ({self.reference_step_count}) exceeds max_steps "
                f"({self.max_steps}), so no agent could ever match the reference."
            )
        return self


class TaskSummary(StrictModel):
    """Public task metadata.

    This is what the API serves. It deliberately omits `verify_cmd` and everything else
    that would tell a model what the hidden tests look like.
    """

    id: str = Field(description="Task identifier.")
    suite: str = Field(description="Suite the task belongs to.")
    title: str = Field(description="One line human readable name.")
    description: str = Field(description="What is broken and what fixed means.")
    language: Language = Field(description="Primary language.")
    difficulty: int = Field(ge=1, le=5, description="Difficulty tier from 1 to 5.")
    tags: list[str] = Field(default_factory=list, description="Filtering labels.")
    max_steps: int = Field(description="Step ceiling for the task.")
    reference_step_count: int = Field(description="Step count of the reference solution.")

    @classmethod
    def from_task(cls, task: Task) -> TaskSummary:
        """Project a full task down to its publishable fields."""
        return cls(
            id=task.id,
            suite=task.suite,
            title=task.title,
            description=task.description,
            language=task.language,
            difficulty=task.difficulty,
            tags=list(task.tags),
            max_steps=task.max_steps,
            reference_step_count=task.reference_step_count,
        )


# ------------------------------------------------------------------------ run config


class RunConfig(StrictModel):
    """Everything that can change between two runs of the same task."""

    model: str = Field(
        description=(
            "LiteLLM model identifier, for example anthropic/claude-sonnet-4-5 or "
            "ollama/qwen2.5-coder. The prefix `stub:` selects an offline scripted policy."
        )
    )
    temperature: float = Field(
        default=0.0, ge=0.0, le=2.0, description="Sampling temperature passed to the model."
    )
    max_steps: int = Field(ge=1, le=200, description="Step ceiling, usually copied from the task.")
    seed: int = Field(
        default=0,
        description=(
            "Seed passed to the provider where supported, and used to select the scripted "
            "policy variant offline. Recorded whether or not the provider honours it."
        ),
    )
    tools_enabled: list[ToolName] = Field(
        default_factory=lambda: list(ToolName),
        min_length=1,
        description=(
            "Tool subset offered to the model. Narrowing it is an experiment, not a default."
        ),
    )
    timeout_seconds: int = Field(ge=30, le=3600, description="Wall clock ceiling for the run.")
    command_timeout_seconds: int = Field(
        default=120, ge=1, le=900, description="Ceiling for a single command."
    )
    budget_usd: float | None = Field(
        default=None,
        gt=0,
        description=(
            "Hard spend ceiling checked before every model call. A run that would cross it "
            "stops with status budget_exceeded rather than making the call."
        ),
    )
    max_output_bytes: int = Field(
        default=16_384,
        ge=512,
        le=1_048_576,
        description=(
            "Per command output cap. Output past this is cut and the step is flagged truncated."
        ),
    )
    sandbox_backend: SandboxBackend = Field(
        default=SandboxBackend.DOCKER, description="Which sandbox implementation to use."
    )


# ---------------------------------------------------------------- workspace state


class WorkspaceManifest(StrictModel):
    """A content hash of every file in the agent workspace at a point in time.

    Captured once before the agent starts and once after it stops. Two rules depend on
    it: path hallucination compares the paths an agent referenced against the paths that
    actually existed, and scope creep compares what changed against what the task said it
    was about. Both live in the run record rather than in a side file, so rescoring a run
    a month later needs nothing but the run itself.
    """

    files: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Workspace relative path to a truncated SHA-256 of the file contents. Truncated "
            "to 16 hex characters, which is ample for change detection and keeps a run "
            "record with a few hundred files small enough to read."
        ),
    )
    truncated: bool = Field(
        default=False,
        description="True when the workspace had more files than the capture limit allowed.",
    )

    def paths(self) -> set[str]:
        """Return the set of workspace relative paths."""
        return set(self.files)

    def changed_against(self, other: WorkspaceManifest) -> set[str]:
        """Return paths added, removed or modified relative to `other`."""
        changed = {p for p, h in self.files.items() if other.files.get(p) != h}
        return changed | (other.paths() - self.paths())


# ----------------------------------------------------------------------------- step


class Step(StrictModel):
    """One tool call and its result.

    Written to disk as soon as it completes, before the next model call is made, so a
    crashed or killed run still leaves a usable partial trajectory behind.
    """

    index: int = Field(ge=0, description="Zero based position in the trajectory.")
    timestamp: datetime = Field(description="When the tool call was issued, in UTC.")
    thought: str | None = Field(
        default=None, description="Assistant text that accompanied the tool call, if any."
    )
    tool_name: str = Field(
        description=(
            "Name the model asked for. A free string rather than the enum, because a model "
            "inventing a tool that does not exist is data, not a crash."
        )
    )
    tool_args: dict[str, Any] = Field(
        default_factory=dict, description="Arguments as the model supplied them, before coercion."
    )
    tool_output: str = Field(default="", description="Combined stdout and stderr, possibly capped.")
    exit_code: int | None = Field(
        default=None, description="Process exit code for bash steps, null for the others."
    )
    duration_ms: int = Field(ge=0, description="Wall clock milliseconds for the tool call itself.")
    tokens_in: int = Field(default=0, ge=0, description="Prompt tokens billed for this step.")
    tokens_out: int = Field(default=0, ge=0, description="Completion tokens billed for this step.")
    cost_usd: float = Field(
        default=0.0, ge=0.0, description="Provider cost attributed to this step."
    )
    truncated: bool = Field(
        default=False, description="True when tool_output was cut at max_output_bytes."
    )
    schema_violation: bool = Field(
        default=False,
        description=(
            "True when the model called an unknown tool or supplied arguments that failed "
            "validation. The step is recorded and the loop continues, because the rate of "
            "these is one of the reported metrics."
        ),
    )
    error: str | None = Field(
        default=None, description="Harness side error message, if the tool call could not be run."
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def failed(self) -> bool:
        """True when the step produced a non-zero exit code."""
        return self.exit_code is not None and self.exit_code != 0


# --------------------------------------------------------------------- verification


class Verification(StrictModel):
    """Result of running the hidden tests after the agent phase."""

    passed: bool = Field(description="True only when every hidden test passed.")
    tests_passed: int = Field(ge=0, description="Number of hidden tests that passed.")
    tests_total: int = Field(ge=0, description="Number of hidden tests discovered.")
    stderr_tail: str = Field(
        default="", description="Last few kilobytes of verification stderr, for triage."
    )
    duration_ms: int = Field(
        ge=0, description="Wall clock milliseconds for the verification phase."
    )
    exit_code: int = Field(description="Exit code of verify_cmd.")
    parse_ok: bool = Field(
        default=True,
        description=(
            "False when the output could not be parsed into a passed and total count. The "
            "run then falls back to exit code only, and partial credit is not meaningful."
        ),
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def partial_credit(self) -> float:
        """Fraction of hidden tests that passed, in the range 0 to 1."""
        if self.tests_total <= 0:
            return 1.0 if self.passed else 0.0
        return round(self.tests_passed / self.tests_total, 6)


# ---------------------------------------------------------------------------- score


class TrajectoryScore(StrictModel):
    """The ten trajectory metrics.

    Formulas, worked examples and the reasoning behind each one are in `docs/metrics.md`
    and in the docstrings of `trajectory_core.scoring`.
    """

    solved: bool = Field(description="Metric 1. Every hidden test passed.")
    partial_credit: float = Field(
        ge=0.0, le=1.0, description="Metric 2. tests_passed divided by tests_total."
    )
    step_efficiency: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Metric 3. reference_step_count divided by steps taken, capped at 1.0. Null on "
            "unsolved runs, where a low step count means the agent gave up rather than that "
            "it was efficient."
        ),
    )
    tool_call_validity: float = Field(
        ge=0.0, le=1.0, description="Metric 4. Valid tool calls divided by total tool calls."
    )
    redundant_action_rate: float = Field(
        ge=0.0,
        le=1.0,
        description="Metric 5. Byte identical repeat commands divided by total commands.",
    )
    recovery_rate: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Metric 6. Of the steps that exited non-zero, the fraction followed within two "
            "steps by a materially different command. Null when nothing failed."
        ),
    )
    premature_termination: bool = Field(
        description="Metric 7. The agent called finish while verification fails."
    )
    context_drift: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Metric 8. Judge scored, 0 means the final third of the trajectory is still on "
            "task and 1 means it has drifted entirely. Null when the judge did not run."
        ),
    )
    cost_usd: float = Field(ge=0.0, description="Metric 9a. Summed provider cost for the run.")
    wall_clock_s: float = Field(ge=0.0, description="Metric 9b. Wall clock seconds for the run.")
    destructive_attempts: int = Field(
        ge=0,
        description=(
            "Metric 10. Commands matching the destructive pattern list. Flagged, never "
            "blocked, because the tendency is the finding."
        ),
    )

    total_steps: int = Field(ge=0, description="Steps recorded in the trajectory.")
    total_commands: int = Field(ge=0, description="Steps that executed a shell command.")
    schema_violations: int = Field(ge=0, description="Steps rejected by tool argument validation.")
    failed_commands: int = Field(ge=0, description="Commands that exited non-zero.")


# -------------------------------------------------------------------- failure modes


class FailureModeHit(StrictModel):
    """One failure mode found in one run."""

    id: FailureModeId = Field(description="Taxonomy identifier, F01 to F10.")
    name: str = Field(description="Short human readable name of the mode.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How sure the detector is. Rule based hits are 1.0 when the rule is exact and "
            "lower when it relies on a heuristic. Judge hits carry the judge's own number."
        ),
    )
    detector: Detector = Field(description="Whether a rule or the judge produced this hit.")
    evidence: str = Field(
        max_length=2000, description="Concrete quote or summary a human can check the hit against."
    )
    step_indices: list[int] = Field(
        default_factory=list, description="Steps the hit points at, for highlighting in the replay."
    )


# --------------------------------------------------------------------- provenance


class RunnerFingerprint(StrictModel):
    """Where a run was produced.

    Results that cannot be reproduced are not results. When a number moves, the first
    question is whether the model changed or the environment did, and this is how you
    answer it without guessing.
    """

    os: str = Field(description="Operating system name.")
    os_release: str = Field(description="Kernel or OS release string.")
    arch: str = Field(description="CPU architecture.")
    python_version: str = Field(description="Interpreter version running the harness.")
    cpu_count: int = Field(ge=1, description="Logical CPUs visible to the harness.")
    docker_version: str | None = Field(
        default=None, description="Docker server version, null when the local backend was used."
    )
    sandbox_backend: SandboxBackend = Field(description="Which sandbox produced the run.")
    ci: bool = Field(default=False, description="True when produced by a CI runner.")

    @classmethod
    def capture(
        cls,
        *,
        sandbox_backend: SandboxBackend,
        docker_version: str | None = None,
        ci: bool = False,
    ) -> RunnerFingerprint:
        """Snapshot the current machine."""
        import os as _os

        return cls(
            os=platform.system(),
            os_release=platform.release(),
            arch=platform.machine(),
            python_version=platform.python_version(),
            cpu_count=_os.cpu_count() or 1,
            docker_version=docker_version,
            sandbox_backend=sandbox_backend,
            ci=ci,
        )


# ------------------------------------------------------------------------------ run


class Run(StrictModel):
    """One agent attempt at one task, start to finish.

    This is the unit the whole project exists to produce. It is written incrementally
    during execution and sealed when the run ends.
    """

    id: str = Field(default_factory=uuid7_str, description="UUIDv7, so runs sort by start time.")
    schema_version: int = Field(
        default=SCHEMA_VERSION, description="Contract version this record was written against."
    )
    task_id: str = Field(description="Task the run attempted.")
    suite: str = Field(default="core-12", description="Suite the task belongs to.")
    config: RunConfig = Field(description="Configuration the run executed under.")

    started_at: datetime = Field(description="Start of the agent phase, in UTC.")
    finished_at: datetime | None = Field(
        default=None, description="End of the run including verification, in UTC."
    )
    status: RunStatus = Field(
        default=RunStatus.ERROR, description="Why the agent loop stopped. Not whether it succeeded."
    )

    steps: list[Step] = Field(default_factory=list, description="The full trajectory, in order.")
    verification: Verification | None = Field(
        default=None, description="Hidden test result, null when verification never ran."
    )
    score: TrajectoryScore | None = Field(
        default=None, description="Trajectory metrics, filled in by the scorer."
    )
    failure_modes: list[FailureModeHit] = Field(
        default_factory=list, description="Classified failures, ranked by confidence descending."
    )

    image_id: str | None = Field(
        default=None,
        description=(
            "Content address of the image the run executed in, as reported by the daemon. "
            "A version tag in a Dockerfile can move; this cannot. When a published number "
            "shifts, this is what tells you whether the environment changed or the model did."
        ),
    )

    harness_version: str = Field(
        default=HARNESS_VERSION, description="Version of the harness that produced the run."
    )
    runner_fingerprint: RunnerFingerprint = Field(description="Machine the run was produced on.")

    initial_workspace: WorkspaceManifest = Field(
        default_factory=WorkspaceManifest,
        description="Workspace contents captured immediately before the agent's first step.",
    )
    final_workspace: WorkspaceManifest = Field(
        default_factory=WorkspaceManifest,
        description="Workspace contents captured immediately after the agent phase ended.",
    )

    context_compressed: bool = Field(
        default=False,
        description=(
            "True when the oldest tool outputs were summarised to stay inside the context "
            "window. Compression changes results, so it is never silent."
        ),
    )
    error: str | None = Field(default=None, description="Harness level error that ended the run.")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def wall_clock_s(self) -> float:
        """Seconds from start to finish, or 0.0 while the run is still open."""
        if self.finished_at is None:
            return 0.0
        return round((self.finished_at - self.started_at).total_seconds(), 3)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def solved(self) -> bool:
        """True when verification ran and every hidden test passed."""
        return self.verification is not None and self.verification.passed

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_cost_usd(self) -> float:
        """Summed provider cost across every step."""
        return round(sum(step.cost_usd for step in self.steps), 6)

    def seal(self, *, finished_at: datetime | None = None) -> None:
        """Mark the run finished. Idempotent."""
        if self.finished_at is None:
            self.finished_at = finished_at or datetime.now(UTC)


# -------------------------------------------------------------------------- bundles


class BundleManifest(StrictModel):
    """Header of a results bundle."""

    bundle_id: str = Field(default_factory=uuid7_str, description="UUIDv7 for this bundle.")
    schema_version: int = Field(default=SCHEMA_VERSION, description="Contract version.")
    harness_version: str = Field(default=HARNESS_VERSION, description="Harness that produced it.")
    suite: str = Field(description="Suite every run in the bundle belongs to.")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC), description="When the bundle was written."
    )
    run_count: int = Field(ge=0, description="Number of run records in the bundle.")
    models: list[str] = Field(default_factory=list, description="Distinct model identifiers.")
    content_sha256: str = Field(
        default="",
        description=(
            "SHA-256 over the canonical JSON of every run, in id order. The API recomputes "
            "it on ingest, so a truncated upload is rejected instead of half stored."
        ),
    )
    fingerprint: RunnerFingerprint = Field(description="Machine the bundle was produced on.")
    notes: str = Field(default="", description="Free text, for example the command line used.")


class ResultsBundle(StrictModel):
    """A manifest plus its runs. This is the unit that moves over the wire."""

    manifest: BundleManifest = Field(description="Bundle header.")
    runs: list[Run] = Field(description="Run records, expected in id order.")


# ---------------------------------------------------------------- reference playbook


class PlaybookStep(StrictModel):
    """One step of the reference solution for a task."""

    tool: ToolName = Field(description="Tool an expert would reach for at this point.")
    args: dict[str, Any] = Field(default_factory=dict, description="Arguments for the tool.")
    note: str = Field(
        default="",
        description=(
            "Why this step exists. Read by nobody at runtime and by every contributor who "
            "wants to understand what the task is really testing."
        ),
    )


class ReferencePlaybook(StrictModel):
    """The reference solution, expressed as the trajectory an expert would produce.

    Kept as a trajectory rather than a patch file for three reasons. It is what the
    offline reference policies replay, so the harness can be exercised end to end with no
    provider and no spend. It is the definition of `reference_step_count`, which removes
    the temptation to guess that number. And it doubles as the CI gate: replay the
    playbook, run the hidden tests, and a task whose own solution has rotted fails the
    build instead of quietly flattering every agent that attempts it.
    """

    task_id: str = Field(description="Task the playbook solves.")
    steps: list[PlaybookStep] = Field(
        min_length=2, description="Ordered steps, ending with a finish call."
    )
    notes: str = Field(default="", description="Author notes about the intended approach.")

    @model_validator(mode="after")
    def _must_end_with_finish(self) -> Self:
        """A playbook that never declares itself done is not a complete trajectory."""
        if self.steps[-1].tool is not ToolName.FINISH:
            raise ValueError(
                f"playbook for {self.task_id} ends with {self.steps[-1].tool.value}, "
                "but the last step of a reference solution has to be finish"
            )
        if any(step.tool is ToolName.FINISH for step in self.steps[:-1]):
            raise ValueError(f"playbook for {self.task_id} calls finish before its last step")
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def step_count(self) -> int:
        """Number of steps including the closing finish call."""
        return len(self.steps)


# ---------------------------------------------------------------------- aggregates


class MetricStat(StrictModel):
    """A mean with its spread across seeds.

    A pass rate reported without variance across seeds is not a result, so every
    aggregate carries one of these rather than a bare float.
    """

    mean: float = Field(description="Arithmetic mean across the runs in the group.")
    stdev: float = Field(ge=0.0, description="Population standard deviation across seeds.")
    n: int = Field(ge=0, description="Number of runs behind the mean.")

    def render(self, *, digits: int = 3, percent: bool = False) -> str:
        """Format as `mean +/- stdev` for a table cell."""
        scale = 100.0 if percent else 1.0
        suffix = "%" if percent else ""
        return f"{self.mean * scale:.{digits}f}{suffix} +/- {self.stdev * scale:.{digits}f}{suffix}"


class LeaderboardRow(StrictModel):
    """One model's aggregate performance on one suite."""

    model: str = Field(description="Model identifier.")
    suite: str = Field(description="Suite the numbers cover.")
    runs: int = Field(ge=0, description="Runs behind the row.")
    tasks: int = Field(ge=0, description="Distinct tasks attempted.")
    seeds: int = Field(ge=0, description="Distinct seeds attempted.")

    solve_rate: MetricStat = Field(description="Metric 1, aggregated across seeds.")
    partial_credit: MetricStat = Field(description="Metric 2, aggregated.")
    step_efficiency: MetricStat = Field(description="Metric 3, over solved runs only.")
    tool_call_validity: MetricStat = Field(description="Metric 4, aggregated.")
    redundant_action_rate: MetricStat = Field(description="Metric 5, aggregated.")
    recovery_rate: MetricStat = Field(description="Metric 6, over runs with a failure.")
    premature_termination_rate: MetricStat = Field(description="Metric 7, as a rate.")
    context_drift: MetricStat = Field(description="Metric 8, over judged runs.")

    mean_cost_usd: float = Field(ge=0.0, description="Mean provider cost per run.")
    cost_per_solved_usd: float | None = Field(
        default=None, description="Mean cost per solved run, null when nothing was solved."
    )
    mean_wall_clock_s: float = Field(ge=0.0, description="Mean wall clock seconds per run.")
    total_cost_usd: float = Field(ge=0.0, description="Summed cost across every run in the row.")
    destructive_attempts: int = Field(ge=0, description="Summed destructive command attempts.")

    last_run_at: datetime | None = Field(
        default=None, description="Start time of the most recent run behind this row."
    )


class FailureModeCount(StrictModel):
    """How often one failure mode fired, sliced by model or by task."""

    id: FailureModeId = Field(description="Taxonomy identifier.")
    name: str = Field(description="Human readable name.")
    count: int = Field(ge=0, description="Number of runs carrying the mode.")
    share_of_failed_runs: float = Field(
        ge=0.0, le=1.0, description="Count divided by the number of unsolved runs in the slice."
    )
