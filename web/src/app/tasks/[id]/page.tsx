import Link from "next/link";
import { notFound } from "next/navigation";

import {
  BackendChip,
  Chip,
  DifficultyMeter,
  EmptyState,
  Field,
  Panel,
  SolveRateBar,
  StatSpread,
  StatusChip,
  Tag,
  VerdictChip,
} from "@/components/ui";
import { getTaskResults, getTasks } from "@/lib/api";
import { count, percent, ratio, seconds, timestamp, usd } from "@/lib/format";
import type { MetricStat, RunSummary } from "@/lib/types";

export const dynamicParams = false;

export async function generateStaticParams() {
  const tasks = await getTasks();
  return tasks.map((task) => ({ id: task.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const results = await getTaskResults(id);
  if (!results) return { title: "Task not found" };
  return {
    title: results.task.title,
    description: results.task.description.slice(0, 180),
  };
}

/** Group the runs by model, keeping seeds in order inside each group. */
function groupByModel(runs: RunSummary[]): Array<[string, RunSummary[]]> {
  const groups = new Map<string, RunSummary[]>();
  for (const run of runs) {
    const bucket = groups.get(run.model);
    if (bucket) bucket.push(run);
    else groups.set(run.model, [run]);
  }
  for (const bucket of groups.values()) {
    bucket.sort((a, b) => a.seed - b.seed || a.started_at.localeCompare(b.started_at));
  }
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
}

/** Derive a solve rate for a model the aggregate did not cover, so the grid is never blank. */
function derivedSolveRate(runs: RunSummary[]): MetricStat {
  if (runs.length === 0) return { mean: 0, stdev: 0, n: 0 };
  const solved = runs.filter((run) => run.solved).length;
  const mean = solved / runs.length;
  const variance =
    runs.reduce((total, run) => total + ((run.solved ? 1 : 0) - mean) ** 2, 0) / runs.length;
  return { mean, stdev: Math.sqrt(variance), n: runs.length };
}

export default async function TaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const results = await getTaskResults(id);
  if (!results) notFound();

  const { task, runs, solve_rate_by_model: byModel } = results;
  const groups = groupByModel(runs);
  const solved = runs.filter((run) => run.solved).length;

  return (
    <div className="flex flex-col gap-6">
      <nav aria-label="Breadcrumb" className="text-[12.5px] text-muted">
        <Link href="/" className="text-accent underline underline-offset-2">
          Leaderboard
        </Link>
        <span aria-hidden="true"> / </span>
        <span className="font-mono">{task.id}</span>
      </nav>

      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[12px] text-accent">{task.id}</span>
          <Tag>{task.suite}</Tag>
          <Tag>{task.language}</Tag>
          <DifficultyMeter tier={task.difficulty} />
        </div>
        <h1 className="max-w-[60ch] text-[21px] font-semibold sm:text-[25px]">{task.title}</h1>
        <div className="flex flex-wrap gap-1.5">
          {task.tags.map((tag) => (
            <Tag key={tag}>{tag}</Tag>
          ))}
        </div>
      </header>

      <Panel className="px-4 py-3">
        <h2 className="sr-only">Task parameters</h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
          <Field
            label="Reference steps"
            title="Step count of the reference solution. Step efficiency is measured against this."
          >
            {task.reference_step_count}
          </Field>
          <Field label="Step ceiling" title="The run stops at this many steps.">
            {task.max_steps}
          </Field>
          <Field label="Runs">{count(runs.length)}</Field>
          <Field label="Solved">{runs.length === 0 ? "n/a" : `${solved} of ${runs.length}`}</Field>
          <Field label="Models">{count(groups.length)}</Field>
        </dl>
      </Panel>

      <section aria-labelledby="what-is-broken">
        <h2 id="what-is-broken" className="mb-2 text-[16px] font-semibold">
          What is broken, and what fixed means
        </h2>
        <div className="max-w-[85ch] rounded-lg border border-line bg-raised px-4 py-3 text-[13.5px] leading-relaxed whitespace-pre-line text-fg">
          {task.description.trim()}
        </div>
      </section>

      <section aria-labelledby="per-model">
        <h2 id="per-model" className="mb-1 text-[16px] font-semibold">
          Results by model
        </h2>
        <p className="mb-3 max-w-[80ch] text-[13px] text-muted">
          One group per model. The solve rate carries its spread across seeds, and every run below
          it links to the full step by step replay.
        </p>

        {groups.length === 0 ? (
          <EmptyState title="No runs on this task yet.">
            The task is in the suite but no recorded run has attempted it. Run the harness against
            it and rebuild the bundle to populate this page.
          </EmptyState>
        ) : (
          <div className="flex flex-col gap-4">
            {groups.map(([model, modelRuns]) => {
              const stat = byModel[model] ?? derivedSolveRate(modelRuns);
              return (
                <Panel key={model} className="overflow-hidden">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line px-4 py-3">
                    <h3 className="font-mono text-[13.5px] font-medium">{model}</h3>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      <SolveRateBar stat={stat} width={120} />
                      <StatSpread stat={stat} />
                    </div>
                    <p className="tnum ml-auto font-mono text-[11px] text-dim">
                      {modelRuns.length} {modelRuns.length === 1 ? "run" : "runs"}, seeds{" "}
                      {modelRuns.map((run) => run.seed).join(", ")}
                    </p>
                  </div>

                  <ul className="divide-y divide-[color:var(--line)]">
                    {modelRuns.map((run) => (
                      <li key={run.id}>
                        <Link
                          href={`/runs/${run.id}/`}
                          className="block px-4 py-2.5 hover:bg-hover"
                        >
                          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                            <span className="tnum font-mono text-[12px] text-muted">
                              seed {run.seed}
                            </span>
                            <VerdictChip
                              solved={run.solved}
                              passed={run.tests_passed}
                              total={run.tests_total}
                            />
                            <StatusChip status={run.status} />
                            <BackendChip backend={run.backend} />
                            {run.premature_termination ? (
                              <Chip
                                tone="bad"
                                title="Metric 7: the agent called finish while the hidden tests still fail."
                              >
                                premature finish
                              </Chip>
                            ) : null}
                            {run.failure_modes.map((mode) => (
                              <Chip key={mode} tone="warn">
                                {mode}
                              </Chip>
                            ))}
                            <span className="ml-auto font-mono text-[11.5px] text-accent underline underline-offset-2">
                              replay
                            </span>
                          </div>
                          <dl className="tnum mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 font-mono text-[11px] text-dim">
                            <div>
                              <dt className="inline">steps </dt>
                              <dd className="inline text-muted">
                                {run.steps} of {task.max_steps}
                              </dd>
                            </div>
                            <div>
                              <dt className="inline">partial credit </dt>
                              <dd className="inline text-muted">
                                {percent(run.partial_credit, 0)}
                              </dd>
                            </div>
                            <div>
                              <dt className="inline">step efficiency </dt>
                              <dd className="inline text-muted">{ratio(run.step_efficiency)}</dd>
                            </div>
                            <div>
                              <dt className="inline">recovery </dt>
                              <dd className="inline text-muted">
                                {run.recovery_rate == null ? "n/a" : percent(run.recovery_rate, 0)}
                              </dd>
                            </div>
                            <div>
                              <dt className="inline">cost </dt>
                              <dd className="inline text-muted">{usd(run.cost_usd)}</dd>
                            </div>
                            <div>
                              <dt className="inline">wall clock </dt>
                              <dd className="inline text-muted">{seconds(run.wall_clock_s)}</dd>
                            </div>
                            <div>
                              <dt className="inline">started </dt>
                              <dd className="inline text-muted">{timestamp(run.started_at)}</dd>
                            </div>
                          </dl>
                        </Link>
                      </li>
                    ))}
                  </ul>
                </Panel>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
