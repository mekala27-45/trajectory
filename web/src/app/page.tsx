import Link from "next/link";

import { LeaderboardTable } from "@/components/leaderboard-table";
import { TrajectoryFigure } from "@/components/trajectory-figure";
import { HeadToHead, IntervalChart } from "@/components/uncertainty";
import {
  DifficultyMeter,
  EmptyState,
  Field,
  NoteBanner,
  Panel,
  PlainNote,
  Tag,
} from "@/components/ui";
import {
  getIndex,
  getLeaderboard,
  getMutation,
  getRun,
  getStats,
  getTasks,
  listRuns,
} from "@/lib/api";
import { count, isStubModel, percent, seconds, splitModel, timestamp, usd } from "@/lib/format";
import type { RunSummary } from "@/lib/types";

/**
 * The run the home page draws.
 *
 * A real model run, never a scripted one, chosen by a fixed rule so the figure cannot be
 * picked to flatter: the shortest run that called finish with hidden tests still failing
 * after changing course on a failed command, then the shortest premature finish, then the
 * shortest unsolved run. Ties go to the earliest identifier.
 */
function featuredRun(runs: RunSummary[]): RunSummary | undefined {
  const real = runs
    .filter((run) => !isStubModel(run.model) && !run.solved && run.steps >= 3 && run.steps <= 26)
    .sort((a, b) => a.steps - b.steps || a.id.localeCompare(b.id));
  return (
    real.find((run) => run.premature_termination && (run.recovery_rate ?? 0) > 0) ??
    real.find((run) => run.premature_termination) ??
    real[0]
  );
}

export const metadata = {
  title: "Leaderboard",
  description:
    "Solve rate with its spread across seeds, plus eight trajectory metrics, for every agent in the dataset: scripted policies that check the harness, and real models.",
};

export default async function LeaderboardPage() {
  const [index, board, tasks, runs, stats, mutation] = await Promise.all([
    getIndex(),
    getLeaderboard(),
    getTasks(),
    listRuns(),
    getStats(),
    getMutation(),
  ]);
  const mutationByTask = new Map((mutation?.tasks ?? []).map((task) => [task.task_id, task]));
  const realStats = (stats?.models ?? []).filter((entry) => !isStubModel(entry.model));
  const shownStats = realStats.length > 0 ? realStats : (stats?.models ?? []);
  const realPairs = (stats?.pairs ?? []).filter(
    (pair) => !isStubModel(pair.first) && !isStubModel(pair.second),
  );

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
  const chosen = featuredRun(runs);
  const figure = chosen ? await getRun(chosen.id) : null;
  const figureTask = figure ? tasks.find((task) => task.id === figure.task_id) : undefined;

  const realResult =
    realModels.length === 1
      ? ` First result: ${splitModel(realModels[0]!).name} solved ${realSolved} of ${realRuns.length} runs.`
      : realModels.length > 1
        ? ` ${realModels.length} real models so far, ${realSolved} of ${realRuns.length} runs solved.`
        : "";

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-4 pt-1 sm:pt-3">
        <h1 className="max-w-[17ch] text-[32px] leading-[1.06] text-balance sm:text-[46px]">
          A pass rate tells you an agent failed, not how.
        </h1>
        <p className="max-w-[66ch] text-[16px] leading-relaxed text-muted">
          Twelve containerized tasks, hidden tests that go in only after the agent stops, and a
          reference path to score against. trajectory reads every step: where the agent changed
          course, where it wandered, and whether it called done while the tests were still red.
        </p>
        {figure && figureTask ? (
          <TrajectoryFigure
            run={figure}
            referenceSteps={figureTask.reference_step_count}
            taskTitle={figureTask.title}
          />
        ) : null}
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
        <h2 id="results" className="mb-1 text-[19px] sm:text-[21px]">
          Results, with the spread
        </h2>
        <p className="mb-3 max-w-[78ch] text-[13.5px] text-muted">
          Each row is one agent on one suite from one sandbox: a scripted policy that checks the
          harness, or a real model. Solve rate is the headline, and the other columns are what a
          solve rate cannot tell you: whether the agent got there in a sensible number of steps,
          whether it recovered when a command failed, and whether it declared victory while the
          hidden tests were still red. Every number carries its standard deviation across seeds, and
          a cell with no runs behind it reads n/a rather than zero.
        </p>
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

      {shownStats.length > 0 ? (
        <section aria-labelledby="how-sure">
          <h2 id="how-sure" className="mb-1 text-[19px] sm:text-[21px]">
            How sure these numbers are
          </h2>
          <p className="mb-3 max-w-[78ch] text-[13.5px] text-muted">
            The spread in the table says whether running the same tasks again would move a number.
            This says whether the number would hold on tasks the suite does not have: each band is a
            95% interval from resampling the twelve tasks, then the seeds of each task, with every
            task weighing the same. pass@k is the chance that at least one of k attempts at a task
            solves it.
            {realStats.length === 0
              ? " No real model is in this dataset, so these are the scripted policies."
              : ""}
          </p>
          <div className="mb-3">
            <PlainNote>
              Twelve tasks is a small exam. If the exam had twelve different questions of the same
              kind, a score could land anywhere in its band. Two programs whose bands overlap a lot
              are ones this exam cannot tell apart, however different their headline numbers look.
            </PlainNote>
          </div>
          <IntervalChart entries={shownStats} />
          {realPairs.length > 0 ? (
            <div className="mt-4 flex flex-col gap-2">
              <h3 className="text-[15px]">Head to head</h3>
              <p className="max-w-[78ch] text-[13px] text-muted">
                Each pair compared on the tasks both attempted, resampled as pairs. A pair is
                separated only when its whole interval sits on one side of zero.
              </p>
              <HeadToHead pairs={realPairs} />
            </div>
          ) : null}
        </section>
      ) : null}

      <section aria-labelledby="beyond">
        <h2 id="beyond" className="mb-1 text-[19px] sm:text-[21px]">
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
        <h2 id="tasks" className="mb-1 text-[19px] sm:text-[21px]">
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
              const taskStats = byTask.get(task.id);
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
                      {taskStats
                        ? `${taskStats.total} runs, ${percent(taskStats.solved / taskStats.total, 0)} solved, ${taskStats.models.size} ${taskStats.models.size === 1 ? "agent" : "agents"}`
                        : "no runs yet"}
                    </span>
                    {(() => {
                      const strength = mutationByTask.get(task.id);
                      if (!strength || strength.scored === 0) return null;
                      return (
                        <span
                          className="tnum font-mono text-[11px] text-dim"
                          title="Mutation testing: near misses of the reference the hidden tests failed."
                        >
                          tests caught {strength.killed} of {strength.scored} near misses
                        </span>
                      );
                    })()}
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
