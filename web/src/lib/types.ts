/**
 * The data contract, mirrored by hand from
 * `packages/core/src/trajectory_core/models.py`.
 *
 * Hand written rather than generated, for two reasons. The generated output for a
 * Pydantic model with computed fields and enums is unreadable, and the act of writing
 * these out is what catches a drift between the API and the app. Every field here exists
 * on the Python side with the same name and the same nullability. `null` means the Python
 * field is `X | None`; `?` means the field can be absent from a payload.
 *
 * When models.py changes, change this file in the same commit.
 */

// --------------------------------------------------------------------------- enums

/** `Language`. Primary language a task is written in. */
export type Language = "python" | "typescript" | "go" | "sql" | "any";

/** `ToolName`. The fixed tool surface exposed to the agent. */
export type ToolName = "bash" | "read_file" | "write_file" | "list_dir" | "finish";

/**
 * `RunStatus`. Why the agent loop stopped. `completed` means the agent called `finish`,
 * which says nothing about whether the task was solved.
 */
export type RunStatus = "completed" | "max_steps" | "timeout" | "budget_exceeded" | "error";

/**
 * `SandboxBackend`. Which sandbox produced a run. `local` runs form their own
 * leaderboard rows and are never averaged with container runs.
 */
export type SandboxBackend = "docker" | "local";

/** `Detector`. Whether a failure mode came from a deterministic rule or the rubric judge. */
export type Detector = "rule" | "judge";

/** `FailureModeId`. Taxonomy identifiers, F01 to F10. */
export type FailureModeId =
  "F01" | "F02" | "F03" | "F04" | "F05" | "F06" | "F07" | "F08" | "F09" | "F10";

// ------------------------------------------------------------------------ task

/** `TaskSummary`. Public task metadata, without anything that describes the hidden tests. */
export interface TaskSummary {
  id: string;
  suite: string;
  title: string;
  description: string;
  language: Language;
  /** Difficulty tier from 1 to 5. */
  difficulty: number;
  tags: string[];
  max_steps: number;
  reference_step_count: number;
}

// ------------------------------------------------------------------ run config

/** `RunConfig`. Everything that can change between two runs of the same task. */
export interface RunConfig {
  model: string;
  temperature: number;
  max_steps: number;
  seed: number;
  tools_enabled: ToolName[];
  timeout_seconds: number;
  command_timeout_seconds: number;
  budget_usd: number | null;
  max_output_bytes: number;
  sandbox_backend: SandboxBackend;
}

// ------------------------------------------------------------ workspace state

/** `WorkspaceManifest`. Workspace relative path to a truncated SHA-256 of the contents. */
export interface WorkspaceManifest {
  files: Record<string, string>;
  truncated: boolean;
}

// -------------------------------------------------------------------------- step

/** `Step`. One tool call and its result. */
export interface Step {
  /** Zero based position in the trajectory. */
  index: number;
  timestamp: string;
  /** Assistant text that accompanied the tool call, if any. */
  thought: string | null;
  /** Name the model asked for. A free string, because an invented tool is data. */
  tool_name: string;
  /** Arguments as the model supplied them, before coercion. */
  tool_args: Record<string, unknown>;
  /** Combined stdout and stderr, possibly capped. */
  tool_output: string;
  /** Process exit code for bash steps, null for the others. */
  exit_code: number | null;
  duration_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  /** True when tool_output was cut at max_output_bytes. */
  truncated: boolean;
  /** True when the model called an unknown tool or supplied arguments that failed validation. */
  schema_violation: boolean;
  /** Harness side error message, if the tool call could not be run. */
  error: string | null;
  /** Computed on the Python side: true when the step produced a non-zero exit code. */
  failed?: boolean;
}

// ------------------------------------------------------------------ verification

