"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type SortingState,
} from "@tanstack/react-table";

import { COST_COLUMNS, STAT_COLUMNS, statOf } from "@/lib/metrics";
import { isStubModel, seconds, splitModel, usd } from "@/lib/format";
import type { LeaderboardRow, SandboxBackend } from "@/lib/types";
import { BackendChip, Chip, EmptyState, SolveRateBar, StatCell } from "./ui";

const helper = createColumnHelper<LeaderboardRow>();

/** Every cell in the table is `text-left`; numbers stay readable by being tabular. */
const TH = "sticky top-0 z-10 bg-raised px-2.5 py-2 text-left align-bottom border-b border-line";
const TD = "px-2.5 py-2 align-middle border-b border-line";

function ModelCell({ row }: { row: LeaderboardRow }) {
  const { prefix, name } = splitModel(row.model);
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="flex min-w-0 flex-wrap items-baseline">
        {prefix ? (
          <span className="font-mono text-[12px] text-dim">
            {row.model.slice(0, prefix.length + 1)}
          </span>
        ) : null}
        <span className="font-mono text-[13px] font-medium break-all">{name}</span>
      </span>
      <span className="flex flex-wrap items-center gap-1">
        {isStubModel(row.model) ? (
          <Chip
            tone="warn"
            title="A scripted offline policy, not a language model. Stubs validate the harness and give the demo real trajectories to replay."
          >
            scripted policy
          </Chip>
        ) : null}
        {row.destructive_attempts > 0 ? (
          <Chip
            tone="bad"
            title="Metric 10: commands matching the destructive pattern list. Flagged, never blocked, because the tendency is the finding."
          >
            {row.destructive_attempts} destructive
          </Chip>
        ) : null}
      </span>
    </div>
  );
}

function SortIndicator({ state }: { state: false | "asc" | "desc" }) {
  return (
    <span aria-hidden="true" className="ml-1 inline-block w-2 text-[9px] text-dim">
      {state === "asc" ? "▲" : state === "desc" ? "▼" : ""}
    </span>
  );
}

