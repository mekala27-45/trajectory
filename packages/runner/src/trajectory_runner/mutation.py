"""Mutation testing of the hidden tests: how much of a wrong answer do they catch.

A hidden test suite is a reward function. Under reinforcement learning a policy is pushed
toward whatever the reward pays for, so the question that matters about a verifier is not
whether the reference passes it, which every task here already proves, but whether a
solution that is almost right passes it too. A suite that cannot tell the reference from a
near miss pays the near miss in full, and a policy trained on it learns the near miss.

This measures that directly. The reference playbook is replayed with one small change in
one of the files it writes, the kind of slip a model makes: a comparison off by one, a sign
flipped, a condition inverted, a constant nudged. The hidden tests run on the result. A
mutant the tests fail is killed; one they pass survived, and every survivor is a specific,
readable gap in what the reward checks. The mutation score is killed over total.

Every change is applied where the reference writes the file, before the rest of the
playbook runs, so a build step that follows compiles the mutant rather than the original.
Each task is first replayed with no change at all, and a task whose unchanged replay does
not pass is reported as such rather than scored. A mutant that breaks the build or crashes
the tests is still killed, and is counted separately, because catching a crash says less
about a suite than catching a wrong answer does.

The operators are the classic ones and deliberately small. A surviving mutant is not
automatically a weak test: some mutants are equivalent to the original, and no test could
tell them apart. The report lists every survivor so a reader can judge which.
"""

from __future__ import annotations

import ast
import random
import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import structlog

from trajectory_core.models import PlaybookStep, ReferencePlaybook, SandboxBackend, ToolName
from trajectory_runner.loader import LoadedTask
from trajectory_runner.sandbox import Sandbox, build_sandbox
from trajectory_runner.verifier import verify

log = structlog.get_logger(__name__)

PYTHON_SUFFIXES = (".py",)
C_LIKE_SUFFIXES = (".go", ".ts", ".mts", ".js", ".mjs")
SQL_SUFFIXES = (".sql",)
TEXT_SUFFIXES = (".txt",)
MUTABLE_SUFFIXES = PYTHON_SUFFIXES + C_LIKE_SUFFIXES + SQL_SUFFIXES + TEXT_SUFFIXES

KILLED = "killed"
KILLED_BY_CRASH = "killed by a crash"
SURVIVED = "survived"
NOT_RUN = "not run"


@dataclass(frozen=True, slots=True)
class Mutant:
    """One small change to one file the reference writes.

    `content` is the file as the mutant has it. `step` and `field` say where in the
    playbook the change goes: the content of the step that writes the file, or, for a
    reference that edits the file with `sed`, the command of that step.
    """

    path: str
    line: int
    operator: str
    before: str
    after: str
    content: str
    step: int = -1
    field: str = "content"

    @property
    def label(self) -> str:
        """Where and what, short enough for a table cell."""
        where = f"{self.path}:{self.line}" if self.line else f"{self.path} (sed)"
        return f"{where} {self.before} -> {self.after}"


@dataclass(slots=True)
class MutantResult:
    """What the hidden tests made of one mutant."""

    mutant: Mutant
    outcome: str
    tests_passed: int = 0
    tests_total: int = 0
    detail: str = ""


@dataclass(slots=True)
class TaskMutation:
    """The mutation result for one task."""

    task_id: str
    files: list[str] = field(default_factory=list)
    sites: int = 0
    control_passed: bool = False
    control_detail: str = ""
    results: list[MutantResult] = field(default_factory=list)

    @property
    def killed(self) -> int:
        """Mutants the hidden tests failed, for any reason."""
        return sum(1 for r in self.results if r.outcome in (KILLED, KILLED_BY_CRASH))

    @property
    def killed_by_assertion(self) -> int:
        """Mutants a hidden test failed while the rest of the suite still ran."""
        return sum(1 for r in self.results if r.outcome == KILLED)

    @property
    def survived(self) -> int:
        """Mutants the hidden tests passed."""
        return sum(1 for r in self.results if r.outcome == SURVIVED)

    @property
    def scored(self) -> int:
        """Mutants with a verdict."""
        return self.killed + self.survived

    @property
    def score(self) -> float | None:
        """Killed over scored, or None when nothing could be scored."""
        return round(self.killed / self.scored, 6) if self.scored else None


# ------------------------------------------------------------------- python operators

