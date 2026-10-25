import Link from "next/link";

import { LeaderboardTable } from "@/components/leaderboard-table";
import {
  DifficultyMeter,
  EmptyState,
  Field,
  NoteBanner,
  Panel,
  PlainNote,
  Tag,
} from "@/components/ui";
import { getIndex, getLeaderboard, getTasks, listRuns } from "@/lib/api";
import { count, isStubModel, percent, seconds, splitModel, timestamp, usd } from "@/lib/format";

export const metadata = {
  title: "Leaderboard",
  description:
    "Solve rate with its spread across seeds, plus eight trajectory metrics, for every agent in the dataset: scripted policies that check the harness, and real models.",
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

  // Read from the bundle, never typed in, so the card cannot drift from the runs behind it.
  const scripted = index.models.filter(isStubModel).length;
  const realModels = index.models.filter((model) => !isStubModel(model));
  const realRuns = runs.filter((run) => !isStubModel(run.model));
  const realSolved = realRuns.filter((run) => run.solved).length;
  const realResult =
    realModels.length === 1
      ? ` First result: ${splitModel(realModels[0]!).name} solved ${realSolved} of ${realRuns.length} runs.`
      : realModels.length > 1
        ? ` ${realModels.length} real models so far, ${realSolved} of ${realRuns.length} runs solved.`
        : "";

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h1 className="text-[22px] font-semibold sm:text-[26px]">
          Coding agent results, with the spread
        </h1>
        <p className="max-w-[78ch] text-[14px] text-muted">
          Each row is one agent on one suite from one sandbox: a scripted policy that checks the
          harness, or a real model. Solve rate is the headline, and the other columns are what a
          solve rate cannot tell you: whether the agent got there in a sensible number of steps,
          whether it recovered when a command failed, and whether it declared victory while the
          hidden tests were still red. Every number carries its standard deviation across seeds, and
          a cell with no runs behind it reads n/a rather than zero.
        </p>
      </section>

      <PlainNote>
        trajectory tests AI programs that fix code on their own. Most tests only ask whether the fix
        worked. This one watches the whole attempt, step by step, and scores how the program worked,
        not just whether it got there. Every row below is one such program. For the full
        walkthrough, read the{" "}
        <Link href="/guide/" className="text-accent hover:underline">
          guide
        </Link>
        .
      </PlainNote>

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
          <Field label="Agents">
            {count(index.models.length)}
            {realModels.length > 0 && scripted > 0 ? (
              <span className="text-dim">
                {" "}
                ({count(scripted)} scripted, {count(realModels.length)} real)
              </span>
            ) : null}
          </Field>
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
        <div className="mb-3">
          <PlainNote>
            Solve rate is how often that program fully fixed the bug. The other columns are what the
            solve rate hides: how direct the route was, whether it recovered after a failed command,
            and whether it claimed success before the hidden tests passed.
          </PlainNote>
        </div>
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
        <div className="mb-3">
          <PlainNote>
            The same recorded runs can be used inside tools at other labs, checked for a program
            trying to cheat the scorer, and produced by a real AI model at no cost. Those are the
            three cards below.
          </PlainNote>
        </div>
        <ul className="grid gap-3 sm:grid-cols-3">
          {[
            {
              title: "Runs in your stack",
              body: "Every task exports to the Harbor task format, and CI grades a sample of the exports both ways: the oracle must score 1.0 and the untouched workspace 0.0. ATIF trajectories from Claude Code, Codex and others import and score with the same metrics.",
              href: "https://github.com/mekala27-45/trajectory/blob/main/docs/interop.md",
              link: "Harbor and ATIF",
            },
            {
              title: "Resists reward hacking",
              body: "Verification no longer reads the implicit import paths a planted file would use, and failure mode F11 flags an agent that leaves such a hook behind. A gamed grader is the one thing a pass rate hides.",
              href: "https://github.com/mekala27-45/trajectory/blob/main/docs/grader-audit.md",
              link: "The grader audit",
            },
            {
              title: "Runs real models for free",
              body: `The same suite drives a real model through a free tier, capped by a shared call ceiling and rate limit, with a one-call smoke that fails before a full matrix starts.${realResult}`,
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
        <div className="mb-3">
          <PlainNote>
            Each task is like a sealed exam: a small project with a bug, a set of hidden checks the
            program never sees until it stops, and a known-good answer used to keep the exam fair.
            Click any task to read what is broken and what counts as fixed.
          </PlainNote>
        </div>
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
                        ? `${stats.total} runs, ${percent(stats.solved / stats.total, 0)} solved, ${stats.models.size} ${stats.models.size === 1 ? "agent" : "agents"}`
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
