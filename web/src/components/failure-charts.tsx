"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Chip, EmptyState, FailureChip, Panel, VerdictChip } from "./ui";
import { FAILURE_IDS, failureColor } from "@/lib/colors";
import { percent, seconds } from "@/lib/format";
import type { FailureModeBreakdown, FailureModeId, FailureModeSpec, RunSummary } from "@/lib/types";

type Slice = { dimension: "model" | "difficulty"; key: string };
type Filter = { mode: FailureModeId; slice: Slice | null };

interface ChartRow {
  label: string;
  [mode: string]: string | number;
}

const TOOLTIP_STYLE = {
  background: "var(--bg-raised)",
  border: "1px solid var(--line-strong)",
  borderRadius: 6,
  color: "var(--fg)",
  fontSize: 12,
  fontFamily: "var(--font-mono)",
} as const;

/** Turn one slice of the breakdown into chart rows, one row per category. */
function toRows(slice: Record<string, Array<{ id: FailureModeId; count: number }>>): ChartRow[] {
  return Object.entries(slice)
    .sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }))
    .map(([label, counts]) => {
      const row: ChartRow = { label };
      for (const id of FAILURE_IDS) row[id] = 0;
      for (const entry of counts) row[entry.id] = entry.count;
      return row;
    });
}

/**
 * Model identifiers are long. A provider path prefix (`anthropic/claude-...`) is dropped
 * because the tooltip and the run list below still carry it in full. A `stub:` prefix is
 * never dropped: it is the difference between a language model and a scripted policy.
 */
function shortenCategory(value: string): string {
  const raw = String(value);
  const slash = raw.lastIndexOf("/");
  const name = slash >= 0 ? raw.slice(slash + 1) : raw;
  return name.length > 22 ? `${name.slice(0, 21)}...` : name;
}

/** Only stack the modes that actually fired, so the legend stays the size of the data. */
function activeModes(rows: ChartRow[]): FailureModeId[] {
  return FAILURE_IDS.filter((id) => rows.some((row) => Number(row[id] ?? 0) > 0));
}

