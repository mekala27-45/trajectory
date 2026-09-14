import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

/**
 * The tests read the same bundle the site was built from, so the expectations track the
 * data rather than a snapshot of it. The bundle is regenerated with more models and more
 * runs before release, and nothing in the suite may hardcode a model, a task id or a
 * count.
 */
const DATA = path.join(__dirname, "..", "public", "data");

function read<T>(...relative: string[]): T {
  return JSON.parse(readFileSync(path.join(DATA, ...relative), "utf8")) as T;
}

export interface BundleRunSummary {
  id: string;
  task_id: string;
  model: string;
  steps: number;
  solved: boolean;
  failure_modes: string[];
}

export interface BundleStep {
  index: number;
  exit_code: number | null;
}

export interface BundleRun {
  id: string;
  steps: BundleStep[];
  failure_modes: Array<{ id: string; step_indices: number[] }>;
}

export const index = () =>
  read<{ suites: string[]; models: string[]; run_count: number; note: string }>("index.json");

export const leaderboard = () => read<{ rows: unknown[]; note: string }>("leaderboard.json");

export const tasks = () => read<Array<{ id: string; title: string }>>("tasks.json");

export const runSummaries = () => read<BundleRunSummary[]>("runs.json");

export const failures = () =>
  read<{
    taxonomy: Array<{ id: string; name: string }>;
    overall: Array<{ id: string; count: number }>;
    by_model: Record<string, unknown[]>;
  }>("failures.json");

export function runRecord(id: string): BundleRun {
  return read<BundleRun>("runs", `${id}.json`);
}

/** Every run record in the bundle, for the tests that need to find a particular shape. */
export function allRunRecords(): BundleRun[] {
  return readdirSync(path.join(DATA, "runs"))
    .filter((name) => name.endsWith(".json"))
    .map((name) => read<BundleRun>("runs", name));
}

/** The first step a detector flagged, or the first non-zero exit code. Mirrors the UI. */
export function firstFailureStep(run: BundleRun): number | null {
  const flagged = run.failure_modes.flatMap((hit) => hit.step_indices);
  if (flagged.length > 0) return Math.min(...flagged);
  const nonZero = run.steps.find((step) => step.exit_code != null && step.exit_code !== 0);
  return nonZero ? nonZero.index : null;
}

/** A run that has something for "jump to first failure" to land on. */
export function runWithAFailure(): BundleRun | null {
  const records = allRunRecords();
  const flagged = records.find((run) => run.failure_modes.length > 0);
  if (flagged) return flagged;
  return records.find((run) => firstFailureStep(run) != null) ?? null;
}

/** The longest run in the bundle, which is the most demanding replay to render. */
export function longestRun(): BundleRun {
  const records = allRunRecords();
  return records.reduce((best, run) => (run.steps.length > best.steps.length ? run : best));
}
