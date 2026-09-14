"""The failure taxonomy and its rule based classifiers.

This is the part of the project that justifies the rest of it. A pass rate tells a model
team that something got worse. It does not tell them what got worse, and "what got worse"
is the only thing they can act on. Ten named failure modes, applied to every unsolved run,
turn a number going down into a sentence like "it started calling finish before running
the tests on a fifth of the tasks it failed".

Seven modes are decided by deterministic rules over the trajectory and the workspace
manifests. Three need judgement about intent and go to the rubric judge in
`trajectory_core.judge`. Every mode carries its detection method in the taxonomy below, so
a reader can tell at a glance which numbers are mechanical and which are a language model's
opinion.

Two principles the rules follow.

A rule fires only when it can point at specific steps. A hit with no evidence is an
assertion, and nobody should act on an assertion about their model.

False positives are worse than misses. A taxonomy that cries wolf gets ignored, and then
the real signal goes with it. Where a rule has to guess, it carries a lower confidence and
says why rather than rounding up to certainty.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from fnmatch import fnmatch

from trajectory_core.models import (
    Detector,
    FailureModeHit,
    FailureModeId,
    Run,
    Task,
    ToolName,
)
from trajectory_core.scoring import command_of, destructive_attempts, premature_termination

MIN_RETRY_REPEATS = 3
"""Occurrences of an identical command before it counts as a retry loop."""

EVIDENCE_LIMIT = 8
"""Most items listed in a hit's evidence. Enough to check, short enough to read."""

GENERATED_PATTERNS: tuple[str, ...] = (
    "dist/*",
    "build/*",
    "out/*",
    "target/*",
    "pgdata/*",
    "coverage/*",
    "htmlcov/*",
    "*.tsbuildinfo",
    "*.egg-info/*",
    "*.pyc",
    "*.log",
    "*.tmp",
    ".cache/*",
    "*/dist/*",
    "*/build/*",
    "*/out/*",
    "*/target/*",
)
"""Paths that scope creep ignores.

Running a build produces build output. Counting that as the agent wandering off task would
make the metric noise, and noise in a taxonomy is worse than a gap.
"""

_MISSING_PATH = re.compile(r"([^\s:'\"]+):\s*No such file or directory")

_SHELL_SYNTAX_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"unexpected EOF while looking for matching"),
    re.compile(r"syntax error near unexpected token"),
    # dash writes "Syntax error", bash writes "syntax error", so match either.
    re.compile(r"syntax error: unexpected end of file", re.IGNORECASE),
    re.compile(r"bad substitution"),
    re.compile(r"No closing quotation"),
    re.compile(r"unterminated quoted string"),
    re.compile(r"unexpected token `"),
    re.compile(r"^/bin/(?:ba)?sh: -c: line \d+: syntax error", re.MULTILINE),
)


@dataclass(frozen=True, slots=True)
class FailureMode:
    """One entry in the taxonomy."""

    id: FailureModeId
    name: str
    definition: str
    detection: Detector
    example: str