/** `Verification`. Result of running the hidden tests after the agent phase. */
export interface Verification {
  passed: boolean;
  tests_passed: number;
  tests_total: number;
  stderr_tail: string;
  duration_ms: number;
  exit_code: number;
  /** False when the output could not be parsed into a passed and total count. */
  parse_ok: boolean;
  /** Computed on the Python side: fraction of hidden tests that passed. */
  partial_credit?: number;
}

// ------------------------------------------------------------------------ score

/** `TrajectoryScore`. The ten trajectory metrics for one run. */
export interface TrajectoryScore {
  /** Metric 1. Every hidden test passed. */
  solved: boolean;
  /** Metric 2. tests_passed divided by tests_total. */
  partial_credit: number;
  /** Metric 3. reference_step_count divided by steps taken, capped at 1. Null on unsolved runs. */
  step_efficiency: number | null;
  /** Metric 4. Valid tool calls divided by total tool calls. */
  tool_call_validity: number;
  /** Metric 5. Byte identical repeat commands divided by total commands. */
  redundant_action_rate: number;
  /** Metric 6. Of the steps that exited non-zero, the fraction the agent adapted after. */
  recovery_rate: number | null;
  /** Metric 7. The agent called finish while verification fails. */
  premature_termination: boolean;
  /** Metric 8. Judge scored drift in the final third of the trajectory. Null without a judge. */
  context_drift: number | null;
  /** Metric 9a. Summed provider cost for the run. */
  cost_usd: number;
  /** Metric 9b. Wall clock seconds for the run. */
  wall_clock_s: number;
  /** Metric 10. Commands matching the destructive pattern list. Flagged, never blocked. */
  destructive_attempts: number;
  total_steps: number;
  total_commands: number;
  schema_violations: number;
  failed_commands: number;
}

// ----------------------------------------------------------------- failure modes

/** `FailureModeHit`. One failure mode found in one run. */
export interface FailureModeHit {
  id: FailureModeId;
  name: string;
  /** How sure the detector is, 0 to 1. */
  confidence: number;
  detector: Detector;
  /** Concrete quote or summary a human can check the hit against. */
  evidence: string;
  /** Steps the hit points at, for highlighting in the replay. */
  step_indices: number[];
}

/** `FailureModeSpec`. One taxonomy entry, as the API and the docs serve it. */
export interface FailureModeSpec {
  id: FailureModeId;
  name: string;
  definition: string;
  detection: Detector;
  example: string;
}

/** `FailureModeCount`. How often one mode fired in a slice, over unsolved runs. */
export interface FailureModeCount {
  id: FailureModeId;
  name: string;
  count: number;
  /** Count divided by the number of unsolved runs in the slice. */
  share_of_failed_runs: number;
}

// ------------------------------------------------------------------ provenance

/** `RunnerFingerprint`. Where a run was produced. */
export interface RunnerFingerprint {
  os: string;
  os_release: string;
  arch: string;
  python_version: string;
  cpu_count: number;
  docker_version: string | null;
  sandbox_backend: SandboxBackend;
  ci: boolean;
}

// -------------------------------------------------------------------------- run

/** `Run`. One agent attempt at one task, start to finish. */
export interface Run {
  id: string;
  schema_version: number;
  task_id: string;
  suite: string;
  config: RunConfig;
  started_at: string;
  finished_at: string | null;
  status: RunStatus;
  steps: Step[];
  verification: Verification | null;
  score: TrajectoryScore | null;
  /** Classified failures, ranked by confidence descending. */
  failure_modes: FailureModeHit[];
  image_id: string | null;
  harness_version: string;
  runner_fingerprint: RunnerFingerprint;
  initial_workspace: WorkspaceManifest;
  final_workspace: WorkspaceManifest;
  /** True when the oldest tool outputs were summarised to stay inside the context window. */
  context_compressed: boolean;
  error: string | null;
  /** Computed on the Python side. */
  wall_clock_s?: number;
  /** Computed on the Python side: verification ran and every hidden test passed. */
  solved?: boolean;
  /** Computed on the Python side: summed provider cost across every step. */
  total_cost_usd?: number;
}

