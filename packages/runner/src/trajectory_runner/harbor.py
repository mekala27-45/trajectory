"""Export a trajectory task to the Harbor task format.

Harbor is the container task format that several labs, this project's readers included,
already grade in. A benchmark that can only be run by its own harness is a walled garden;
one that round-trips to Harbor can be dropped into an existing evaluation stack without
rewriting a line of it. So every task here exports to a Harbor task directory, and the
export is checked in CI by running the Harbor oracle on it.

The layout written per task, and the one property that makes each piece correct:

    task.toml              limits and metadata, Harbor schema 1.0
    instruction.md         the only thing the agent sees, verbatim from agent_prompt
    environment/
        Dockerfile         the task image, workspace baked in, tests never
        workspace/         the starting tree the Dockerfile copies to /workspace
    tests/
        test.sh            runs after the agent, writes the reward on every path
        run_verify.sh      the task's own verify command, verbatim, no re-quoting
        score.py           a standalone parser, so the verifier needs nothing installed
        verify/            the hidden tests, present only at grading time
    solution/
        solve.sh           the reference playbook replayed, file writes via base64

The hidden tests live under `tests/`, never under `environment/`, because Harbor builds
the environment image from `environment/` and hands it to the agent. A test that rode
along in that image would be readable by the agent, and the benchmark would measure
nothing. This mirrors the invariant the native sandbox enforces by copying `verify/` in
only after the agent has stopped.

The reward file is written on every exit path. A verifier that crashes without writing a
reward is scored by Harbor as a missing result, which is the one outcome that silently
drops a task from an aggregate, so `test.sh` traps its own exit and writes a zero reward
if nothing else did.
"""

from __future__ import annotations

import base64
import shutil
import stat
from pathlib import Path

from trajectory_core.models import Task, ToolName
from trajectory_runner.loader import LoadedTask

HARBOR_VERSION = "1.0"
REWARD_DIR = "/logs/verifier"
DEFAULT_STORAGE_MB = 2048

_FORBIDDEN_COPIES = ("verify", "tests", "reference", "solution")


class HarborExportError(RuntimeError):
    """Raised when a task cannot be exported to a correct Harbor directory."""


# ----------------------------------------------------------------------- TOML


def _toml_basic(value: str) -> str:
    """Render a string as a TOML basic string, collapsing newlines so it stays one line."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("\t", " ")
    )
    return f'"{escaped}"'


def _toml_array(values: list[str]) -> str:
    """Render a list of strings as a TOML inline array."""
    return "[" + ", ".join(_toml_basic(v) for v in values) + "]"


def render_task_toml(task: Task) -> str:
    """Build the Harbor `task.toml` from a trajectory task.

    The agent and verifier timeouts come straight across. The environment block is the
    container ceilings, with a storage default Harbor requires and this project does not
    track. The `trajectory` sub-table carries the identifiers needed to round-trip a run
    back to this harness without reading them out of a path.
    """
    lines = [
        f'version = "{HARBOR_VERSION}"',
        "",
        "[metadata]",
        f"id = {_toml_basic(task.id)}",
        f"title = {_toml_basic(task.title)}",
        f"suite = {_toml_basic(task.suite)}",
        f"language = {_toml_basic(task.language.value)}",
        f"difficulty = {task.difficulty}",
        f"tags = {_toml_array(task.tags)}",
        f"description = {_toml_basic(task.description)}",
        "",
        "[agent]",
        f"timeout_sec = {task.timeout_seconds}",
        f"max_steps = {task.max_steps}",
        "",
        "[verifier]",
        f"timeout_sec = {task.verify_timeout_seconds}",
        "env = {}",
        "",
        "[environment]",
        f"cpus = {task.cpus}",
        f"memory_mb = {task.memory_mb}",
        f"storage_mb = {DEFAULT_STORAGE_MB}",
        "gpus = 0",
        "mcp_servers = []",
        f"network = {str(task.network_allowed).lower()}",
        "",
        "[solution]",
        "env = {}",
        "",
        "[trajectory]",
        f"task_id = {_toml_basic(task.id)}",
        f"verify_parser = {_toml_basic(task.verify_parser.value)}",
        *([f"expected_tests = {task.expected_tests}"] if task.expected_tests else []),
        f"reference_step_count = {task.reference_step_count}",
        "",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------- test.sh


_TEST_SH = r"""#!/usr/bin/env bash
# Grades the agent's work. Harbor runs this after the agent has stopped, with the hidden
# tests present under this directory and the agent's workspace at /workspace. It must
# write a reward to {reward_dir}/reward.json on every path, so a crash is a zero rather
# than a missing result that drops the task from an aggregate.
#
# The two paths default to Harbor's own and can be overridden, so the same verifier can be
# run outside a Harbor container, which is how it is exercised in CI without one.
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
REWARD_DIR="${{LOGS_VERIFIER_DIR:-{reward_dir}}}"
WORKDIR="${{HARBOR_WORKSPACE:-/workspace}}"
mkdir -p "$REWARD_DIR"
OUT="$REWARD_DIR/reward.json"

