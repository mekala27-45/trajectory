/**
 * One data client, two sources.
 *
 * With `NEXT_PUBLIC_API_URL` set, every function below talks to the results API. With it
 * unset, they read the JSON bundle in `public/data`, which is committed to the
 * repository. That is the default, and it is what makes the demo work for a stranger with
 * no key, no account and no network: `npm run build` reads files off disk and emits a
 * fully static site.
 *
 * Both paths return the identical shapes from `lib/types.ts`, so no page knows or cares
 * which one it is talking to. These functions run on the server only (file system access,
 * and build time fetches), which keeps the API base URL and any future auth header out of
 * the browser bundle.
 */

import { promises as fs } from "node:fs";
import path from "node:path";

import type {
  DatasetIndex,
  FailureModeBreakdown,
  LeaderboardResponse,
  Run,
  RunSummary,
  SandboxBackend,
  Step,
  TaskResults,
  TaskSummary,
  TrajectoryPage,
} from "./types";

/** Trailing slashes stripped so path joins below never produce a double slash. */
const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");

/** True when the app is reading from the live API rather than the committed bundle. */
export const usingLiveApi = API_URL.length > 0;

/** Where the static bundle lives, relative to the directory `next build` runs in. */
const DATA_DIR = path.join(process.cwd(), "public", "data");

/**
 * `force-cache` rather than the Next 15 default.
 *
 * A statically exported page cannot be rendered from an uncached request, so the live API
 * path bakes its responses at build time. Rebuild to publish new numbers.
 */
const FETCH_OPTIONS: RequestInit = { cache: "force-cache" };

class ApiError extends Error {
  constructor(url: string, status: number) {
    super(`GET ${url} returned ${status}`);
    this.name = "ApiError";
  }
}

async function fetchJson<T>(route: string): Promise<T> {
  const url = `${API_URL}${route}`;
  const response = await fetch(url, FETCH_OPTIONS);
  if (!response.ok) {
    throw new ApiError(url, response.status);
  }
  return (await response.json()) as T;
}

/** Fetch a route that is allowed to 404, for example an unknown run id. */
async function fetchJsonOrNull<T>(route: string): Promise<T | null> {
  const url = `${API_URL}${route}`;
  const response = await fetch(url, FETCH_OPTIONS);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new ApiError(url, response.status);
  }
  return (await response.json()) as T;
}

async function readJson<T>(relative: string): Promise<T> {
  const raw = await fs.readFile(path.join(DATA_DIR, relative), "utf8");
  return JSON.parse(raw) as T;
}