_COMPARE_SWAPS: dict[type[ast.cmpop], type[ast.cmpop]] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
}
_BINOP_SWAPS: dict[type[ast.operator], type[ast.operator]] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.FloorDiv: ast.Mult,
}
_BOOL_SWAPS: dict[type[ast.boolop], type[ast.boolop]] = {ast.And: ast.Or, ast.Or: ast.And}
_SYMBOL: dict[type[ast.AST], str] = {
    ast.Lt: "<",
    ast.LtE: "<=",
    ast.Gt: ">",
    ast.GtE: ">=",
    ast.Eq: "==",
    ast.NotEq: "!=",
    ast.In: "in",
    ast.NotIn: "not in",
    ast.Is: "is",
    ast.IsNot: "is not",
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.And: "and",
    ast.Or: "or",
}


def _python_sites(tree: ast.Module) -> list[tuple[ast.AST, str, int]]:
    """Every node a python operator can change, with the operator's name and the line."""
    sites: list[tuple[ast.AST, str, int]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Compare):
            sites.extend(
                (node, f"compare:{index}", line)
                for index, op in enumerate(node.ops)
                if type(op) in _COMPARE_SWAPS
            )
        elif isinstance(node, ast.BinOp | ast.AugAssign) and type(node.op) in _BINOP_SWAPS:
            sites.append((node, "arithmetic", line))
        elif isinstance(node, ast.BoolOp):
            sites.append((node, "logic", line))
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            sites.append((node, "negation", line))
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            sites.append((node, "boolean", line))
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, int)
            and not isinstance(node.value, bool)
        ):
            sites.append((node, "constant", line))
    return sites


class _Apply(ast.NodeTransformer):
    """Apply one operator to one node, found by identity."""

    def __init__(self, target: ast.AST, operator: str) -> None:
        self.target = target
        self.operator = operator
        self.before = ""
        self.after = ""

    def generic_visit(self, node: ast.AST) -> ast.AST:
        node = super().generic_visit(node)
        if node is not self.target:
            return node
        if isinstance(node, ast.Compare) and self.operator.startswith("compare:"):
            index = int(self.operator.split(":", 1)[1])
            old = node.ops[index]
            new = _COMPARE_SWAPS[type(old)]()
            node.ops[index] = new
            self.before, self.after = _SYMBOL[type(old)], _SYMBOL[type(new)]
        elif isinstance(node, ast.BinOp | ast.AugAssign):
            old_op = node.op
            new_op = _BINOP_SWAPS[type(old_op)]()
            node.op = new_op
            self.before, self.after = _SYMBOL[type(old_op)], _SYMBOL[type(new_op)]
        elif isinstance(node, ast.BoolOp):
            old_bool = node.op
            new_bool = _BOOL_SWAPS[type(old_bool)]()
            node.op = new_bool
            self.before, self.after = _SYMBOL[type(old_bool)], _SYMBOL[type(new_bool)]
        elif isinstance(node, ast.UnaryOp):
            self.before, self.after = "not x", "x"
            return node.operand
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            self.before, self.after = str(node.value), str(not node.value)
            node.value = not node.value
        elif isinstance(node, ast.Constant) and isinstance(node.value, int):
            self.before, self.after = str(node.value), str(node.value + 1)
            node.value = node.value + 1
        return node


