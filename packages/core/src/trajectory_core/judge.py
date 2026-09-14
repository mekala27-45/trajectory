"""The rubric judge.

Three of the ten failure modes need judgement about intent rather than a pattern match.
Whether an agent's next action addressed what a test failure actually said is not something
a regular expression can decide, and pretending otherwise would produce a taxonomy that
looks rigorous and is not.

So those three go to a language model, under three constraints.

The output is structured by construction. The judge is given exactly one tool whose
parameters are the verdict schema, and `tool_choice` forces it, so the reply arrives as
JSON that Pydantic validates. There is no free text and nothing is recovered with a
regular expression. The verdict also has one named field per mode rather than a list of
findings, which removes the entire class of errors where a model returns duplicates, omits
one, or invents a fourth.

The judge is asked for evidence, not just a verdict. A finding with no step index and no
quote is an assertion, and nobody should change their model on the strength of an
assertion.

The judge's own reliability is measured and published. Running it twice at different
temperatures over a sample and reporting the agreement is the difference between a
taxonomy and a horoscope. An evaluation harness that does not measure the reliability of
its own judge has no business grading anyone else.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import structlog
from pydantic import Field

from trajectory_core.failure_modes import TAXONOMY
from trajectory_core.models import (
    Detector,
    FailureModeHit,
    FailureModeId,
    Run,
    StrictModel,
    Task,
)

log = structlog.get_logger(__name__)

VERDICT_TOOL_NAME = "record_verdict"
DEFAULT_STEP_OUTPUT_CHARS = 900
DEFAULT_TRAJECTORY_CHARS = 60_000


class ModeJudgement(StrictModel):
    """The judge's decision about one failure mode."""

    present: bool = Field(description="Whether this failure mode occurred in this trajectory.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are. Use 0.0 when the mode is absent. Use below 0.6 when the "
            "trajectory is ambiguous rather than rounding up."
        ),
    )
    evidence: str = Field(
        default="",
        max_length=1200,
        description=(
            "Quote or paraphrase the specific steps that show this. Leave empty when the "
            "mode is absent. Never assert the mode without pointing at something."
        ),
    )
    step_indices: list[int] = Field(
        default_factory=list,
        description="Step numbers that show the mode, so a human can check the finding.",
    )


class JudgeVerdict(StrictModel):
    """The judge's full reply.

    One named field per judged mode rather than a list, so a model cannot return
    duplicates, omit one, or invent a fourth.
    """

    context_drift: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Metric 8. How far the final third of the trajectory has drifted from the "
            "stated goal. 0.0 means it is still working directly on the task. 1.0 means it "
            "is working on something unrelated. Judge the final third only."
        ),
    )
    drift_reasoning: str = Field(
        default="",
        max_length=1200,
        description="One or two sentences on what the final third was actually doing.",
    )
    ignored_test_output: ModeJudgement = Field(
        description=(
            "F06. The agent ran the tests, the tests failed, and its next action does not "
            "address what the failure said. Not present if it never ran the tests, and not "
            "present if it took a reasonable diagnostic step such as reading the file named "
            "in the traceback."
        )
    )
    long_horizon_context_loss: ModeJudgement = Field(
        description=(
            "F07. The agent re-solved something it had already solved, or contradicted a "
            "conclusion it had reached correctly earlier in the same run. Repeating a "
            "command is not this on its own; the agent has to have lost the conclusion."
        )
    )
    environment_mismatch: ModeJudgement = Field(
        description=(
            "F10. The agent used the wrong package manager, interpreter or build tool for "
            "the environment: reaching for the network in an offline task, npm in a Python "
            "project, a tool the image does not have when an equivalent one is installed."
        )
    )

    def mode_map(self) -> dict[FailureModeId, ModeJudgement]:
        """Map the named fields back to taxonomy identifiers."""
        return {
            FailureModeId.IGNORED_TEST_OUTPUT: self.ignored_test_output,
            FailureModeId.LONG_HORIZON_CONTEXT_LOSS: self.long_horizon_context_loss,
            FailureModeId.ENVIRONMENT_MISMATCH: self.environment_mismatch,
        }


class JudgeError(RuntimeError):
    """Raised when the judge could not produce a valid verdict."""


class JudgeClient(Protocol):
    """What the judge needs from a model: one validated verdict."""

    model: str

    def verdict(self, system: str, user: str, temperature: float) -> JudgeVerdict:
        """Return a validated verdict for one trajectory."""
        ...


# ------------------------------------------------------------------- rendering