/** Read a bundle file that is allowed to be missing, for example a task with no results. */
async function readJsonOrNull<T>(relative: string): Promise<T | null> {
  try {
    return await readJson<T>(relative);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

/**
 * A task or run id arrives from a route segment. Anything that is not a plain identifier
 * is rejected before it reaches the file system or the API.
 */
const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

function safeId(id: string): string | null {
  return SAFE_ID.test(id) ? id : null;
}

// ---------------------------------------------------------------------------- index

/** What the dataset contains, and the note explaining how to read it. */
export async function getIndex(): Promise<DatasetIndex> {
  if (usingLiveApi) {
    return fetchJson<DatasetIndex>("/v1/index");
  }
  return readJson<DatasetIndex>("index.json");
}

// ---------------------------------------------------------------------- leaderboard

export interface LeaderboardFilters {
  suite?: string;
  backend?: SandboxBackend;
}

/**
 * Aggregate rows, one per model, suite and backend.
 *
 * The filters map onto the API query string. Against the static bundle they are applied
 * here, over the full row set, so both sources answer the same question the same way.
 */
export async function getLeaderboard(
  filters: LeaderboardFilters = {},
): Promise<LeaderboardResponse> {
  if (usingLiveApi) {
    const query = new URLSearchParams();
    if (filters.suite) query.set("suite", filters.suite);
    if (filters.backend) query.set("backend", filters.backend);
    const suffix = query.size > 0 ? `?${query.toString()}` : "";
    return fetchJson<LeaderboardResponse>(`/v1/leaderboard${suffix}`);
  }

  const board = await readJson<LeaderboardResponse>("leaderboard.json");
  const rows = board.rows.filter(
    (row) =>
      (!filters.suite || row.suite === filters.suite) &&
      (!filters.backend || row.backend === filters.backend),
  );
  return { ...board, suite: filters.suite ?? board.suite, rows };
}

// ---------------------------------------------------------------------------- tasks

/** Every task in the dataset, without anything that describes the hidden tests. */
export async function getTasks(): Promise<TaskSummary[]> {
  if (usingLiveApi) {
    return fetchJson<TaskSummary[]>("/v1/tasks");
  }
  return readJson<TaskSummary[]>("tasks.json");
}

/** Per model results for one task, or null when the task is not in the dataset. */
export async function getTaskResults(id: string): Promise<TaskResults | null> {
  const clean = safeId(id);
  if (!clean) return null;
  if (usingLiveApi) {
    return fetchJsonOrNull<TaskResults>(`/v1/tasks/${encodeURIComponent(clean)}/results`);
  }
  return readJsonOrNull<TaskResults>(path.join("tasks", `${clean}.json`));
}

// ----------------------------------------------------------------------------- runs

/**
 * Every run in the dataset, without trajectories.
 *
 * The API has no list-all endpoint by design (the number grows without bound), so the
 * live path assembles the list from the per task results, which is the same set of
 * summaries the bundle's `runs.json` holds.
 */
export async function listRuns(): Promise<RunSummary[]> {
  if (usingLiveApi) {
    const tasks = await getTasks();
    const perTask = await Promise.all(tasks.map((task) => getTaskResults(task.id)));
    return perTask.flatMap((results) => results?.runs ?? []);
  }
  return readJson<RunSummary[]>("runs.json");
}

/** Steps for one run, in pages. Only reachable against the live API. */
export async function getTrajectory(
  runId: string,
  { offset = 0, limit = 200 }: { offset?: number; limit?: number } = {},
): Promise<TrajectoryPage | null> {
  const clean = safeId(runId);
  if (!clean || !usingLiveApi) return null;
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return fetchJsonOrNull<TrajectoryPage>(
    `/v1/runs/${encodeURIComponent(clean)}/trajectory?${query.toString()}`,
  );
}

/** How many steps to ask the trajectory endpoint for at a time. */
const TRAJECTORY_PAGE_SIZE = 200;

/**
 * One full run record, trajectory included, or null when the id is unknown.
 *
 * `GET /v1/runs/{id}` is allowed to return the run with its steps omitted, because a long
 * trajectory is the one part of a run record that does not belong in a single response.
 * When that happens the steps are paged in from the trajectory endpoint, so the replay
 * page has the same complete record either way.
 */
export async function getRun(id: string): Promise<Run | null> {
  const clean = safeId(id);
  if (!clean) return null;

  if (!usingLiveApi) {
    return readJsonOrNull<Run>(path.join("runs", `${clean}.json`));
  }

  const run = await fetchJsonOrNull<Run>(`/v1/runs/${encodeURIComponent(clean)}`);
  if (!run) return null;

  const expected = run.score?.total_steps ?? 0;
  if (run.steps.length >= expected) {
    return run;
  }

  const steps: Step[] = [];
  let offset = 0;
  let total = expected;
  do {
    const page = await getTrajectory(clean, { offset, limit: TRAJECTORY_PAGE_SIZE });
    if (!page || page.steps.length === 0) break;
    steps.push(...page.steps);
    total = page.total;
    offset += page.steps.length;
  } while (steps.length < total);

  return { ...run, steps };
}

// ------------------------------------------------------------------- failure modes

/** The taxonomy plus counts by model and by difficulty tier. */
export async function getFailures(): Promise<FailureModeBreakdown> {
  if (usingLiveApi) {
    return fetchJson<FailureModeBreakdown>("/v1/failure-modes");
  }
  return readJson<FailureModeBreakdown>("failures.json");
}