def python_mutants(path: str, source: str) -> list[Mutant]:
    """Every single-operator mutant of a python file, or none when it does not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    mutants: list[Mutant] = []
    for index, (_, operator, line) in enumerate(_python_sites(tree)):
        # A fresh tree per mutant, so one change never leaks into the next.
        fresh = ast.parse(source)
        node, _, _ = _python_sites(fresh)[index]
        applier = _Apply(node, operator)
        mutated = ast.fix_missing_locations(applier.visit(fresh))
        if not applier.before:
            continue
        mutants.append(
            Mutant(
                path=path,
                line=line,
                operator=operator.split(":", 1)[0],
                before=applier.before,
                after=applier.after,
                content=ast.unparse(mutated) + "\n",
            )
        )
    return mutants


# ---------------------------------------------------------------------- text operators

# Each rule finds a token outside strings and comments, and replaces it. The spacing rules
# keep a comparison apart from a generic type argument, and a binary sign apart from a
# unary one, so most mutants still compile and are about behaviour rather than syntax.
Rule = tuple[str, re.Pattern[str], Callable[[str], str]]


def _swap(table: dict[str, str]) -> Callable[[str], str]:
    """A replacement that looks the token up, case insensitively for SQL keywords."""
    lowered = {key.lower(): value for key, value in table.items()}
    return lambda token: table.get(token) or lowered[token.lower()]


def _plus_one(token: str) -> str:
    return str(int(token) + 1)


_COMPARE = {"<=": "<", ">=": ">", "<": "<=", ">": ">="}
_EQUALITY = {"===": "!==", "!==": "===", "==": "!=", "!=": "=="}
_TEXT_RULES: tuple[Rule, ...] = (
    ("compare", re.compile(r"(?<=\s)(<=|>=|<|>)(?=\s)"), _swap(_COMPARE)),
    ("equality", re.compile(r"(?<=\s)(===|!==|==|!=)(?=\s)"), _swap(_EQUALITY)),
    ("logic", re.compile(r"(&&|\|\|)"), _swap({"&&": "||", "||": "&&"})),
    ("arithmetic", re.compile(r"(?<=\s)(\+|-)(?=\s)"), _swap({"+": "-", "-": "+"})),
    ("boolean", re.compile(r"\b(true|false)\b"), _swap({"true": "false", "false": "true"})),
    ("constant", re.compile(r"(?<![\w.])(\d+)(?![\w.])"), _plus_one),
)
_SQL_RULES: tuple[Rule, ...] = (
    ("compare", re.compile(r"(?<=\s)(<=|>=|<|>)(?=\s)"), _swap(_COMPARE)),
    ("equality", re.compile(r"(?<=\s)(=|<>)(?=\s)"), _swap({"=": "<>", "<>": "="})),
    ("logic", re.compile(r"\b(AND|OR)\b", re.IGNORECASE), _swap({"AND": "OR", "OR": "AND"})),
    (
        "null",
        re.compile(r"\b(IS NOT NULL|IS NULL)\b", re.IGNORECASE),
        _swap({"IS NOT NULL": "IS NULL", "IS NULL": "IS NOT NULL"}),
    ),
    (
        "boolean",
        re.compile(r"\b(true|false)\b", re.IGNORECASE),
        _swap({"true": "false", "false": "true"}),
    ),
    ("constant", re.compile(r"(?<![\w.])(\d+)(?![\w.])"), _plus_one),
)
# Version pins: an inverted pin or a nudged version is the near miss a resolver allows.
_PIN_RULES: tuple[Rule, ...] = (
    (
        "pin",
        re.compile(r"(==|>=|<=|<|>)(?=\s*\d)"),
        _swap({"==": "!=", ">=": "<", "<=": ">", "<": ">=", ">": "<="}),
    ),
    ("version", re.compile(r"(?<=[=<>])(\d+)"), _plus_one),
)

_C_LIKE_SKIP = re.compile(
    r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'|`[^`]*`', re.DOTALL
)
_SQL_SKIP = re.compile(r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\"[^\"]*\"", re.DOTALL)
_HASH_SKIP = re.compile(r"#[^\n]*")


def _code_spans(source: str, skip: re.Pattern[str]) -> Iterator[tuple[int, int]]:
    """The stretches of a file that are code rather than strings or comments."""
    position = 0
    for match in skip.finditer(source):
        if match.start() > position:
            yield position, match.start()
        position = match.end()
    if position < len(source):
        yield position, len(source)


def text_mutants(path: str, source: str) -> list[Mutant]:
    """Every single-token mutant of a non python file, outside its strings and comments."""
    suffix = PurePosixPath(path).suffix
    if suffix in SQL_SUFFIXES:
        rules, skip = _SQL_RULES, _SQL_SKIP
    elif suffix in TEXT_SUFFIXES:
        rules, skip = _PIN_RULES, _HASH_SKIP
    else:
        rules, skip = _TEXT_RULES, _C_LIKE_SKIP
    mutants: list[Mutant] = []
    for start, end in _code_spans(source, skip):
        for operator, pattern, change in rules:
            for match in pattern.finditer(source, start, end):
                token = next(group for group in match.groups() if group is not None)
                at = match.start(match.lastindex or 0)
                replacement = change(token)
                content = source[:at] + replacement + source[at + len(token) :]
                mutants.append(
                    Mutant(
                        path=path,
                        line=source.count("\n", 0, at) + 1,
                        operator=operator,
                        before=token,
                        after=replacement,
                        content=content,
                    )
                )
    return mutants


# ------------------------------------------------------------------------ the playbook


def solution_files(playbook: ReferencePlaybook) -> dict[str, str]:
    """The source files the reference writes, at their final content.

    Test files and data are left out: mutating a test the agent may edit, or a fixture,
    says nothing about the hidden tests' grip on the solution.
    """
    files: dict[str, str] = {}
    for step in playbook.steps:
        if step.tool is not ToolName.WRITE_FILE:
            continue
        path = str(step.args.get("path", ""))
        parts = PurePosixPath(path).parts
        name = parts[-1] if parts else ""
        is_test = any(part.startswith("test") for part in parts[:-1])
        is_test = is_test or name.startswith("test_") or ".test." in name
        is_test = is_test or name.endswith("_test.go")
        if path.endswith(MUTABLE_SUFFIXES) and not is_test:
            files[path] = str(step.args.get("content", ""))
    return files


# A reference that edits in place: sed -i 's/old/new/' path, with any one character as the
# delimiter. Only the replacement is mutated, because it is the part the reference wrote.
_SED = re.compile(
    r"""^sed\s+-i\s+'s(?P<d>[^\w\s'])(?P<old>(?:(?!(?P=d)).)*)(?P=d)"""
    r"""(?P<new>(?:(?!(?P=d)).)*)(?P=d)(?P<flags>[a-z]*)'\s+(?P<path>\S+)\s*$"""
)