def render_step(step: Any, *, output_chars: int) -> str:  # noqa: ANN401  duck typed for Step
    """Render one step compactly enough that a long trajectory still fits in a prompt."""
    args = json.dumps(step.tool_args, default=str)
    if len(args) > 400:
        args = args[:400] + "...(truncated)"
    output = step.tool_output or ""
    if len(output) > output_chars:
        head = output[: output_chars // 2]
        tail = output[-output_chars // 2 :]
        output = f"{head}\n...({len(output) - output_chars} chars elided)...\n{tail}"
    parts = [f"### step {step.index}: {step.tool_name}"]
    if step.thought:
        parts.append(f"reasoning: {step.thought.strip()[:600]}")
    parts.append(f"arguments: {args}")
    if step.exit_code is not None:
        parts.append(f"exit code: {step.exit_code}")
    if step.schema_violation:
        parts.append("this call was rejected by the harness as malformed")
    parts.append(f"output:\n{output.strip() or '(no output)'}")
    return "\n".join(parts)


def render_trajectory(
    run: Run,
    task: Task,
    *,
    output_chars: int = DEFAULT_STEP_OUTPUT_CHARS,
    total_chars: int = DEFAULT_TRAJECTORY_CHARS,
) -> str:
    """Render a run for the judge.

    When the whole trajectory does not fit, the middle is dropped rather than the end. The
    final third is what metric 8 is about, and the first few steps are where an agent's
    plan is visible, so those are the parts worth keeping.
    """
    header = [
        f"TASK: {task.id} ({task.language.value}, difficulty {task.difficulty} of 5)",
        f"TASK STATEMENT GIVEN TO THE AGENT:\n{task.agent_prompt.strip()}",
        f"REFERENCE SOLUTION LENGTH: {task.reference_step_count} steps",
        f"RUN STATUS: {run.status.value}",
        f"STEPS TAKEN: {len(run.steps)}",
    ]
    if run.verification is not None:
        header.append(
            f"HIDDEN TEST RESULT: {run.verification.tests_passed} of "
            f"{run.verification.tests_total} passed"
        )
    if run.context_compressed:
        header.append(
            "NOTE: the harness compressed older tool outputs during this run, so the agent "
            "saw a summary in place of some earlier output."
        )

    rendered = [render_step(step, output_chars=output_chars) for step in run.steps]
    body = "\n\n".join(rendered)

    if len(body) > total_chars:
        keep = total_chars // 2
        front: list[str] = []
        size = 0
        for text in rendered:
            if size + len(text) > keep:
                break
            front.append(text)
            size += len(text)
        back: list[str] = []
        size = 0
        for text in reversed(rendered):
            if size + len(text) > keep:
                break
            back.append(text)
            size += len(text)
        elided = len(rendered) - len(front) - len(back)
        body = "\n\n".join(
            [*front, f"\n... {elided} middle step(s) elided to fit ...\n", *reversed(back)]
        )

    return "\n".join(header) + "\n\nTRAJECTORY:\n\n" + body


SYSTEM_PROMPT = """\
You are reviewing one attempt by a coding agent at a software engineering task, for an
evaluation harness. Your job is to classify three specific failure modes and score one
metric. You are not grading the code and you are not deciding whether the task was solved:
the hidden test result is given to you.

Rules for your verdict:

- Judge only what the trajectory shows. Do not infer intent that is not visible in the
  reasoning or the actions.
- Point at step numbers. A finding without a step number is useless to the team reading it.
- When the trajectory is genuinely ambiguous, say so with a confidence below 0.6 rather
  than rounding to certain. An unreliable judge that sounds certain is worse than one that
  hedges.
- A failed attempt is not automatically any of these modes. An agent that read the failure,
  formed a wrong hypothesis, and tested it carefully has none of them.
- Record your verdict by calling the record_verdict tool. Do not reply with prose.
"""


def build_user_prompt(run: Run, task: Task, **render_kwargs: Any) -> str:  # noqa: ANN401
    """Build the judge prompt for one run, with the rubric restated inline."""
    rubric = "\n".join(
        f"- {mode_id.value} {TAXONOMY[mode_id].name}: {TAXONOMY[mode_id].definition} "
        f"Example: {TAXONOMY[mode_id].example}"
        for mode_id in (
            FailureModeId.IGNORED_TEST_OUTPUT,
            FailureModeId.LONG_HORIZON_CONTEXT_LOSS,
            FailureModeId.ENVIRONMENT_MISMATCH,
        )
    )
    return (
        f"{render_trajectory(run, task, **render_kwargs)}\n\n"
        f"MODES TO CLASSIFY:\n{rubric}\n\n"
        "CONTEXT DRIFT (metric 8): score only the final third of the trajectory. 0.0 means "
        "it is still working directly on the stated goal. 0.5 means it has partly wandered. "
        "1.0 means it is working on something unrelated to the task. A run that stayed on "
        "task and failed anyway scores 0.0.\n\n"
        "Call record_verdict now."
    )


# -------------------------------------------------------------------- litellm


class LiteLLMJudge:
    """A judge backed by any model LiteLLM can reach.

    The verdict schema is presented as a single tool and `tool_choice` forces it, which is
    the most portable way to get schema-conformant output across vendors: every model this
    harness supports already has function calling, because the agent loop requires it.
    """

    def __init__(
        self,
        model: str,
        *,
        request_timeout_s: float = 180.0,
        max_attempts: int = 3,
        api_base: str | None = None,
    ) -> None:
        """Configure the judge."""
        self.model = model
        self.request_timeout_s = request_timeout_s
        self.max_attempts = max_attempts
        self.api_base = api_base
        self._litellm: Any | None = None

    def _lib(self) -> Any:  # noqa: ANN401  litellm is untyped
        """Import LiteLLM lazily, so an offline run never touches it."""
        if self._litellm is None:
            import litellm

            litellm.drop_params = True
            litellm.suppress_debug_info = True
            self._litellm = litellm
        return self._litellm

    @staticmethod
    def tool_definition() -> dict[str, Any]:
        """The verdict schema, as a function tool."""
        schema = JudgeVerdict.model_json_schema()
        return {
            "type": "function",
            "function": {
                "name": VERDICT_TOOL_NAME,
                "description": "Record your verdict for this trajectory.",
                "parameters": schema,
            },
        }

    def verdict(self, system: str, user: str, temperature: float) -> JudgeVerdict:
        """Ask for one verdict, retrying on a reply that does not validate.

        A model that returns something the schema rejects is told exactly what was wrong
        and asked again. After the attempts are exhausted the failure propagates, because a
        judge that silently substitutes a default verdict would poison the published
        numbers in a way nobody could see.
        """
        litellm = self._lib()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        last_error: str | None = None

        for attempt in range(1, self.max_attempts + 1):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                "tools": [self.tool_definition()],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": VERDICT_TOOL_NAME},
                },
                "temperature": temperature,
                "timeout": self.request_timeout_s,
            }
            if self.api_base:
                kwargs["api_base"] = self.api_base

            try:
                response = litellm.completion(**kwargs)
                calls = getattr(response.choices[0].message, "tool_calls", None) or []
                if not calls:
                    raise JudgeError("the judge replied without calling record_verdict")
                raw = calls[0].function.arguments
                payload = json.loads(raw) if isinstance(raw, str) else raw
                return JudgeVerdict.model_validate(payload)
            except Exception as exc:  # noqa: BLE001  vendor exception types vary widely
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "judge.retry", model=self.model, attempt=attempt, error=last_error[:300]
                )
                if attempt == self.max_attempts:
                    break
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous reply was rejected: {last_error}. Call "
                            f"{VERDICT_TOOL_NAME} with arguments that satisfy its schema."
                        ),
                    }
                )

        raise JudgeError(
            f"the judge ({self.model}) produced no valid verdict in {self.max_attempts} "
            f"attempt(s). Last error: {last_error}"
        )


