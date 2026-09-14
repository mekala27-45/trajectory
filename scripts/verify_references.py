"""Verify each task's unfixed state fails and its reference playbook passes."""
import logging, sys
from pathlib import Path
import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
from trajectory_core.models import ToolName
from trajectory_runner.agent import run_agent
from trajectory_runner.loader import discover
from trajectory_runner.policies import build_script
from trajectory_runner.providers import StubProvider
from trajectory_runner.sandbox import LocalSandbox
from trajectory_runner.verifier import verify

only = sys.argv[1:] or None
failures = 0
for lt in discover(Path("tasks")):
    if only and lt.id not in only:
        continue
    with LocalSandbox(lt.task, lt.directory, allow_override=True) as sb:
        v0 = verify(lt.task, sb)
    provider = StubProvider("stub:methodical", build_script("methodical", lt.playbook, 0))
    with LocalSandbox(lt.task, lt.directory, allow_override=True) as sb:
        o = run_agent(lt.task, provider, sb, max_steps=lt.task.max_steps,
            timeout_seconds=lt.task.timeout_seconds,
            command_timeout_seconds=lt.task.command_timeout_seconds,
            cap_bytes=16384, tools_enabled=list(ToolName))
        v = verify(lt.task, sb)
        ok = v.passed and not v0.passed and o.status.value == "completed"
        failures += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {lt.id:24s} unfixed {v0.tests_passed}/{v0.tests_total} -> reference {v.tests_passed}/{v.tests_total}  {len(o.steps)} steps  {o.status.value}")
        if not ok:
            for s in o.steps:
                if s.exit_code not in (0, None) or s.schema_violation:
                    print(f"   step {s.index} {s.tool_name} rc={s.exit_code}: {s.tool_output[-700:]}")
            print("   verify stderr:", v.stderr_tail[-900:])
print("failures:", failures)
sys.exit(1 if failures else 0)