def _last_write(playbook: ReferencePlaybook, path: str) -> int:
    return max(
        index
        for index, step in enumerate(playbook.steps)
        if step.tool is ToolName.WRITE_FILE and step.args.get("path") == path
    )


def _sed_mutants(playbook: ReferencePlaybook) -> list[Mutant]:
    """Mutants of the replacement text in a `sed -i` edit the reference makes."""
    mutants: list[Mutant] = []
    for index, step in enumerate(playbook.steps):
        if step.tool is not ToolName.BASH:
            continue
        command = str(step.args.get("command", "")).strip()
        match = _SED.match(command)
        if not match or not match.group("path").endswith(MUTABLE_SUFFIXES):
            continue
        start, end = match.span("new")
        replacement = match.group("new")
        for changed in text_mutants("replacement.txt.c", replacement):
            mutants.append(
                Mutant(
                    path=match.group("path"),
                    line=0,
                    operator=changed.operator,
                    before=changed.before,
                    after=changed.after,
                    content=command[:start] + changed.content + command[end:],
                    step=index,
                    field="command",
                )
            )
    return mutants


def mutants_for(playbook: ReferencePlaybook) -> list[Mutant]:
    """Every mutant of every solution file the reference writes or edits."""
    mutants: list[Mutant] = []
    for path, content in solution_files(playbook).items():
        found = (
            python_mutants(path, content)
            if path.endswith(PYTHON_SUFFIXES)
            else text_mutants(path, content)
        )
        at = _last_write(playbook, path)
        mutants.extend(
            Mutant(
                path=m.path,
                line=m.line,
                operator=m.operator,
                before=m.before,
                after=m.after,
                content=m.content,
                step=at,
                field="content",
            )
            for m in found
        )
    mutants.extend(_sed_mutants(playbook))
    return mutants


def sample(mutants: list[Mutant], count: int, seed: str) -> list[Mutant]:
    """A fixed sample, the same on every machine for the same task and count."""
    if len(mutants) <= count:
        return list(mutants)
    rng = random.Random(seed)  # noqa: S311  a reproducible sample, not a secret
    chosen = rng.sample(range(len(mutants)), count)
    return [mutants[index] for index in sorted(chosen)]


def mutated_playbook(playbook: ReferencePlaybook, mutant: Mutant | None) -> ReferencePlaybook:
    """The reference with the mutant put where the reference writes or edits that file."""
    if mutant is None:
        return playbook
    steps = list(playbook.steps)
    original = steps[mutant.step]
    steps[mutant.step] = PlaybookStep(
        tool=original.tool,
        args={**original.args, mutant.field: mutant.content},
        note=original.note,
    )
    return playbook.model_copy(update={"steps": steps})


def _replay(sandbox: Sandbox, loaded: LoadedTask, playbook: ReferencePlaybook) -> None:
    """Do what the playbook does, step by step, without an agent loop around it."""
    task = loaded.task
    for step in playbook.steps:
        if step.tool is ToolName.WRITE_FILE:
            sandbox.write_file(str(step.args["path"]), str(step.args["content"]))
        elif step.tool is ToolName.BASH:
            sandbox.exec(
                str(step.args["command"]),
                timeout_s=task.command_timeout_seconds,
                cap_bytes=65_536,
            )
        elif step.tool is ToolName.FINISH:
            return