class ScriptedJudge:
    """Returns prepared verdicts in order. Used by the tests and by nothing else."""

    model = "stub:judge"

    def __init__(self, verdicts: Sequence[JudgeVerdict]) -> None:
        """Store the verdicts to replay."""
        self._verdicts = list(verdicts)
        self.calls: list[tuple[str, float]] = []

    def verdict(self, system: str, user: str, temperature: float) -> JudgeVerdict:
        """Return the next prepared verdict."""
        del system
        self.calls.append((user[:80], temperature))
        if not self._verdicts:
            raise JudgeError("scripted judge ran out of verdicts")
        return self._verdicts.pop(0)


# ---------------------------------------------------------------------- entry


def judge_run(
    client: JudgeClient,
    run: Run,
    task: Task,
    *,
    temperature: float = 0.0,
) -> JudgeVerdict:
    """Judge one run."""
    return client.verdict(SYSTEM_PROMPT, build_user_prompt(run, task), temperature)


def verdict_to_hits(verdict: JudgeVerdict) -> list[FailureModeHit]:
    """Turn a verdict into failure mode hits, dropping the modes it judged absent."""
    hits: list[FailureModeHit] = []
    for mode_id, judgement in verdict.mode_map().items():
        if not judgement.present:
            continue
        hits.append(
            FailureModeHit(
                id=mode_id,
                name=TAXONOMY[mode_id].name,
                confidence=judgement.confidence,
                detector=Detector.JUDGE,
                evidence=judgement.evidence or "the judge reported no evidence",
                step_indices=judgement.step_indices,
            )
        )
    return sorted(hits, key=lambda hit: (-hit.confidence, hit.id.value))