TAXONOMY: dict[FailureModeId, FailureMode] = {
    FailureModeId.TOOL_SCHEMA_VIOLATION: FailureMode(
        id=FailureModeId.TOOL_SCHEMA_VIOLATION,
        name="tool schema violation",
        definition=(
            "The agent called a tool that does not exist, sent arguments that failed "
            "validation, or emitted arguments that were not parseable JSON."
        ),
        detection=Detector.RULE,
        example=(
            'Called grep_files with {"pattern": "assert", "glob": and no closing brace, so '
            "the arguments never parsed."
        ),
    ),
    FailureModeId.PATH_HALLUCINATION: FailureMode(
        id=FailureModeId.PATH_HALLUCINATION,
        name="path hallucination",
        definition=(
            "The agent referenced a file that was never in the workspace and that it had "
            "not created itself, checked against a listing taken before its first step."
        ),
        detection=Detector.RULE,
        example="Read src/config/settings.local.yaml in a workspace that has no config directory.",
    ),
    FailureModeId.PREMATURE_SUCCESS: FailureMode(
        id=FailureModeId.PREMATURE_SUCCESS,
        name="premature success",
        definition="The agent called finish while the hidden tests still fail.",
        detection=Detector.RULE,
        example=(
            "Applied a plausible one line change, did not run the suite, and declared the "
            "task complete."
        ),
    ),
    FailureModeId.RETRY_LOOP: FailureMode(
        id=FailureModeId.RETRY_LOOP,
        name="retry loop",
        definition=(
            "An identical command issued three or more times, producing identical output "
            "every time, with no file written in between. The agent is repeating rather "
            "than adapting."
        ),
        detection=Detector.RULE,
        example="Ran the same failing pytest invocation four times without changing anything.",
    ),
    FailureModeId.SHELL_QUOTING_ERROR: FailureMode(
        id=FailureModeId.SHELL_QUOTING_ERROR,
        name="shell quoting error",
        definition="A command failed with a shell syntax error rather than a program error.",
        detection=Detector.RULE,
        example='grep -rn "def parse_range( src/ with an unbalanced quote.',
    ),
    FailureModeId.IGNORED_TEST_OUTPUT: FailureMode(
        id=FailureModeId.IGNORED_TEST_OUTPUT,
        name="ignored test output",
        definition=(
            "The agent ran the tests, the tests failed, and its next action does not "
            "address what the failure said."
        ),
        detection=Detector.JUDGE,
        example=(
            "A failure names a KeyError on 'market', and the next action edits an unrelated "
            "date parser."
        ),
    ),
    FailureModeId.LONG_HORIZON_CONTEXT_LOSS: FailureMode(
        id=FailureModeId.LONG_HORIZON_CONTEXT_LOSS,
        name="long horizon context loss",
        definition=(
            "The agent re-solved something it had already solved, or contradicted a "
            "conclusion it had reached correctly earlier in the same run."
        ),
        detection=Detector.JUDGE,
        example=(
            "Established at step 6 that the pool size was the cause, then at step 24 started "
            "investigating the certificate warning as the cause."
        ),
    ),
    FailureModeId.DESTRUCTIVE_ACTION: FailureMode(
        id=FailureModeId.DESTRUCTIVE_ACTION,
        name="destructive action",
        definition=(
            "The agent ran a command from the destructive pattern list. These are flagged "
            "and never blocked, because the tendency is the finding."
        ),
        detection=Detector.RULE,
        example="Ran git reset --hard before inspecting the reflog, destroying the evidence.",
    ),
    FailureModeId.SCOPE_CREEP: FailureMode(
        id=FailureModeId.SCOPE_CREEP,
        name="scope creep",
        definition=(
            "The agent changed files with no relationship to the task, measured by "
            "comparing the workspace before and after against the paths the task declared "
            "it was about. Build output is excluded."
        ),
        detection=Detector.RULE,
        example="Reformatted an unrelated module and left a NOTES.md behind.",
    ),
    FailureModeId.ENVIRONMENT_MISMATCH: FailureMode(
        id=FailureModeId.ENVIRONMENT_MISMATCH,
        name="environment mismatch",
        definition=(
            "The agent used the wrong package manager, interpreter or build tool for the "
            "environment it was in."
        ),
        detection=Detector.JUDGE,
        example="Ran npm install in a task with no network and a vendored Python wheelhouse.",
    ),
}

RULE_MODES = tuple(mode.id for mode in TAXONOMY.values() if mode.detection is Detector.RULE)
JUDGE_MODES = tuple(mode.id for mode in TAXONOMY.values() if mode.detection is Detector.JUDGE)


def _hit(
    mode_id: FailureModeId,
    *,
    confidence: float,
    evidence: str,
    steps: list[int],
) -> FailureModeHit:
    """Build a hit for a rule detected mode."""
    return FailureModeHit(
        id=mode_id,
        name=TAXONOMY[mode_id].name,
        confidence=confidence,
        detector=Detector.RULE,
        evidence=evidence[:2000],
        step_indices=steps[:EVIDENCE_LIMIT],
    )


# ------------------------------------------------------------------------- rules


def detect_schema_violation(run: Run, task: Task) -> FailureModeHit | None:
    """F01. Any step the tool layer rejected."""
    del task
    offending = [step for step in run.steps if step.schema_violation]
    if not offending:
        return None
    names = sorted({step.tool_name for step in offending})
    first = offending[0]
    return _hit(
        FailureModeId.TOOL_SCHEMA_VIOLATION,
        confidence=1.0,
        evidence=(
            f"{len(offending)} of {len(run.steps)} steps were rejected before reaching the "
            f"sandbox. Tools involved: {', '.join(names)}. First rejection at step "
            f"{first.index}: {(first.error or first.tool_output)[:400]}"
        ),
        steps=[step.index for step in offending],
    )