def _attempt(
    loaded: LoadedTask,
    mutant: Mutant | None,
    *,
    backend: SandboxBackend,
    image_tag: str | None,
    allow_local_override: bool,
) -> MutantResult | tuple[bool, str]:
    """Replay one playbook, mutated or not, and run the hidden tests on the result."""
    playbook = mutated_playbook(loaded.playbook, mutant)
    try:
        with build_sandbox(
            loaded.task,
            loaded.directory,
            backend,
            image_tag=image_tag,
            allow_local_override=allow_local_override,
        ) as sandbox:
            _replay(sandbox, loaded, playbook)
            verification = verify(loaded.task, sandbox)
    except Exception as exc:  # noqa: BLE001  one mutant, one verdict, whatever went wrong
        if mutant is None:
            return False, f"{type(exc).__name__}: {str(exc)[:300]}"
        return MutantResult(mutant, NOT_RUN, detail=f"{type(exc).__name__}: {str(exc)[:300]}")

    if mutant is None:
        detail = f"{verification.tests_passed}/{verification.tests_total}"
        return verification.passed, detail
    if verification.passed:
        outcome = SURVIVED
    elif verification.tests_passed == 0 or not verification.parse_ok:
        outcome = KILLED_BY_CRASH
    else:
        outcome = KILLED
    return MutantResult(
        mutant,
        outcome,
        tests_passed=verification.tests_passed,
        tests_total=verification.tests_total,
    )


def mutate_task(
    loaded: LoadedTask,
    *,
    backend: SandboxBackend,
    per_task: int,
    parallel: int = 1,
    image_tag: str | None = None,
    allow_local_override: bool = False,
) -> TaskMutation:
    """Run the unchanged control and then a fixed sample of mutants for one task."""
    found = mutants_for(loaded.playbook)
    report = TaskMutation(
        task_id=loaded.id,
        files=sorted(solution_files(loaded.playbook)),
        sites=len(found),
    )
    if not found:
        report.control_detail = (
            "the reference changes its source with commands, not by writing a file"
            if not report.files
            else "no operator applies to the source the reference writes"
        )
        return report

    control = _attempt(
        loaded,
        None,
        backend=backend,
        image_tag=image_tag,
        allow_local_override=allow_local_override,
    )
    assert isinstance(control, tuple)
    report.control_passed, report.control_detail = control
    if not report.control_passed:
        log.warning("mutation.control_failed", task=loaded.id, detail=report.control_detail)
        return report

    chosen = sample(found, per_task, seed=loaded.id)

    def one(mutant: Mutant) -> MutantResult:
        result = _attempt(
            loaded,
            mutant,
            backend=backend,
            image_tag=image_tag,
            allow_local_override=allow_local_override,
        )
        assert isinstance(result, MutantResult)
        log.info("mutation.mutant", task=loaded.id, mutant=mutant.label, outcome=result.outcome)
        return result

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        report.results = list(pool.map(one, chosen))
    return report


def task_record(report: TaskMutation) -> dict[str, object]:
    """One task's result, as the published report stores it."""
    return {
        "task_id": report.task_id,
        "files": report.files,
        "sites": report.sites,
        "control_passed": report.control_passed,
        "control_detail": report.control_detail,
        "scored": report.scored,
        "killed": report.killed,
        "killed_by_assertion": report.killed_by_assertion,
        "survived": report.survived,
        "score": report.score,
        "mutants": [
            {
                "label": result.mutant.label,
                "path": result.mutant.path,
                "line": result.mutant.line,
                "operator": result.mutant.operator,
                "before": result.mutant.before,
                "after": result.mutant.after,
                "outcome": result.outcome,
                "tests_passed": result.tests_passed,
                "tests_total": result.tests_total,
                "detail": result.detail,
            }
            for result in report.results
        ],
    }


def suite_record(
    reports: list[TaskMutation],
    *,
    suite: str,
    backend: SandboxBackend,
    per_task: int,
    harness_version: str,
    generated_at: str,
) -> dict[str, object]:
    """The whole suite's result: per task, and pooled over every scored mutant."""
    scored = sum(report.scored for report in reports)
    killed = sum(report.killed for report in reports)
    return {
        "suite": suite,
        "backend": backend.value,
        "per_task": per_task,
        "harness_version": harness_version,
        "generated_at": generated_at,
        "scored": scored,
        "killed": killed,
        "killed_by_assertion": sum(report.killed_by_assertion for report in reports),
        "survived": sum(report.survived for report in reports),
        "score": round(killed / scored, 6) if scored else None,
        "tasks": [task_record(report) for report in reports],
    }
