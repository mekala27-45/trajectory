import Link from "next/link";

import { LeaderboardTable } from "@/components/leaderboard-table";
import { DifficultyMeter, EmptyState, Field, NoteBanner, Panel, Tag } from "@/components/ui";
import { getIndex, getLeaderboard, getTasks, listRuns } from "@/lib/api";
import { count, percent, seconds, timestamp, usd } from "@/lib/format";

export const metadata = {
  title: "Leaderboard",
  description:
    "Solve rate with its spread across seeds, plus eight trajectory metrics, for every model in the dataset.",
};

export default async function LeaderboardPage() {
  const [index, board, tasks, runs] = await Promise.all([
    getIndex(),
    getLeaderboard(),
    getTasks(),
    listRuns(),
  ]);

  const byTask = new Map<string, { total: number; solved: number; models: Set<string> }>();
  for (const run of runs) {
    const entry = byTask.get(run.task_id) ?? { total: 0, solved: 0, models: new Set<string>() };
    entry.total += 1;
    if (run.solved) entry.solved += 1;
    entry.models.add(run.model);
    byTask.set(run.task_id, entry);
  }

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h1 className="text-[22px] font-semibold sm:text-[26px]">
          Coding agent results, with the spread
        </h1>
        <p className="max-w-[78ch] text-[14px] text-muted">
          Each row is one model on one suite from one sandbox. Solve rate is the headline, and the
          other columns are what a solve rate cannot tell you: whether the agent got there in a
          sensible number of steps, whether it recovered when a command failed, and whether it
          declared victory while the hidden tests were still red. Every number carries its standard
          deviation across seeds, and a cell with no runs behind it reads n/a rather than zero.
        </p>
      </section>

      <NoteBanner note={board.note || index.note} />

      <Panel className="px-4 py-3">
        <h2 className="sr-only">Dataset</h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-3 lg:grid-cols-6">
          <Field label="Last run" title="Start time of the most recent run behind any row.">
            {timestamp(board.last_run_at ?? index.generated_at)}
          </Field>
          <Field label="Runs">
            {count(index.run_count)}
            <span className="text-dim"> ({count(index.solved_count)} solved)</span>
          </Field>
          <Field label="Tasks">{count(index.task_count)}</Field>
          <Field label="Models">{count(index.models.length)}</Field>
          <Field label="Total spend">{usd(index.total_cost_usd)}</Field>
          <Field label="Total wall clock">{seconds(index.total_wall_clock_s)}</Field>
        </dl>
        <p className="mt-3 border-t border-line pt-2 font-mono text-[11px] text-dim">
          harness {index.harness_version}, schema {index.schema_version}, bundle built{" "}
          {timestamp(index.generated_at)}
          {index.judge_model ? `, judge ${index.judge_model}` : ", rubric judge did not run"}
        </p>
      </Panel>

      <section aria-labelledby="results">
        <h2 id="results" className="mb-3 text-[16px] font-semibold">
          Results
        </h2>
        {board.rows.length === 0 ? (
          <EmptyState title="No results in this dataset.">
            Nothing has been scored yet. Run the harness and rebuild the bundle with
            <code className="mx-1 font-mono text-[12px]">python scripts/build_web_bundle.py</code>
            to fill this in.
          </EmptyState>
        ) : (
          <LeaderboardTable rows={board.rows} suites={index.suites} backends={index.backends} />
        )}
      </section>

      <section aria-labelledby="beyond">
        <h2 id="beyond" className="mb-1 text-[16px] font-semibold">
          Beyond the leaderboard
        </h2>
        <p className="mb-3 max-w-[78ch] text-[13px] text-muted">
          Three things this harness does that a pass rate cannot. Each is a pure part of the same
          run record, so a result published here can be dropped into another stack, checked for a
          gamed grader, or reproduced against a real model.
        </p>
        <ul className="grid gap-3 sm:grid-cols-3">
          {[
            {
              title: "Runs in your stack",
              body: "Every task exports to the Harbor task format, verifier and oracle included, and CI proves each export grades the same as the native harness.",
              href: "https://github.com/mekala27-45/trajectory#beyond-the-leaderboard",
              link: "trajectory export harbor",
            },
            {
              title: "Resists reward hacking",
              body: "The verifier is hardened so a file the agent plants cannot shadow the hidden tests, and failure mode F11 flags any attempt. A gamed grader is the one thing a pass rate hides.",
              href: "https://github.com/mekala27-45/trajectory/blob/main/docs/grader-audit.md",
              link: "The grader audit",
            },
            {
              title: "Runs real models for free",
              body: "The same suite drives a real model through a no-card tier, capped by a shared call ceiling and rate limit, with a one-call smoke that fails before the credits do.",
              href: "https://github.com/mekala27-45/trajectory/blob/main/docs/real-models.md",
              link: "Real models",
            },
          ].map((card) => (
            <li
              key={card.title}
              className="flex h-full flex-col gap-2 rounded-lg border border-line bg-raised px-3.5 py-3"
            >
              <span className="text-[13.5px] font-medium">{card.title}</span>
              <span className="text-[12.5px] text-muted">{card.body}</span>
              <a
                href={card.href}
                target="_blank"
                rel="noreferrer"
                className="mt-auto pt-1 font-mono text-[11.5px] text-accent hover:underline"
              >
                {card.link} &rarr;
              </a>
            </li>
          ))}
        </ul>
      </section>

      <section aria-labelledby="tasks">
        <h2 id="tasks" className="mb-1 text-[16px] font-semibold">
          Tasks
        </h2>
        <p className="mb-3 max-w-[78ch] text-[13px] text-muted">
          Each task is a workspace with something wrong in it, a hidden test suite the agent never
          sees, and a reference solution that has to pass those tests in CI. The reference step
          count is what step efficiency is measured against.
        </p>
        {tasks.length === 0 ? (
          <EmptyState title="No tasks in this dataset." />
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {tasks.map((task) => {
              const stats = byTask.get(task.id);
              return (
                <li key={task.id}>
                  <Link
                    href={`/tasks/${task.id}/`}
                    className="flex h-full flex-col gap-2 rounded-lg border border-line bg-raised px-3.5 py-3 hover:border-accent-line hover:bg-hover"
                  >
                    <span className="flex items-start justify-between gap-2">
                      <span className="font-mono text-[11.5px] text-accent">{task.id}</span>
                      <DifficultyMeter tier={task.difficulty} />
                    </span>
                    <span className="text-[13.5px] font-medium">{task.title}</span>
                    <span className="mt-auto flex flex-wrap items-center gap-1.5 pt-1">
                      <Tag>{task.language}</Tag>
                      <Tag>ref {task.reference_step_count} steps</Tag>
                      <Tag>max {task.max_steps}</Tag>
                    </span>
                    <span className="tnum font-mono text-[11px] text-muted">
                      {stats
                        ? `${stats.total} runs, ${percent(stats.solved / stats.total, 0)} solved, ${stats.models.size} ${stats.models.size === 1 ? "model" : "models"}`
                        : "no runs yet"}
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