# ------------------------------------------------------------------ agreement


@dataclass(frozen=True, slots=True)
class ModeAgreement:
    """How often two judge passes agreed about one mode.

    Stored as the full two by two table rather than as a single agreement count, because
    the interesting cases are asymmetric: a mode one pass sees ten times and the other
    sees twice is a different problem from ten scattered disagreements.
    """

    mode_id: FailureModeId
    both_present: int
    first_only: int
    second_only: int
    neither_present: int

    @property
    def n(self) -> int:
        """Runs compared."""
        return self.both_present + self.first_only + self.second_only + self.neither_present

    @property
    def agreements(self) -> int:
        """Runs where the two passes gave the same verdict."""
        return self.both_present + self.neither_present

    @property
    def raw_agreement(self) -> float:
        """Fraction of runs where the two passes gave the same verdict."""
        return round(self.agreements / self.n, 4) if self.n else 0.0

    @property
    def cohens_kappa(self) -> float | None:
        """Agreement corrected for what chance alone would produce.

        Raw agreement flatters a rare mode: two passes that both say "absent" every time
        agree perfectly and have told you nothing. Kappa is None when chance agreement is
        already total, which happens when neither pass ever saw the mode. Reporting 0.0
        there would read as disagreement rather than as "not computable", and that
        distinction is the whole reason this number is published.
        """
        total = self.n
        if not total:
            return None
        first_present = (self.both_present + self.first_only) / total
        second_present = (self.both_present + self.second_only) / total
        observed = self.agreements / total
        chance = first_present * second_present + (1 - first_present) * (1 - second_present)
        if chance >= 1.0:
            return None
        return round((observed - chance) / (1 - chance), 4)


@dataclass(frozen=True, slots=True)
class JudgeAgreement:
    """Self agreement of the judge across two passes at different temperatures."""

    per_mode: dict[FailureModeId, ModeAgreement]
    drift_mean_absolute_difference: float
    drift_max_absolute_difference: float
    n: int

    @property
    def mean_raw_agreement(self) -> float:
        """Average raw agreement across the judged modes."""
        values = [entry.raw_agreement for entry in self.per_mode.values()]
        return round(statistics.fmean(values), 4) if values else 0.0


def measure_agreement(
    first: Sequence[JudgeVerdict], second: Sequence[JudgeVerdict]
) -> JudgeAgreement:
    """Compare two judge passes over the same runs.

    Args:
        first: Verdicts from the first pass, in run order.
        second: Verdicts from the second pass, same runs, same order.

    Returns:
        Per mode agreement and the spread of the drift score.

    Raises:
        ValueError: If the two passes do not cover the same number of runs.
    """
    if len(first) != len(second):
        raise ValueError(
            f"cannot compare {len(first)} verdicts against {len(second)}: the two judge "
            "passes have to cover the same runs in the same order"
        )
    n = len(first)
    per_mode: dict[FailureModeId, ModeAgreement] = {}

    for mode_id in (
        FailureModeId.IGNORED_TEST_OUTPUT,
        FailureModeId.LONG_HORIZON_CONTEXT_LOSS,
        FailureModeId.ENVIRONMENT_MISMATCH,
    ):
        both = first_only = second_only = neither = 0
        for left, right in zip(first, second, strict=True):
            a = left.mode_map()[mode_id].present
            b = right.mode_map()[mode_id].present
            if a and b:
                both += 1
            elif a:
                first_only += 1
            elif b:
                second_only += 1
            else:
                neither += 1
        per_mode[mode_id] = ModeAgreement(
            mode_id=mode_id,
            both_present=both,
            first_only=first_only,
            second_only=second_only,
            neither_present=neither,
        )

    diffs = [abs(a.context_drift - b.context_drift) for a, b in zip(first, second, strict=True)]
    return JudgeAgreement(
        per_mode=per_mode,
        drift_mean_absolute_difference=round(statistics.fmean(diffs), 4) if diffs else 0.0,
        drift_max_absolute_difference=round(max(diffs), 4) if diffs else 0.0,
        n=n,
    )