export function LeaderboardTable({
  rows,
  suites,
  backends,
}: {
  rows: LeaderboardRow[];
  suites: string[];
  backends: SandboxBackend[];
}) {
  const [suite, setSuite] = useState<string>("all");
  const [backend, setBackend] = useState<string>("all");
  const [sorting, setSorting] = useState<SortingState>([{ id: "solve_rate", desc: true }]);

  const filtered = useMemo(
    () =>
      rows.filter(
        (row) =>
          (suite === "all" || row.suite === suite) &&
          (backend === "all" || row.backend === backend),
      ),
    [rows, suite, backend],
  );

  const columns = useMemo(
    () => [
      helper.display({
        id: "model",
        header: "Model",
        cell: (info) => <ModelCell row={info.row.original} />,
      }),
      helper.accessor("backend", {
        id: "backend",
        header: "Backend",
        cell: (info) => <BackendChip backend={info.getValue()} />,
      }),
      helper.accessor((row) => row.runs, {
        id: "runs",
        header: "Runs",
        meta: {
          title: "Runs behind the row",
          description:
            "Runs behind the row, over the distinct tasks and seeds they cover. A mean over one seed has no spread to report.",
        },
        cell: (info) => (
          <span
            className="tnum font-mono text-[12px]"
            title={`${info.row.original.tasks} tasks x ${info.row.original.seeds} seeds`}
          >
            {info.row.original.runs}
          </span>
        ),
      }),
      ...STAT_COLUMNS.map((column) =>
        helper.accessor(
          (row) => {
            const stat = statOf(row, column.key);
            // undefined, not 0, so an empty group sorts last in both directions instead
            // of pretending to be a measured zero.
            return !stat || stat.n === 0 ? undefined : stat.mean;
          },
          {
            id: column.key as string,
            header: column.header,
            sortUndefined: "last",
            sortDescFirst: column.direction !== "lower",
            meta: { title: column.title, description: column.description },
            cell: (info) =>
              column.key === "solve_rate" ? (
                <SolveRateBar stat={statOf(info.row.original, column.key)} />
              ) : (
                <StatCell
                  stat={statOf(info.row.original, column.key)}
                  percent={column.percent}
                  digits={column.digits}
                />
              ),
          },
        ),
      ),
      helper.accessor((row) => row.cost_per_solved_usd ?? undefined, {
        id: "cost_per_solved_usd",
        header: "Cost / solved",
        sortUndefined: "last",
        meta: {
          title: COST_COLUMNS.cost_per_solved_usd.title,
          description: COST_COLUMNS.cost_per_solved_usd.description,
        },
        cell: (info) => (
          <span className="tnum font-mono text-[12px]">
            {info.row.original.cost_per_solved_usd == null ? (
              <span
                className="text-dim"
                title="Nothing was solved, so there is no cost per solved task."
              >
                n/a
              </span>
            ) : (
              usd(info.row.original.cost_per_solved_usd)
            )}
          </span>
        ),
      }),
      helper.accessor((row) => row.mean_wall_clock_s, {
        id: "mean_wall_clock_s",
        header: "Wall clock",
        meta: {
          title: COST_COLUMNS.mean_wall_clock_s.title,
          description: COST_COLUMNS.mean_wall_clock_s.description,
        },
        cell: (info) => (
          <span className="tnum font-mono text-[12px]">{seconds(info.getValue())}</span>
        ),
      }),
    ],
    [],
  );

  const table = useReactTable({
    data: filtered,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-end gap-x-4 gap-y-2">
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] tracking-wide text-dim uppercase">Suite</span>
          <select
            value={suite}
            onChange={(event) => setSuite(event.target.value)}
            className="min-w-[9rem] rounded-md border border-line bg-raised px-2 py-1 font-mono text-[12.5px] text-fg"
          >
            <option value="all">all suites</option>
            {suites.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-mono text-[10px] tracking-wide text-dim uppercase">Backend</span>
          <select
            value={backend}
            onChange={(event) => setBackend(event.target.value)}
            className="min-w-[9rem] rounded-md border border-line bg-raised px-2 py-1 font-mono text-[12.5px] text-fg"
          >
            <option value="all">all backends</option>
            {backends.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
        <p className="text-[12px] text-muted">
          {filtered.length} {filtered.length === 1 ? "row" : "rows"}. Click a header to sort.
        </p>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="No rows for this filter.">
          The bundle has no runs for that suite and backend combination. Widen the filter, or
          rebuild the bundle from a run directory that covers it.
        </EmptyState>
      ) : (
        <div className="scroll-x rounded-lg border border-line bg-raised">
          <table className="w-full min-w-[60rem] border-collapse text-[13px]">
            <caption className="sr-only">
              Aggregate results per model, suite and sandbox backend. Every metric column shows a
              mean with its standard deviation across seeds, and reads n/a when no runs sit behind
              it.
            </caption>
            <thead>
              {table.getHeaderGroups().map((group) => (
                <tr key={group.id}>
                  {group.headers.map((header) => {
                    const meta = header.column.columnDef.meta as
                      { title?: string; description?: string } | undefined;
                    const sorted = header.column.getIsSorted();
                    const sortable = header.column.getCanSort();
                    return (
                      <th
                        key={header.id}
                        scope="col"
                        // aria-sort belongs only on a column that can be sorted.
                        aria-sort={
                          !sortable
                            ? undefined
                            : sorted === "asc"
                              ? "ascending"
                              : sorted === "desc"
                                ? "descending"
                                : "none"
                        }
                        className={`${TH} ${header.column.id === "model" ? "sticky left-0 z-20 min-w-[11rem]" : ""}`}
                      >
                        {sortable ? (
                          <button
                            type="button"
                            onClick={header.column.getToggleSortingHandler()}
                            title={
                              meta?.description
                                ? `${meta.title}. ${meta.description}`
                                : "Sort by this column"
                            }
                            className="flex max-w-[5.5rem] items-baseline text-left font-mono text-[10.5px] leading-tight tracking-wide text-muted uppercase hover:text-fg"
                          >
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            <SortIndicator state={sorted} />
                          </button>
                        ) : (
                          <span className="font-mono text-[10.5px] tracking-wide text-muted uppercase">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                          </span>
                        )}
                      </th>
                    );
                  })}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map((row) => (
                <tr key={row.id} className="hover:bg-hover">
                  {row.getVisibleCells().map((cell) => (
                    <td
                      key={cell.id}
                      className={`${TD} ${
                        cell.column.id === "model"
                          ? "sticky left-0 z-10 bg-raised min-w-[11rem]"
                          : ""
                      }`}
                    >
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <details className="mt-3 rounded-lg border border-line bg-raised px-4 py-3">
        <summary className="cursor-pointer text-[13px] font-medium">
          What each column measures
        </summary>
        <dl className="mt-3 grid gap-3 sm:grid-cols-2">
          {STAT_COLUMNS.map((column) => (
            <div key={column.key as string}>
              <dt className="font-mono text-[11.5px] text-accent">{column.title}</dt>
              <dd className="mt-0.5 max-w-[60ch] text-[12.5px] text-muted">{column.description}</dd>
            </div>
          ))}
          {(Object.keys(COST_COLUMNS) as Array<keyof typeof COST_COLUMNS>).map((key) => (
            <div key={key}>
              <dt className="font-mono text-[11.5px] text-accent">{COST_COLUMNS[key].title}</dt>
              <dd className="mt-0.5 max-w-[60ch] text-[12.5px] text-muted">
                {COST_COLUMNS[key].description}
              </dd>
            </div>
          ))}
          <div>
            <dt className="font-mono text-[11.5px] text-accent">Backend</dt>
            <dd className="mt-0.5 max-w-[60ch] text-[12.5px] text-muted">
              Which sandbox produced the runs. Part of the row&apos;s identity: runs from the
              unisolated local backend form their own rows and are never averaged with container
              runs.
            </dd>
          </div>
          <div>
            <dt className="font-mono text-[11.5px] text-accent">Runs</dt>
            <dd className="mt-0.5 max-w-[60ch] text-[12.5px] text-muted">
              Runs behind the row, then the distinct tasks and seeds they cover. A mean over one
              seed has no spread to report, and every cell that aggregates across seeds says so.
            </dd>
          </div>
        </dl>
      </details>

      <p className="mt-3 text-[12.5px] text-muted">
        Per task results and the step by step replay of every run are linked from the task list
        below.{" "}
        <Link href="/failures/" className="text-accent underline underline-offset-2">
          The failure taxonomy
        </Link>{" "}
        explains what the harness looks for when a run does not solve its task.
      </p>
    </div>
  );
}