write_zero() {{
  if [ ! -f "$OUT" ]; then
    printf '%s\n' \
      '{{"reward": 0.0, "partial_credit": 0.0, "tests_passed": 0, "tests_total": 0}}' \
      > "$OUT"
  fi
}}
trap write_zero EXIT

# Run the task's own verify command verbatim, with the working directory and VERIFY_DIR
# the native harness uses, and capture both streams together the way the parser expects.
export VERIFY_DIR="$HERE/verify"
out="$(cd "$WORKDIR" && VERIFY_DIR="$VERIFY_DIR" bash "$HERE/run_verify.sh" 2>&1)"
rc=$?
printf '%s\n' "$out"

printf '%s' "$out" | python3 "$HERE/score.py" --parser {parser} --exit "$rc" --out "$OUT"{expected}
"""


def render_test_sh(task: Task) -> str:
    """The grading entrypoint, parameterised by the task's parser and the reward path."""
    expected = f" --expected {task.expected_tests}" if task.expected_tests else ""
    return _TEST_SH.format(
        reward_dir=REWARD_DIR, parser=task.verify_parser.value, expected=expected
    )


# score.py is a standalone copy of the verifier's parsing. It deliberately imports nothing
# from this project, so the Harbor verifier image needs only a stock Python 3, and it is
# the one place the two code paths are allowed to duplicate: a shared import would drag the
# whole package into every exported task.
_SCORE_PY = r'''#!/usr/bin/env python3
"""Turn captured verifier output into a Harbor reward. Standalone, stock Python 3 only."""

from __future__ import annotations

import argparse
import json
import re
import sys

_PYTEST = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed)\b")
_GO_PASS = re.compile(r"^\s*--- PASS:", re.MULTILINE)
_GO_FAIL = re.compile(r"^\s*--- FAIL:", re.MULTILINE)
_TAP_OK = re.compile(r"^\s*ok\s+\d+", re.MULTILINE)
_TAP_NOT_OK = re.compile(r"^\s*not ok\s+\d+", re.MULTILINE)


def parse_pytest(output):
    counts = {}
    for match in _PYTEST.finditer(output):
        key = match.group(2).rstrip("s")
        counts[key] = counts.get(key, 0) + int(match.group(1))
    if not counts:
        return None
    passed = counts.get("passed", 0) + counts.get("xpassed", 0)
    failed = counts.get("failed", 0) + counts.get("xfailed", 0)
    errors = counts.get("error", 0)
    if passed == 0 and failed == 0 and errors:
        return None
    total = passed + failed + errors
    return (passed, total) if total else None


def parse_go(output):
    passed = len(_GO_PASS.findall(output))
    failed = len(_GO_FAIL.findall(output))
    total = passed + failed
    return (passed, total) if total else None


def parse_tap(output):
    passed = len(_TAP_OK.findall(output))
    failed = len(_TAP_NOT_OK.findall(output))
    total = passed + failed
    return (passed, total) if total else None


def parse_json(output):
    for line in reversed(output.splitlines()):
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            payload = json.loads(s)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "passed" in payload and "total" in payload:
            return int(payload["passed"]), int(payload["total"])
    return None


PARSERS = {
    "pytest": parse_pytest,
    "go_test": parse_go,
    "node_tap": parse_tap,
    "json_report": parse_json,
}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--parser", required=True)
    ap.add_argument("--exit", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--expected", type=int, default=None)
    args = ap.parse_args(argv)

    output = sys.stdin.read()
    parsed = None if args.parser == "exit_code" else PARSERS[args.parser](output)

    if parsed is None:
        ok = args.exit == 0
        passed, total = (1, 1) if ok else (0, 1)
        partial = 1.0 if ok else 0.0
    else:
        passed, total = parsed
        if args.expected and total < args.expected:
            total = args.expected  # a declared test that never ran did not pass
        partial = (passed / total) if total else 0.0

    solved = total > 0 and passed == total and args.exit == 0
    reward = 1.0 if solved else 0.0
    payload = {
        "reward": reward,
        "partial_credit": round(partial, 6),
        "tests_passed": passed,
        "tests_total": total,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
        handle.write("\n")
    print("reward=%s partial=%.3f (%d/%d)" % (reward, partial, passed, total), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
'''


# ------------------------------------------------------------------ solve.sh


