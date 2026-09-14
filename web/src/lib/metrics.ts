/**
 * Column metadata for the leaderboard.
 *
 * The descriptions are taken from the docstrings in
 * `packages/core/src/trajectory_core/scoring.py` rather than rewritten, so the tooltip a
 * reader sees says the same thing as the code that produced the number.
 */

import type { LeaderboardRow, MetricStat } from "./types";

/** Which direction is good. Used for the sort default and nothing else, never for colour. */
export type Direction = "higher" | "lower" | "neutral";

export interface MetricColumn {
  /** Key on `LeaderboardRow`. */
  key: keyof LeaderboardRow;
  /** Column header, short enough to fit. */
  header: string;
  /** Full name, for the tooltip title and the accessible label. */
  title: string;
  /** What the number means, in the words of the scorer. */
  description: string;
  percent: boolean;
  digits: number;
  direction: Direction;
}

/** The eight aggregate metrics that carry a mean and a spread across seeds. */
export const STAT_COLUMNS: readonly MetricColumn[] = [
  {
    key: "solve_rate",
    header: "Solve rate",
    title: "Metric 1: solve rate",
    description:
      "Every hidden test passed. Deliberately not derived from the run status: an agent can call finish on a run that solved nothing, and a run that hit its step ceiling can still have left the workspace in a passing state. Aggregated across seeds.",
    percent: true,
    digits: 1,
    direction: "higher",
  },
  {
    key: "partial_credit",
    header: "Partial credit",
    title: "Metric 2: partial credit",
    description:
      "Hidden tests passed divided by hidden tests discovered. Four failing tests taken down to one is progress, and a benchmark that reports that identically to no change at all has thrown away the only signal in the run.",
    percent: true,
    digits: 1,
    direction: "higher",
  },
  {
    key: "step_efficiency",
    header: "Step efficiency",
    title: "Metric 3: step efficiency",
    description:
      "Reference step count divided by steps taken, capped at 1. Measured over solved runs only, because on a run that failed a low step count means the agent gave up early rather than that it was efficient. A model that solved nothing has no efficiency at all, and the cell reads n/a.",
    percent: false,
    digits: 3,
    direction: "higher",
  },
  {
    key: "tool_call_validity",
    header: "Tool validity",
    title: "Metric 4: tool call validity",
    description:
      "Valid tool calls divided by total tool calls. A call counts as invalid when it named a tool that does not exist, sent arguments that failed validation, or emitted arguments that were not parseable JSON.",
    percent: true,
    digits: 1,
    direction: "higher",
  },
  {
    key: "recovery_rate",
    header: "Recovery",
    title: "Metric 6: recovery rate",
    description:
      "Of the commands that failed, the fraction the agent adapted after. A failure counts as recovered when at least one of the next two steps is something other than a re-issue of the same command. Measured over runs where something failed, so a row with nothing failing reads n/a.",
    percent: true,
    digits: 1,
    direction: "higher",
  },
  {
    key: "redundant_action_rate",
    header: "Redundancy",
    title: "Metric 5: redundant action rate",
    description:
      "Byte identical repeat commands divided by total commands. Read this against the reference solution, not against zero: running a test suite again after changing the code is a repeat command and exactly the right thing to do. The signal is the gap between the agent's rate and the reference's.",
    percent: true,
    digits: 1,
    direction: "lower",
  },
  {
    key: "premature_termination_rate",
    header: "Premature finish",
    title: "Metric 7: premature termination rate",
    description:
      "The share of runs where the agent called finish while the hidden tests still fail. This is the failure a pass rate is least able to see: the run looks like a clean completion from the outside, with no timeout, no error and no budget exhaustion.",
    percent: true,
    digits: 1,
    direction: "lower",
  },
  {
    key: "context_drift",
    header: "Context drift",
    title: "Metric 8: context drift",
    description:
      "Judge scored. 0 means the final third of the trajectory is still on task, 1 means it has drifted entirely. Reads n/a when the rubric judge did not run.",
    percent: false,
    digits: 3,
    direction: "lower",
  },
] as const;

/** The two scalar cost columns, which are plain numbers rather than a mean with a spread. */
export const COST_COLUMNS = {
  cost_per_solved_usd: {
    title: "Metric 9a: mean cost per solved task",
    description:
      "Summed provider cost divided by the number of solved runs. Null, and shown as n/a, when nothing was solved: a cost per solved task with no solved tasks behind it is not a number.",
  },
  mean_wall_clock_s: {
    title: "Metric 9b: mean wall clock",
    description: "Mean wall clock seconds per run, including the verification phase.",
  },
} as const;

/** Read a `MetricStat` off a row by column key, for the table's cell and sort functions. */
export function statOf(row: LeaderboardRow, key: keyof LeaderboardRow): MetricStat | undefined {
  const value = row[key];
  if (value && typeof value === "object" && "mean" in value && "n" in value) {
    return value as MetricStat;
  }
  return undefined;
}

/** What the backend label means, shown on the chip. */
export const BACKEND_NOTE: Record<string, string> = {
  docker: "Container sandbox. The agent could not reach the hidden tests.",
  local:
    "Unisolated local sandbox. These runs form their own rows and are never averaged with container runs, because the local backend cannot guarantee the agent did not see the hidden tests.",
};

/** What the run status means. `completed` says the agent called finish, nothing more. */
export const STATUS_NOTE: Record<string, string> = {
  completed: "The agent called finish. This says nothing about whether the task was solved.",
  max_steps: "The agent hit the step ceiling for the task.",
  timeout: "The run hit its wall clock ceiling.",
  budget_exceeded: "The next model call would have crossed the spend ceiling.",
  error: "The harness stopped the run.",
};
