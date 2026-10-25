import Link from "next/link";

import type { Run, Step } from "@/lib/types";

/**
 * One real run drawn against its task's reference path.
 *
 * The figure is the thesis of the project in one picture: a run whose outcome is a single
 * bit, and the steps that bit throws away. It is drawn from a run record in the bundle,
 * never from invented data, so every dot is a step that happened and the caption is
 * computed from the same record.
 *
 * Two drawings of the same run: a wide one with its callouts written on it, and a compact
 * one for a phone, where text inside a scaled drawing would be too small to read, so the
 * callouts move into a legend underneath.
 */

interface Geometry {
  width: number;
  height: number;
  x0: number;
  x1: number;
  refY: number;
  runY: number;
  dip: number;
  scale: number;
  labels: boolean;
}

const WIDE: Geometry = {
  width: 760,
  height: 160,
  x0: 104,
  x1: 688,
  refY: 34,
  runY: 104,
  dip: 14,
  scale: 1,
  labels: true,
};

const COMPACT: Geometry = {
  width: 360,
  height: 92,
  x0: 10,
  x1: 348,
  refY: 20,
  runY: 62,
  dip: 10,
  scale: 0.85,
  labels: false,
};

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** The last path segment of a model identifier, which is the name a reader recognises. */
export function shortModel(model: string): string {
  const parts = model.split("/");
  return parts[parts.length - 1] ?? model;
}

export interface FigureStory {
  /** Step indexes of the commands that exited non-zero. */
  failed: number[];
  /** Of those, the ones the agent changed course after. */
  adapted: number[];
  finishedRed: boolean;
}

function commandOf(step: Step): string | null {
  if (step.tool_name !== "bash") return null;
  const command = step.tool_args.command;
  return typeof command === "string" ? command : JSON.stringify(command ?? null);
}

function isFailed(step: Step): boolean {
  return (
    step.failed ?? (step.tool_name === "bash" && step.exit_code != null && step.exit_code !== 0)
  );
}

/**
 * What happened in the run, read off its steps.
 *
 * A failed command counts as adapted after when one of the next two steps does something
 * other than issue the same command again, the rule metric 6 scores. Ending the run is not
 * a change of course, so a finish does not count.
 */
export function storyOf(run: Run): FigureStory {
  const steps = run.steps;
  const failed = steps.filter((step) => isFailed(step) && commandOf(step) !== null);
  const adapted = failed.filter((step) => {
    const command = commandOf(step);
    return steps
      .slice(step.index + 1, step.index + 3)
      .some((later) =>
        commandOf(later) === null ? later.tool_name !== "finish" : commandOf(later) !== command,
      );
  });
  const last = steps[steps.length - 1];
  return {
    failed: failed.map((step) => step.index),
    adapted: adapted.map((step) => step.index),
    finishedRed: Boolean(last && last.tool_name === "finish" && !run.verification?.passed),
  };
}

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine"];

/** A count that opens a sentence, in words while it is small. */
function opening(count: number): string {
  return WORDS[count] ?? String(count);
}

/** The failure sentence of the caption, in words a reader can follow. */
function failureSentence(story: FigureStory): string {
  const failed = story.failed.length;
  const adapted = story.adapted.length;
  if (failed === 0) return "No command failed.";
  if (failed === 1) {
    return adapted === 1
      ? "One command failed, and it changed course."
      : "One command failed, and it tried the same thing again.";
  }
  const lead = `${opening(failed)} commands failed`;
  if (adapted === failed) return `${lead}, and it changed course after each.`;
  if (adapted === 0) return `${lead}, and it never changed course.`;
  return `${lead}, and it changed course after ${adapted} of them.`;
}