def render_solve_sh(loaded: LoadedTask) -> str:
    """Replay the reference playbook as a shell script Harbor can run as its oracle.

    File writes are emitted as base64, so a file whose content contains quotes, newlines
    or shell metacharacters is reproduced byte for byte with no chance of the heredoc
    being broken by its own payload. Reads and directory listings are comments: they moved
    no state, and the oracle does not need to look before it writes.
    """
    out = [
        "#!/usr/bin/env bash",
        "# Reference solution for " + loaded.id + ", replayed from the trajectory playbook.",
        "#",
        "# No set -e on purpose. A playbook's bash steps include commands that exercise the",
        "# task's error paths, so they exit non-zero by design, exactly as they do when the",
        "# native harness runs each step on its own. The reward the verifier writes is the",
        "# real check that the solution applied, not this script's exit code.",
        'cd "${HARBOR_WORKSPACE:-/workspace}" || exit 1',
        "",
    ]
    for index, step in enumerate(loaded.playbook.steps):
        if step.tool is ToolName.WRITE_FILE:
            path = str(step.args.get("path", "")).strip()
            content = str(step.args.get("content", ""))
            if not path:
                raise HarborExportError(f"{loaded.id}: write_file step {index} has no path")
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            out.append(f"# step {index}: write {path}")
            out.append(f"mkdir -p \"$(dirname '{path}')\"")
            out.append(f"base64 -d > '{path}' <<'B64_{index}'")
            out.append(encoded)
            out.append(f"B64_{index}")
            out.append("")
        elif step.tool is ToolName.BASH:
            command = str(step.args.get("command", "")).strip()
            out.append(f"# step {index}: run")
            out.append(command)
            out.append("")
        else:
            note = (step.note or step.tool.value).splitlines()[0] if step.note else step.tool.value
            out.append(f"# step {index}: {step.tool.value} ({note[:70]})")
    out.append('echo "reference solution applied"')
    out.append("")
    return "\n".join(out)


# -------------------------------------------------------------------- export


def _guard_dockerfile(text: str, task_id: str) -> None:
    """Refuse a Dockerfile that would bake the hidden tests into the agent's image.

    Only the source arguments of a COPY or ADD are inspected, so a legitimate destination
    such as `/workspace/` is never mistaken for a forbidden source directory.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith(("copy", "add")):
            continue
        for token in stripped.split()[1:]:
            if token.startswith("--"):
                continue  # a flag such as --chown=, not a path
            source = token.strip('"').lstrip("./")
            for forbidden in _FORBIDDEN_COPIES:
                if source == forbidden or source.startswith(forbidden + "/"):
                    raise HarborExportError(
                        f"{task_id}: the Dockerfile copies {forbidden}/ into the image, which "
                        "would let the agent read the hidden tests. Export refuses it."
                    )


def export_task(loaded: LoadedTask, out_dir: Path, *, force: bool = False) -> Path:
    """Write one task as a Harbor task directory under `out_dir/<task id>`.

    Args:
        loaded: The task, its directory and its reference playbook.
        out_dir: Parent directory to write the task into.
        force: Overwrite an existing export rather than refusing.

    Returns:
        The task directory that was written.

    Raises:
        HarborExportError: If the task is missing a piece the export needs, or its
            Dockerfile would leak the hidden tests into the agent's environment.
    """
    task = loaded.task
    source = loaded.directory
    workspace = source / "workspace"
    verify = source / "verify"
    dockerfile = source / "Dockerfile"
    required = ((workspace, "workspace/"), (verify, "verify/"), (dockerfile, "Dockerfile"))
    for path, what in required:
        if not path.exists():
            raise HarborExportError(f"{task.id}: missing {what}")

    _guard_dockerfile(dockerfile.read_text(encoding="utf-8"), task.id)

    target = out_dir / task.id
    if target.exists():
        if not force:
            raise HarborExportError(f"{target} already exists. Pass force=True to overwrite it.")
        shutil.rmtree(target)
    (target / "environment").mkdir(parents=True)
    (target / "tests").mkdir()
    (target / "solution").mkdir()

    (target / "task.toml").write_text(render_task_toml(task), encoding="utf-8")
    (target / "instruction.md").write_text(task.agent_prompt, encoding="utf-8")

    shutil.copy2(dockerfile, target / "environment" / "Dockerfile")
    shutil.copytree(workspace, target / "environment" / "workspace")

    shutil.copytree(verify, target / "tests" / "verify")
    _write_exec(target / "tests" / "test.sh", render_test_sh(task))
    verify_script = "#!/usr/bin/env bash\n" + task.verify_cmd + "\n"
    _write_exec(target / "tests" / "run_verify.sh", verify_script)
    _write_exec(target / "tests" / "score.py", _SCORE_PY)

    _write_exec(target / "solution" / "solve.sh", render_solve_sh(loaded))
    return target


def _write_exec(path: Path, text: str) -> None:
    """Write a file and mark it executable, which the Harbor scripts have to be."""
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