// -------------------------------------------------------------------- aggregates

/**
 * `MetricStat`. A mean with its spread across seeds.
 *
 * `n === 0` means the group is empty and the cell renders `n/a`. Step efficiency is
 * undefined on unsolved runs, so a model that solved nothing has no efficiency at all,
 * and printing a zero would read as a measurement of terrible efficiency rather than as
 * the absence of one.
 */
export interface MetricStat {
  mean: number;
  stdev: number;
  n: number;
}

/** `LeaderboardRow`. One model's aggregate performance on one suite and one backend. */
export interface LeaderboardRow {
  model: string;
  suite: string;
  /** Part of the row's identity, not a footnote. */
  backend: SandboxBackend;
  runs: number;
  tasks: number;
  seeds: number;
  solve_rate: MetricStat;
  partial_credit: MetricStat;
  /** Over solved runs only. */
  step_efficiency: MetricStat;
  tool_call_validity: MetricStat;
  redundant_action_rate: MetricStat;
  /** Over runs with a failure. */
  recovery_rate: MetricStat;
  premature_termination_rate: MetricStat;
  /** Over judged runs. */
  context_drift: MetricStat;
  mean_cost_usd: number;
  /** Null when nothing was solved. */
  cost_per_solved_usd: number | null;
  mean_wall_clock_s: number;
  total_cost_usd: number;
  destructive_attempts: number;
  last_run_at: string | null;
}

// --------------------------------------------------------------------- responses

/** `RunSummary`. A run without its trajectory. */
export interface RunSummary {
  id: string;
  task_id: string;
  suite: string;
  model: string;
  seed: number;
  backend: SandboxBackend;
  status: RunStatus;
  solved: boolean;
  tests_passed: number;
  tests_total: number;
  partial_credit: number;
  step_efficiency: number | null;
  tool_call_validity: number;
  redundant_action_rate: number;
  recovery_rate: number | null;
  premature_termination: boolean;
  context_drift: number | null;
  destructive_attempts: number;
  steps: number;
  cost_usd: number;
  wall_clock_s: number;
  /** Modes found, ranked by confidence. Identifiers only. */
  failure_modes: FailureModeId[];
  started_at: string;
}

/** `LeaderboardResponse`. The leaderboard, with enough context to interpret it. */
export interface LeaderboardResponse {
  generated_at: string;
  harness_version: string;
  suite: string | null;
  rows: LeaderboardRow[];
  last_run_at: string | null;
  /** Anything a reader needs in order not to misread the table. */
  note: string;
}

/** `TaskResults`. Per model results for one task. */
export interface TaskResults {
  task: TaskSummary;
  solve_rate_by_model: Record<string, MetricStat>;
  runs: RunSummary[];
}

/** `FailureModeBreakdown`. The taxonomy plus counts, sliced the two ways that are actionable. */
export interface FailureModeBreakdown {
  taxonomy: FailureModeSpec[];
  /** Counts across every unsolved run. */
  overall: FailureModeCount[];
  by_model: Record<string, FailureModeCount[]>;
  /** Keyed by the difficulty tier as a string. */
  by_difficulty: Record<string, FailureModeCount[]>;
  /** Denominator behind every share. */
  unsolved_runs: number;
}

/** `DatasetIndex`. What the bundle or the database contains. */
export interface DatasetIndex {
  generated_at: string;
  harness_version: string;
  schema_version: number;
  suites: string[];
  models: string[];
  backends: SandboxBackend[];
  run_count: number;
  task_count: number;
  solved_count: number;
  total_cost_usd: number;
  total_wall_clock_s: number;
  judge_model: string | null;
  /** How to read these numbers. */
  note: string;
}

/** Response shape of `GET /v1/runs/{id}/trajectory`. */
export interface TrajectoryPage {
  run_id: string;
  total: number;
  offset: number;
  limit: number;
  steps: Step[];
}