function Drawing({
  geo,
  run,
  story,
  referenceSteps,
  label,
  className,
}: {
  geo: Geometry;
  run: Run;
  story: FigureStory;
  referenceSteps: number;
  label: string;
  className: string;
}) {
  const steps = run.steps;
  const count = Math.max(steps.length, referenceSteps, 2);
  const gap = (geo.x1 - geo.x0) / (count - 1);
  const x = (index: number) => geo.x0 + index * gap;
  const refEnd = x(Math.max(referenceSteps - 1, 0));
  const failedAt = new Set(story.failed);
  const points = steps.map((step) => ({
    x: x(step.index),
    y: failedAt.has(step.index) ? geo.runY + geo.dip : geo.runY,
    step,
  }));
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(1)},${p.y}`).join(" ");
  const end = points[points.length - 1];
  const verification = run.verification;
  const tests = verification
    ? `${verification.tests_passed} of ${verification.tests_total} hidden tests passing`
    : "hidden tests never ran";
  const marked = story.adapted[0] ?? story.failed[0];
  const callout = marked == null ? null : points[marked];
  const calloutText =
    story.adapted[0] != null ? "command failed, then it changed course" : "command failed";
  const endText = story.finishedRed ? `called finish, ${tests}` : `stopped, ${tests}`;
  const endAnchor =
    end && end.x > geo.width - 210 ? "end" : end && end.x < 210 ? "start" : "middle";
  const calloutAnchor =
    callout && callout.x < 230
      ? "start"
      : callout && callout.x > geo.width - 230
        ? "end"
        : "middle";
  const r = (size: number) => size * geo.scale;
  const delay = (index: number) =>
    `${(0.2 + (index / Math.max(steps.length, 1)) * 1.1).toFixed(2)}s`;

  return (
    <svg
      viewBox={`0 0 ${geo.width} ${geo.height}`}
      role="img"
      aria-label={label}
      className={`h-auto w-full ${className}`}
    >
      {geo.labels ? (
        <text x="8" y={geo.refY + 4} className="fill-[var(--fg-muted)] font-mono text-[11px]">
          reference
        </text>
      ) : null}
      <line
        x1={geo.x0}
        y1={geo.refY}
        x2={refEnd}
        y2={geo.refY}
        stroke="var(--line-strong)"
        strokeWidth={2 * geo.scale}
      />
      {Array.from({ length: Math.max(referenceSteps - 1, 0) }, (_, index) => (
        <circle key={index} cx={x(index)} cy={geo.refY} r={r(3.6)} fill="var(--fg-dim)" />
      ))}
      <circle cx={refEnd} cy={geo.refY} r={r(6)} fill="var(--ok)" />
      {geo.labels ? (
        <text
          x={clamp(refEnd + 12, 0, geo.width - 48)}
          y={geo.refY + 4}
          className="fill-[var(--ok)] font-mono text-[11px]"
        >
          solved
        </text>
      ) : null}

      {geo.labels ? (
        <text x="8" y={geo.runY + 4} className="fill-[var(--fg-muted)] font-mono text-[11px]">
          this run
        </text>
      ) : null}
      <path
        d={path}
        pathLength={1}
        className="trace-path"
        fill="none"
        stroke="var(--accent)"
        strokeWidth={2.4 * geo.scale}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      {points.map((point) => {
        const isEnd = point === end;
        const failed = failedAt.has(point.step.index);
        const color = isEnd
          ? story.finishedRed
            ? "var(--bad)"
            : "var(--warn)"
          : failed
            ? "var(--warn)"
            : point.step.schema_violation
              ? "var(--bad)"
              : "var(--accent)";
        return (
          <circle
            key={point.step.index}
            className="trace-dot"
            style={{ animationDelay: delay(point.step.index) }}
            cx={point.x}
            cy={point.y}
            r={r(isEnd ? 6.5 : failed ? 5 : 4)}
            fill={color}
          >
            <title>
              {`step ${point.step.index + 1}: ${point.step.tool_name}${
                failed ? `, exited ${point.step.exit_code}` : ""
              }${point.step.schema_violation ? ", malformed call" : ""}`}
            </title>
          </circle>
        );
      })}

      {geo.labels && callout && callout !== end ? (
        <>
          <line
            x1={callout.x}
            y1={callout.y + 6}
            x2={callout.x}
            y2={geo.runY + geo.dip + 18}
            stroke="var(--warn)"
            strokeWidth="1"
          />
          <text
            x={calloutAnchor === "start" ? callout.x - 6 : callout.x}
            y={geo.runY + geo.dip + 32}
            textAnchor={calloutAnchor}
            className="fill-[var(--warn)] font-mono text-[10.5px]"
          >
            {calloutText}
          </text>
        </>
      ) : null}

      {geo.labels && end ? (
        <>
          <line
            x1={end.x}
            y1={end.y - 7}
            x2={end.x}
            y2={geo.runY - 26}
            stroke={story.finishedRed ? "var(--bad)" : "var(--warn)"}
            strokeWidth="1"
          />
          <text
            x={end.x}
            y={geo.runY - 32}
            textAnchor={endAnchor}
            className={`font-mono text-[10.5px] ${
              story.finishedRed ? "fill-[var(--bad)]" : "fill-[var(--warn)]"
            }`}
          >
            {endText}
          </text>
        </>
      ) : null}
    </svg>
  );
}

function Swatch({ color }: { color: string }) {
  return (
    <span
      aria-hidden="true"
      className="mt-[5px] inline-block h-2 w-2 shrink-0 rounded-full"
      style={{ background: color }}
    />
  );
}

export function TrajectoryFigure({
  run,
  referenceSteps,
  taskTitle,
}: {
  run: Run;
  referenceSteps: number;
  taskTitle: string;
}) {
  const story = storyOf(run);
  const steps = run.steps.length;
  const verification = run.verification;
  const tests = verification
    ? `${verification.tests_passed} of ${verification.tests_total} hidden tests passing`
    : "the hidden tests never run";
  const model = shortModel(run.config.model);
  const task = taskTitle.charAt(0).toLowerCase() + taskTitle.slice(1);
  const ending = story.finishedRed
    ? `Then it called finish with ${tests}.`
    : `It stopped with ${tests}.`;
  const label =
    `${model} on ${run.task_id}, seed ${run.config.seed}: ${steps} steps against a reference ` +
    `of ${referenceSteps}. ${failureSentence(story)} ${ending}`;

  return (
    <figure className="rounded-lg border border-line bg-raised px-3.5 pt-4 pb-3 sm:px-5">
      <div className="max-w-[880px]">
        <Drawing
          geo={WIDE}
          run={run}
          story={story}
          referenceSteps={referenceSteps}
          label={label}
          className="hidden sm:block"
        />
        <Drawing
          geo={COMPACT}
          run={run}
          story={story}
          referenceSteps={referenceSteps}
          label={label}
          className="block sm:hidden"
        />
      </div>
      <ul className="mt-2 flex flex-col gap-1 font-mono text-[11px] text-muted sm:hidden">
        <li className="flex gap-2">
          <Swatch color="var(--fg-dim)" />
          top line: the reference, {referenceSteps} steps to solved
        </li>
        <li className="flex gap-2">
          <Swatch color="var(--accent)" />
          lower line: this run, {steps} steps
        </li>
        {story.failed.length > 0 ? (
          <li className="flex gap-2">
            <Swatch color="var(--warn)" />
            each dip: a command that failed
          </li>
        ) : null}
        <li className="flex gap-2">
          <Swatch color={story.finishedRed ? "var(--bad)" : "var(--warn)"} />
          last dot: {story.finishedRed ? "called finish" : "stopped"}, {tests}
        </li>
      </ul>
      <figcaption className="mt-3 flex max-w-[72ch] flex-col gap-1.5 text-[13px] leading-relaxed text-muted">
        <span>
          A real run from this dataset, drawn step by step: <b className="text-fg">{model}</b> on{" "}
          <span className="font-mono text-[12px]">{run.task_id}</span>, seed {run.config.seed}, a
          task to {task}. It took {steps} steps where the reference takes {referenceSteps}.{" "}
          {failureSentence(story)} {ending} The outcome is one bit, not solved. The steps are the
          rest of the story, and they are what a leaderboard built on pass rate throws away.
        </span>
        <Link
          href={`/runs/${run.id}/`}
          className="w-fit font-mono text-[12px] text-accent underline underline-offset-2"
        >
          Replay this run
        </Link>
      </figcaption>
    </figure>
  );
}
