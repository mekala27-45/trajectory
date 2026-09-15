import Link from "next/link";
import { notFound } from "next/navigation";

import { CodePane } from "@/components/code-pane";
import { TrajectoryReplay } from "@/components/trajectory-replay";
import { BackendChip, Chip, Field, Panel, StatusChip } from "@/components/ui";
import { getFailures, getRun, getTasks, listRuns } from "@/lib/api";
import { millis, percent, ratio, seconds, timestamp, usd } from "@/lib/format";
import { OUTPUT_DISPLAY_CAP } from "@/lib/highlight";
import type { FailureModeSpec, Run, Step } from "@/lib/types";

export const dynamicParams = false;

export async function generateStaticParams() {
  const runs = await listRuns();
  return runs.map((run) => ({ id: run.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const run = await getRun(id);
  if (!run) return { title: "Run not found" };
  const verdict = run.verification?.passed ? "solved" : "not solved";
  return {
    title: `${run.task_id}, ${run.config.model}, seed ${run.config.seed}`,
    description: `Trajectory replay: ${run.steps.length} steps, ${verdict}.`,
  };
}

/**
 * Bound what crosses into the browser.
 *
 * The replay is a client component so that j and k can move between steps, which means
 * every byte of every step output is serialised into the page. A task whose workspace
 * holds a forty thousand line log can produce a step far larger than anyone will read, so
 * it is cut here as well as in the pane that renders it.
 */
function trimForDisplay(run: Run): { run: Run; cutSteps: number } {
  let cutSteps = 0;
  const steps: Step[] = run.steps.map((step) => {
    if (step.tool_output.length <= OUTPUT_DISPLAY_CAP) return step;
    cutSteps += 1;
    return { ...step, tool_output: step.tool_output.slice(0, OUTPUT_DISPLAY_CAP) };
  });
  return { run: { ...run, steps }, cutSteps };
}

export default async function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [loaded, tasks, failures] = await Promise.all([getRun(id), getTasks(), getFailures()]);
  if (!loaded) notFound();

  const { run, cutSteps } = trimForDisplay(loaded);
  const task = tasks.find((candidate) => candidate.id === run.task_id) ?? null;
  const specs: Record<string, FailureModeSpec | undefined> = Object.fromEntries(
    failures.taxonomy.map((spec) => [spec.id, spec]),
  );
  const score = run.score;
  const verification = run.verification;

  return (
    <div className="flex flex-col gap-4">
      <nav aria-label="Breadcrumb" className="text-[12.5px] text-muted">
        <Link href="/" className="text-accent underline underline-offset-2">
          Leaderboard
        </Link>
        <span aria-hidden="true"> / </span>
        <Link href={`/tasks/${run.task_id}/`} className="text-accent underline underline-offset-2">
          {run.task_id}
        </Link>
        <span aria-hidden="true"> / </span>
        <span className="font-mono break-all">{run.id}</span>
      </nav>

      <header className="flex flex-col gap-2">
        <h1 className="max-w-[60ch] text-[19px] font-semibold sm:text-[23px]">
          {task ? task.title : run.task_id}
        </h1>
        {/* The verdict, the model and the backend live in the sticky bar below, which
            travels with the reader. Only the things that need saying once are here. */}
        <div className="flex flex-wrap items-center gap-2 empty:hidden">
          {score?.premature_termination ? (
            <Chip
              tone="bad"
              title="Metric 7: the agent called finish while the hidden tests still fail. The run looks like a clean completion from the outside."
            >
              premature finish
            </Chip>
          ) : null}
          {verification && !verification.parse_ok ? (
            <Chip
              tone="warn"
              title="The verification output could not be parsed into a passed and total count, so the run fell back to the exit code and partial credit is not meaningful."
            >
              verification output not parsed
            </Chip>
          ) : null}
        </div>
      </header>

      <Panel className="px-4 py-3">
        <h2 className="sr-only">Run record</h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
          <Field label="Model">{run.config.model}</Field>
          <Field label="Seed">{run.config.seed}</Field>
          <Field label="Temperature">{run.config.temperature}</Field>
          <Field label="Steps">
            {run.steps.length}
            <span className="text-dim"> of {run.config.max_steps}</span>
          </Field>
          <Field label="Total cost">{usd(run.total_cost_usd ?? score?.cost_usd ?? 0)}</Field>
          <Field label="Wall clock">{seconds(run.wall_clock_s ?? score?.wall_clock_s ?? 0)}</Field>
          <Field label="Hidden tests">
            {verification
              ? `${verification.tests_passed} of ${verification.tests_total} passed`
              : "verification did not run"}
          </Field>
          <Field label="Verify exit code">{verification ? verification.exit_code : "n/a"}</Field>
          <Field label="Verify duration">
            {verification ? millis(verification.duration_ms) : "n/a"}
          </Field>
          <Field label="Started">{timestamp(run.started_at)}</Field>
          <Field label="Finished">{timestamp(run.finished_at)}</Field>
          <Field label="Suite">{run.suite}</Field>
          <Field label="Status" title="Why the agent loop stopped. Not whether it succeeded.">
            <StatusChip status={run.status} />
          </Field>
          <Field label="Sandbox">
            <BackendChip backend={run.runner_fingerprint.sandbox_backend} />
          </Field>
        </dl>
      </Panel>

      {run.error ? (
        <p className="rounded-md border border-bad/40 bg-bad-bg px-3 py-2 text-[13px] text-bad">
          The harness ended this run: {run.error}
        </p>
      ) : null}

      {cutSteps > 0 ? (
        <p className="rounded-md border border-warn/40 bg-warn-bg px-3 py-2 text-[12.5px] text-warn">
          {cutSteps} {cutSteps === 1 ? "step output was" : "step outputs were"} cut for display. The
          run record on disk holds the full text.
        </p>
      ) : null}

      <TrajectoryReplay run={run} task={task} specs={specs} />

      <section aria-labelledby="metrics" className="mt-2">
        <h2 id="metrics" className="mb-2 text-[16px] font-semibold">
          Metrics for this run
        </h2>
        {score ? (
          <Panel className="px-4 py-3">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
              <Field label="Partial credit" title="Metric 2: hidden tests passed over discovered.">
                {percent(score.partial_credit, 1)}
              </Field>
              <Field
                label="Step efficiency"
                title="Metric 3: reference step count over steps taken, capped at 1. Null on unsolved runs, where a low step count means the agent gave up rather than that it was efficient."
              >
                {ratio(score.step_efficiency)}
              </Field>
              <Field
                label="Tool validity"
                title="Metric 4: valid tool calls over total tool calls."
              >
                {percent(score.tool_call_validity, 1)}
              </Field>
              <Field
                label="Redundancy"
                title="Metric 5: byte identical repeat commands over total commands. A correct trajectory has a non-zero rate, because running the suite again after a change is the right thing to do."
              >
                {percent(score.redundant_action_rate, 1)}
              </Field>
              <Field
                label="Recovery"
                title="Metric 6: of the commands that failed, the fraction followed within two steps by a materially different command. Null when nothing failed."
              >
                {score.recovery_rate == null ? "n/a" : percent(score.recovery_rate, 1)}
              </Field>
              <Field
                label="Context drift"
                title="Metric 8: judge scored drift over the final third of the trajectory. Null when the judge did not run."
              >
                {ratio(score.context_drift)}
              </Field>
              <Field label="Commands">{score.total_commands}</Field>
              <Field label="Schema violations">{score.schema_violations}</Field>
              <Field label="Failed commands">{score.failed_commands}</Field>
              <Field
                label="Destructive attempts"
                title="Metric 10: commands matching the destructive pattern list. Flagged, never blocked, because the tendency is the finding."
              >
                {score.destructive_attempts}
              </Field>
            </dl>
          </Panel>
        ) : (
          <p className="rounded-lg border border-dashed border-line-strong bg-raised px-4 py-3 text-[13px] text-muted">
            This run was never scored, so it has no metrics. That happens when the harness stopped
            before verification could run.
          </p>
        )}
      </section>

      {verification && verification.stderr_tail.trim().length > 0 ? (
        <section aria-labelledby="verify-output">
          <h2 id="verify-output" className="mb-1 text-[16px] font-semibold">
            Verification output
          </h2>
          <p className="mb-1 max-w-[80ch] text-[12.5px] text-muted">
            The last few kilobytes of the hidden test run, stdout and stderr together, kept for
            triage. The agent never saw this.
          </p>
          <CodePane text={verification.stderr_tail} kind="output" collapsedLines={12} />
        </section>
      ) : null}

      <details className="rounded-lg border border-line bg-raised px-4 py-3">
        <summary className="cursor-pointer text-[13px] font-medium">Provenance</summary>
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-4">
          <Field label="Harness">{run.harness_version}</Field>
          <Field label="Schema">{run.schema_version}</Field>
          <Field label="Sandbox">{run.runner_fingerprint.sandbox_backend}</Field>
          <Field label="Image">{run.image_id ?? "n/a"}</Field>
          <Field label="OS">
            {run.runner_fingerprint.os} {run.runner_fingerprint.os_release}
          </Field>
          <Field label="Arch">{run.runner_fingerprint.arch}</Field>
          <Field label="Python">{run.runner_fingerprint.python_version}</Field>
          <Field label="Docker">{run.runner_fingerprint.docker_version ?? "n/a"}</Field>
          <Field label="CPUs">{run.runner_fingerprint.cpu_count}</Field>
          <Field label="CI">{run.runner_fingerprint.ci ? "yes" : "no"}</Field>
          <Field label="Command timeout">{run.config.command_timeout_seconds}s</Field>
          <Field label="Run timeout">{run.config.timeout_seconds}s</Field>
          <Field label="Output cap">{run.config.max_output_bytes} bytes</Field>
          <Field label="Budget">
            {run.config.budget_usd == null ? "none" : usd(run.config.budget_usd)}
          </Field>
          <Field label="Tools">{run.config.tools_enabled.join(", ")}</Field>
          <Field label="Workspace files">
            {Object.keys(run.final_workspace.files).length} after,{" "}
            {Object.keys(run.initial_workspace.files).length} before
          </Field>
        </dl>
        <p className="mt-3 max-w-[85ch] text-[12.5px] text-muted">
          Results that cannot be reproduced are not results. When a number moves, this is how you
          tell whether the model changed or the environment did.
        </p>
      </details>
    </div>
  );
}