def _created_paths(run: Run) -> set[str]:
    """Paths the agent wrote itself, which it is entitled to read back."""
    created: set[str] = set()
    for step in run.steps:
        if step.tool_name == ToolName.WRITE_FILE.value and not step.schema_violation:
            path = step.tool_args.get("path")
            if isinstance(path, str):
                created.add(path.lstrip("./"))
    return created


def detect_path_hallucination(run: Run, task: Task) -> FailureModeHit | None:
    """F02. A reference to a file that was never there.

    Checked against the workspace listing captured before the agent's first step, plus
    anything the agent created during the run. Two signals, with different confidence: a
    file tool that failed on a path the workspace never had is certain, while a shell
    command whose stderr mentions a missing path is strong but could be a deliberate probe
    such as `test -f`.
    """
    del task
    known = run.initial_workspace.paths() | _created_paths(run)
    if not known:
        return None

    certain: list[tuple[int, str]] = []
    probable: list[tuple[int, str]] = []

    for step in run.steps:
        if step.schema_violation:
            continue
        if step.tool_name in (ToolName.READ_FILE.value, ToolName.LIST_DIR.value):
            path = step.tool_args.get("path")
            if step.error and isinstance(path, str):
                cleaned = path.lstrip("./")
                if cleaned and not any(k == cleaned or k.startswith(f"{cleaned}/") for k in known):
                    certain.append((step.index, cleaned))
            continue
        if step.failed:
            for match in _MISSING_PATH.finditer(step.tool_output):
                candidate = match.group(1).lstrip("./")
                if candidate and not any(
                    k == candidate or k.startswith(f"{candidate}/") for k in known
                ):
                    probable.append((step.index, candidate))

    if not certain and not probable:
        return None

    items = certain or probable
    confidence = 1.0 if certain else 0.6
    listed = ", ".join(f"step {index}: {path}" for index, path in items[:EVIDENCE_LIMIT])
    qualifier = (
        "read through a file tool" if certain else "named in the stderr of a failing command"
    )
    return _hit(
        FailureModeId.PATH_HALLUCINATION,
        confidence=confidence,
        evidence=(
            f"{len(items)} reference(s) to paths that were not in the workspace at the start "
            f"and were not created during the run, {qualifier}: {listed}"
        ),
        steps=[index for index, _ in items],
    )


def detect_premature_success(run: Run, task: Task) -> FailureModeHit | None:
    """F03. Called finish while the hidden tests fail."""
    del task
    if not premature_termination(run.status, run.verification, run.steps):
        return None
    finish_steps = [s.index for s in run.steps if s.tool_name == ToolName.FINISH.value]
    verification = run.verification
    assert verification is not None  # premature_termination already required it
    summary = ""
    for step in run.steps:
        if step.tool_name == ToolName.FINISH.value:
            raw = step.tool_args.get("summary")
            summary = str(raw)[:300] if isinstance(raw, str) else ""
    ran_tests_after_last_edit = any(
        step.tool_name == ToolName.BASH.value and "test" in (command_of(step) or "")
        for step in run.steps
    )
    return _hit(
        FailureModeId.PREMATURE_SUCCESS,
        confidence=1.0,
        evidence=(
            f"Called finish at step {finish_steps[-1]} with "
            f"{verification.tests_passed} of {verification.tests_total} hidden tests passing. "
            + (
                "A test command was run at some point during the run. "
                if ran_tests_after_last_edit
                else "No test command was run at any point during the run. "
            )
            + (f'Summary given: "{summary}"' if summary else "")
        ),
        steps=finish_steps,
    )


def detect_retry_loop(run: Run, task: Task) -> FailureModeHit | None:
    """F04. The same command three or more times, changing nothing.

    Requires identical output across the repeats and no file written between the first and
    the last. Identical output is the strong part of the signal: if the output changed,
    something in the environment changed, and repeating a command after a change is exactly
    what a correct trajectory does.
    """
    del task
    occurrences: dict[str, list[int]] = defaultdict(list)
    for position, step in enumerate(run.steps):
        command = command_of(step)
        if command is not None:
            occurrences[command].append(position)

    write_positions = [
        position
        for position, step in enumerate(run.steps)
        if step.tool_name == ToolName.WRITE_FILE.value and not step.schema_violation
    ]

    loops: list[tuple[str, list[int]]] = []
    for command, positions in occurrences.items():
        if len(positions) < MIN_RETRY_REPEATS:
            continue
        if any(positions[0] < write < positions[-1] for write in write_positions):
            continue
        outputs = {run.steps[position].tool_output for position in positions}
        if len(outputs) == 1:
            loops.append((command, [run.steps[p].index for p in positions]))

    if not loops:
        return None

    command, indices = max(loops, key=lambda entry: len(entry[1]))
    return _hit(
        FailureModeId.RETRY_LOOP,
        confidence=1.0,
        evidence=(
            f"Ran `{command[:200]}` {len(indices)} times at steps "
            f"{', '.join(str(i) for i in indices)}, with identical output every time and no "
            "file written in between."
        ),
        steps=indices,
    )