export function FailureCharts({
  breakdown,
  runs,
  difficultyByTask,
  titleByTask,
}: {
  breakdown: FailureModeBreakdown;
  runs: RunSummary[];
  difficultyByTask: Record<string, number>;
  titleByTask: Record<string, string>;
}) {
  const [filter, setFilter] = useState<Filter | null>(null);

  const specById = useMemo<Record<string, FailureModeSpec | undefined>>(
    () => Object.fromEntries(breakdown.taxonomy.map((spec) => [spec.id, spec])),
    [breakdown.taxonomy],
  );

  const modelRows = useMemo(() => toRows(breakdown.by_model), [breakdown.by_model]);
  const difficultyRows = useMemo(() => toRows(breakdown.by_difficulty), [breakdown.by_difficulty]);
  const modelModes = activeModes(modelRows);
  const difficultyModes = activeModes(difficultyRows);
  const hasData = modelModes.length > 0 || difficultyModes.length > 0;

  const matching = useMemo(() => {
    if (!filter) return [];
    return runs.filter((run) => {
      if (run.solved) return false;
      if (!run.failure_modes.includes(filter.mode)) return false;
      if (!filter.slice) return true;
      if (filter.slice.dimension === "model") return run.model === filter.slice.key;
      return String(difficultyByTask[run.task_id] ?? "") === filter.slice.key;
    });
  }, [filter, runs, difficultyByTask]);

  const select = (mode: FailureModeId, slice: Slice | null) => {
    setFilter((current) =>
      current && current.mode === mode && current.slice?.key === slice?.key
        ? null
        : { mode, slice },
    );
  };

  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-5 xl:grid-cols-2">
        <ChartPanel
          heading="Failure modes by model"
          caption="One bar per model, stacked by failure mode, counted over every run that model produced rather than only the ones that failed. That is deliberate: three of the policies here solve every task, so a chart scoped to failures would show nothing at all for them. A taller bar means more runs carried a mode, and a run can carry several."
          rows={modelRows}
          modes={modelModes}
          layout="vertical"
          categoryLabel="Model"
          valueLabel="Runs carrying the mode (a run can carry several)"
          specById={specById}
          onSegment={(mode, key) => select(mode, { dimension: "model", key })}
        />
        <ChartPanel
          heading="Failure modes by task difficulty"
          caption="The same counts sliced by the task's difficulty tier, 1 to 5. This is the view that shows whether a mode is a general weakness or something that only appears once a task gets hard. Tiers hold different numbers of tasks, so compare the mix within a bar rather than the heights across bars."
          rows={difficultyRows}
          modes={difficultyModes}
          layout="horizontal"
          categoryLabel="Difficulty tier"
          valueLabel="Runs carrying the mode (a run can carry several)"
          specById={specById}
          onSegment={(mode, key) => select(mode, { dimension: "difficulty", key })}
        />
      </div>

      {!hasData ? (
        <p className="rounded-lg border border-line bg-raised px-4 py-3 text-[13px] text-muted">
          No failure modes fired in this dataset:{" "}
          {breakdown.unsolved_runs === 0
            ? "every recorded run solved its task, so there is nothing for the detectors to have found."
            : `${breakdown.unsolved_runs} runs did not solve their task, but no rule matched and the rubric judge did not run.`}{" "}
          The charts keep their axes so the shape of the question stays visible, and the taxonomy
          below is the reference either way.
        </p>
      ) : null}

      <section aria-labelledby="mode-filter">
        <h3 id="mode-filter" className="mb-2 text-[14px] font-semibold">
          Runs by failure mode
        </h3>
        <p className="mb-2 max-w-[80ch] text-[12.5px] text-muted">
          Click a bar segment above, or a mode here, to list the unsolved runs carrying it. Each one
          links to its replay with the flagged steps highlighted.
        </p>
        <div className="flex flex-wrap gap-1.5">
          {FAILURE_IDS.map((id) => {
            const total = breakdown.overall.find((entry) => entry.id === id)?.count ?? 0;
            const active = filter?.mode === id;
            return (
              <FailureChip
                key={id}
                id={id}
                name={specById[id]?.name ?? ""}
                suffix={`(${total})`}
                active={active}
                pressed={active}
                disabled={total === 0}
                onClick={() => select(id, null)}
                title={specById[id]?.definition ?? undefined}
              />
            );
          })}
          {filter ? (
            <button
              type="button"
              onClick={() => setFilter(null)}
              className="rounded border border-line bg-raised px-1.5 py-0.5 font-mono text-[11px] text-muted hover:text-fg"
            >
              clear filter
            </button>
          ) : null}
        </div>

        <div className="mt-3">
          {!filter ? (
            <p className="rounded-lg border border-dashed border-line-strong bg-raised px-4 py-3 text-[13px] text-muted">
              Pick a mode to list its runs. A mode with no runs behind it cannot be selected.
            </p>
          ) : matching.length === 0 ? (
            <EmptyState title={`No runs to list for ${filter.mode}.`}>
              The count for this slice is zero, or the runs carrying it solved their task and so sit
              outside the unsolved denominator these counts use.
            </EmptyState>
          ) : (
            <Panel className="overflow-hidden">
              <p className="border-b border-line px-4 py-2 font-mono text-[11.5px] text-muted">
                {filter.mode} {specById[filter.mode]?.name}
                {filter.slice ? `, ${filter.slice.dimension} ${filter.slice.key}` : ""},{" "}
                {matching.length} {matching.length === 1 ? "run" : "runs"}
              </p>
              <ul className="divide-y divide-[color:var(--line)]">
                {matching.map((run) => (
                  <li key={run.id}>
                    <Link href={`/runs/${run.id}/`} className="block px-4 py-2.5 hover:bg-hover">
                      <span className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
                        <span className="font-mono text-[12px] text-accent">{run.task_id}</span>
                        <span className="font-mono text-[12px] text-muted">{run.model}</span>
                        <span className="tnum font-mono text-[11.5px] text-dim">
                          seed {run.seed}
                        </span>
                        <VerdictChip
                          solved={run.solved}
                          passed={run.tests_passed}
                          total={run.tests_total}
                        />
                        {run.failure_modes.map((mode) => (
                          <Chip key={mode} tone={mode === filter.mode ? "bad" : "neutral"}>
                            {mode}
                          </Chip>
                        ))}
                        <span className="ml-auto font-mono text-[11.5px] text-accent underline underline-offset-2">
                          replay
                        </span>
                      </span>
                      <span className="tnum mt-1 flex flex-wrap gap-x-4 font-mono text-[11px] text-dim">
                        <span>{titleByTask[run.task_id] ?? ""}</span>
                        <span>{run.steps} steps</span>
                        <span>partial credit {percent(run.partial_credit, 0)}</span>
                        <span>{seconds(run.wall_clock_s)}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </Panel>
          )}
        </div>
      </section>
    </div>
  );
}

function ChartPanel({
  heading,
  caption,
  rows,
  modes,
  layout,
  categoryLabel,
  valueLabel,
  specById,
  onSegment,
}: {
  heading: string;
  caption: string;
  rows: ChartRow[];
  modes: FailureModeId[];
  layout: "vertical" | "horizontal";
  categoryLabel: string;
  valueLabel: string;
  specById: Record<string, FailureModeSpec | undefined>;
  onSegment: (mode: FailureModeId, categoryKey: string) => void;
}) {
  const stacked = modes;
  const maxTotal = rows.reduce(
    (highest, row) =>
      Math.max(
        highest,
        modes.reduce((sum, mode) => sum + Number(row[mode] ?? 0), 0),
      ),
    0,
  );
  // Without bars Recharts has no domain to infer, and the value axis disappears with it.
  const valueDomain: [number, number] = [0, Math.max(1, maxTotal)];
  const summary = rows
    .map((row) => {
      const total = modes.reduce((sum, mode) => sum + Number(row[mode] ?? 0), 0);
      return `${row.label}: ${total}`;
    })
    .join("; ");

  return (
    <Panel as="figure" className="m-0 px-4 py-3">
      <figcaption>
        <h3 className="text-[14px] font-semibold">{heading}</h3>
        <p className="mt-1 max-w-[70ch] text-[12.5px] text-muted">{caption}</p>
      </figcaption>

      {rows.length === 0 ? (
        <p className="mt-3 text-[13px] text-muted">Nothing to chart in this slice.</p>
      ) : (
        <div
          className="mt-3"
          role="img"
          aria-label={`${heading}. ${valueLabel} by ${categoryLabel.toLowerCase()}. ${summary || "All counts are zero."}`}
        >
          <ResponsiveContainer width="100%" height={layout === "vertical" ? 240 : 260}>
            {layout === "vertical" ? (
              <BarChart
                data={rows}
                layout="vertical"
                margin={{ top: 4, right: 12, bottom: 24, left: 4 }}
              >
                <CartesianGrid stroke="var(--line)" horizontal={false} />
                <XAxis
                  type="number"
                  allowDecimals={false}
                  domain={valueDomain}
                  stroke="var(--line-strong)"
                  label={{ value: valueLabel, position: "insideBottom", offset: -16 }}
                />
                <YAxis
                  type="category"
                  dataKey="label"
                  width={132}
                  stroke="var(--line-strong)"
                  tickLine={false}
                  tickFormatter={shortenCategory}
                />
                <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "var(--bg-hover)" }} />
                <Legend
                  verticalAlign="top"
                  align="left"
                  wrapperStyle={{ fontSize: 11, paddingBottom: 8 }}
                />
                {stacked.map((mode) => (
                  <Bar
                    key={mode}
                    dataKey={mode}
                    name={`${mode} ${specById[mode]?.name ?? ""}`}
                    stackId="modes"
                    fill={failureColor(mode)}
                    cursor="pointer"
                    onClick={(entry: { label?: string }) => onSegment(mode, entry?.label ?? "")}
                  />
                ))}
              </BarChart>
            ) : (
              <BarChart data={rows} margin={{ top: 4, right: 12, bottom: 28, left: 4 }}>
                <CartesianGrid stroke="var(--line)" vertical={false} />
                <XAxis
                  dataKey="label"
                  stroke="var(--line-strong)"
                  tickLine={false}
                  label={{ value: categoryLabel, position: "insideBottom", offset: -18 }}
                />
                <YAxis
                  allowDecimals={false}
                  domain={valueDomain}
                  width={40}
                  stroke="var(--line-strong)"
                  label={{ value: "Runs", angle: -90, position: "insideLeft", offset: 14 }}
                />
                <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "var(--bg-hover)" }} />
                <Legend
                  verticalAlign="top"
                  align="left"
                  wrapperStyle={{ fontSize: 11, paddingBottom: 8 }}
                />
                {stacked.map((mode) => (
                  <Bar
                    key={mode}
                    dataKey={mode}
                    name={`${mode} ${specById[mode]?.name ?? ""}`}
                    stackId="modes"
                    fill={failureColor(mode)}
                    cursor="pointer"
                    onClick={(entry: { label?: string }) => onSegment(mode, entry?.label ?? "")}
                  />
                ))}
              </BarChart>
            )}
          </ResponsiveContainer>
        </div>
      )}

      <details className="mt-2">
        <summary className="cursor-pointer font-mono text-[11px] text-muted">
          Chart values as a table
        </summary>
        <div className="scroll-x mt-2">
          <table className="w-full border-collapse text-[12px]">
            <caption className="sr-only">{`${heading}, as numbers`}</caption>
            <thead>
              <tr>
                <th
                  scope="col"
                  className="border-b border-line px-2 py-1 text-left font-mono text-[10.5px] text-dim uppercase"
                >
                  {categoryLabel}
                </th>
                {stacked.map((mode) => (
                  <th
                    key={mode}
                    scope="col"
                    className="border-b border-line px-2 py-1 text-left font-mono text-[10.5px] text-dim"
                  >
                    {mode}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.label}>
                  <th
                    scope="row"
                    className="border-b border-line px-2 py-1 text-left font-mono text-[11.5px] font-normal"
                  >
                    {row.label}
                  </th>
                  {stacked.map((mode) => (
                    <td
                      key={mode}
                      className="tnum border-b border-line px-2 py-1 font-mono text-[11.5px]"
                    >
                      {Number(row[mode] ?? 0)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </Panel>
  );
}