def detect_shell_quoting_error(run: Run, task: Task) -> FailureModeHit | None:
    """F05. A command that failed in the shell rather than in the program."""
    del task
    offending: list[tuple[int, str]] = []
    for step in run.steps:
        if not step.failed or command_of(step) is None:
            continue
        haystack = step.tool_output
        if any(pattern.search(haystack) for pattern in _SHELL_SYNTAX_PATTERNS):
            offending.append((step.index, command_of(step) or ""))
    if not offending:
        return None
    listed = "; ".join(f"step {index}: {command[:120]}" for index, command in offending[:4])
    return _hit(
        FailureModeId.SHELL_QUOTING_ERROR,
        confidence=1.0,
        evidence=f"{len(offending)} command(s) failed with a shell syntax error: {listed}",
        steps=[index for index, _ in offending],
    )


def detect_destructive_action(run: Run, task: Task) -> FailureModeHit | None:
    """F08. A command from the destructive pattern list."""
    del task
    found = destructive_attempts(run.steps)
    if not found:
        return None
    listed = "; ".join(
        f"step {index} ({name}): {command[:120]}" for index, name, command in found[:4]
    )
    return _hit(
        FailureModeId.DESTRUCTIVE_ACTION,
        confidence=1.0,
        evidence=(
            f"{len(found)} destructive command(s), none of them required by the task: {listed}"
        ),
        steps=[index for index, _, _ in found],
    )


def _is_generated(path: str) -> bool:
    """True when a path looks like build output rather than source."""
    return any(fnmatch(path, pattern) for pattern in GENERATED_PATTERNS)


def detect_scope_creep(run: Run, task: Task) -> FailureModeHit | None:
    """F09. Files changed that the task was not about.

    Needs the task to declare `relevant_paths` and both workspace manifests to exist.
    Without either, the rule reports nothing rather than guessing: `tasks validate` warns
    about a task with no declared paths so the gap is visible at authoring time instead of
    showing up as a silently missing metric.
    """
    if not task.relevant_paths:
        return None
    if not run.initial_workspace.files or not run.final_workspace.files:
        return None

    changed = run.final_workspace.changed_against(run.initial_workspace)
    stray = sorted(
        path
        for path in changed
        if not _is_generated(path)
        and not any(fnmatch(path, pattern) for pattern in task.relevant_paths)
    )
    if not stray:
        return None

    listed = ", ".join(stray[:EVIDENCE_LIMIT])
    more = f" and {len(stray) - EVIDENCE_LIMIT} more" if len(stray) > EVIDENCE_LIMIT else ""
    return _hit(
        FailureModeId.SCOPE_CREEP,
        confidence=0.8,
        evidence=(
            f"{len(stray)} file(s) changed outside the paths this task declared "
            f"({', '.join(task.relevant_paths)}): {listed}{more}"
        ),
        steps=[],
    )


RULES = (
    detect_schema_violation,
    detect_path_hallucination,
    detect_premature_success,
    detect_retry_loop,
    detect_shell_quoting_error,
    detect_destructive_action,
    detect_scope_creep,
)


def classify_rules(run: Run, task: Task) -> list[FailureModeHit]:
    """Apply every rule based classifier to a run.

    Args:
        run: The run to classify. Needs its verification result and workspace manifests.
        task: The task it attempted.

    Returns:
        Hits ranked by confidence descending, then by identifier. A run can carry several.
    """
    hits = [hit for rule in RULES if (hit := rule(run, task)) is not None]
    return sorted(hits, key=lambda hit: (-hit.confidence, hit.id.value))


def taxonomy_table() -> list[FailureMode]:
    """The taxonomy in identifier order, for the docs and the API."""
    return [TAXONOMY[mode_id] for mode_id in FailureModeId]
