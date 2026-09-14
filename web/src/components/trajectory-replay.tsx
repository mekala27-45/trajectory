"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { StepCard } from "./step-card";
import { BackendChip, Chip, EmptyState, FailureChip, VerdictChip } from "./ui";
import { failureColor } from "@/lib/colors";
import { seconds, usd } from "@/lib/format";
import type { FailureModeHit, FailureModeSpec, Run, TaskSummary } from "@/lib/types";

const SHORTCUTS = [
  { keys: "j", action: "next step" },
  { keys: "k", action: "previous step" },
  { keys: "f", action: "first failure" },
  { keys: "e", action: "expand the focused output" },
  { keys: "Home / End", action: "first or last step" },
] as const;

/** Keys are ignored while the reader is typing into a control. */
function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable === true
  );
}

export function TrajectoryReplay({
  run,
  task,
  specs,
}: {
  run: Run;
  task: TaskSummary | null;
  specs: Record<string, FailureModeSpec | undefined>;
}) {
  const steps = run.steps;
  const [focused, setFocused] = useState<number | null>(null);
  const [openMode, setOpenMode] = useState<string | null>(null);
  const [showKeys, setShowKeys] = useState(false);
  const refs = useRef(new Map<number, HTMLElement>());
  const evidenceRef = useRef<HTMLDivElement | null>(null);

  const hitsByStep = useMemo(() => {
    const map = new Map<number, FailureModeHit[]>();
    for (const hit of run.failure_modes) {
      for (const index of hit.step_indices) {
        const bucket = map.get(index);
        if (bucket) bucket.push(hit);
        else map.set(index, [hit]);
      }
    }
    return map;
  }, [run.failure_modes]);

  /**
   * Where "jump to first failure" goes.
   *
   * The first step any detector flagged, and when nothing was flagged, the first command
   * that exited non-zero. A run where neither exists has nothing to jump to and the
   * control says so rather than moving the reader somewhere arbitrary.
   */
  const firstFailure = useMemo(() => {
    const flagged = [...hitsByStep.keys()].filter((index) => index >= 0 && index < steps.length);
    if (flagged.length > 0) return Math.min(...flagged);
    const nonZero = steps.find((step) => step.exit_code != null && step.exit_code !== 0);
    return nonZero ? nonZero.index : null;
  }, [hitsByStep, steps]);

  const registerRef = useCallback((index: number, element: HTMLElement | null) => {
    if (element) refs.current.set(index, element);
    else refs.current.delete(index);
  }, []);

  const focusStep = useCallback(
    (index: number, { scroll = true }: { scroll?: boolean } = {}) => {
      if (steps.length === 0) return;
      const clamped = Math.min(steps.length - 1, Math.max(0, index));
      const target = steps[clamped];
      if (!target) return;
      setFocused(target.index);
      const element = refs.current.get(target.index);
      if (!element) return;
      element.focus({ preventScroll: true });
      if (scroll) {
        element.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    },
    [steps],
  );

  const move = useCallback(
    (delta: number) => {
      if (steps.length === 0) return;
      const position = focused == null ? -1 : steps.findIndex((step) => step.index === focused);
      const next = position < 0 ? (delta > 0 ? 0 : steps.length - 1) : position + delta;
      focusStep(next);
    },
    [focused, focusStep, steps],
  );

  const jumpToFailure = useCallback(() => {
    if (firstFailure == null) return;
    const position = steps.findIndex((step) => step.index === firstFailure);
    focusStep(position < 0 ? 0 : position);
  }, [firstFailure, focusStep, steps]);

  const expandFocused = useCallback(() => {
    if (focused == null) return;
    const element = refs.current.get(focused);
    const button = element?.querySelector<HTMLButtonElement>('button[data-role="expand-output"]');
    button?.click();
  }, [focused]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) return;
      switch (event.key) {
        case "j":
          event.preventDefault();
          move(1);
          break;
        case "k":
          event.preventDefault();
          move(-1);
          break;
        case "f":
          event.preventDefault();
          jumpToFailure();
          break;
        case "e":
          event.preventDefault();
          expandFocused();
          break;
        case "Home":
          event.preventDefault();
          focusStep(0);
          break;
        case "End":
          event.preventDefault();
          focusStep(steps.length - 1);
          break;
        case "?":
          event.preventDefault();
          setShowKeys((value) => !value);
          break;
        default:
          break;
      }
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [expandFocused, focusStep, jumpToFailure, move, steps.length]);

  useEffect(() => {
    if (openMode) {
      evidenceRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [openMode]);

  const openHit = openMode ? run.failure_modes.find((hit) => hit.id === openMode) : undefined;

  return (
    <div>
      {/* 48px is the height of the site header, which is sticky above this one. */}
      <div className="sticky top-12 z-30 -mx-4 border-y border-line bg-page/95 px-4 py-1.5 backdrop-blur sm:-mx-6 sm:px-6">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <VerdictChip
            solved={run.verification?.passed ?? false}
            passed={run.verification?.tests_passed}
            total={run.verification?.tests_total}
          />
          {task ? (
            <Link
              href={`/tasks/${run.task_id}/`}
              className="min-w-0 truncate font-mono text-[12px] text-accent underline underline-offset-2"
              title={task.title}
            >
              {run.task_id}
            </Link>
          ) : (
            <span className="font-mono text-[12px]">{run.task_id}</span>
          )}
          <span className="min-w-0 truncate font-mono text-[12px] text-muted">
            {run.config.model}
          </span>
          <span className="tnum font-mono text-[11.5px] text-dim">seed {run.config.seed}</span>
          <BackendChip backend={run.runner_fingerprint.sandbox_backend} />
          <span className="tnum font-mono text-[11.5px] text-dim">
            {steps.length} {steps.length === 1 ? "step" : "steps"}
          </span>
          <span className="tnum font-mono text-[11.5px] text-dim">
            {usd(run.total_cost_usd ?? run.score?.cost_usd ?? 0)}
          </span>
          <span className="tnum font-mono text-[11.5px] text-dim">
            {seconds(run.wall_clock_s ?? run.score?.wall_clock_s ?? 0)}
          </span>

          <span className="ml-auto flex items-center gap-1.5">
            <button
              type="button"
              onClick={jumpToFailure}
              disabled={firstFailure == null}
              title={
                firstFailure == null
                  ? "Nothing was flagged and no command exited non-zero in this run."
                  : "Move to the first flagged step, or the first non-zero exit code."
              }
              className="rounded border border-accent-line bg-accent-bg px-2 py-1 font-mono text-[11px] text-accent disabled:cursor-not-allowed disabled:border-line disabled:bg-sunken disabled:text-dim"
            >
              jump to first failure
            </button>
            <button
              type="button"
              onClick={() => setShowKeys((value) => !value)}
              aria-expanded={showKeys}
              className="rounded border border-line bg-raised px-2 py-1 font-mono text-[11px] text-muted hover:bg-hover hover:text-fg"
            >
              keys
            </button>
          </span>
        </div>

        {run.failure_modes.length > 0 ? (
          <div className="scroll-x mt-1.5 flex gap-1.5 pb-0.5">
            {run.failure_modes.map((hit) => (
              <FailureChip
                key={`${hit.id}-${hit.detector}-${hit.confidence}`}
                id={hit.id}
                name={hit.name}
                detector={hit.detector}
                confidence={hit.confidence}
                active={openMode === hit.id}
                expanded={openMode === hit.id}
                onClick={() => setOpenMode(openMode === hit.id ? null : hit.id)}
                title="Show this mode's definition and the evidence behind the hit."
              />
            ))}
          </div>
        ) : null}
      </div>

      {showKeys ? (
        <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 rounded-lg border border-line bg-raised px-3 py-2">
          {SHORTCUTS.map((shortcut) => (
            <div key={shortcut.keys} className="flex items-center gap-1.5">
              <dt>
                <kbd className="rounded border border-line-strong bg-sunken px-1.5 py-0.5 font-mono text-[11px]">
                  {shortcut.keys}
                </kbd>
              </dt>
              <dd className="text-[12px] text-muted">{shortcut.action}</dd>
            </div>
          ))}
        </dl>
      ) : null}

      {openHit ? (
        <div
          ref={evidenceRef}
          className="mt-3 rounded-lg border bg-raised px-3.5 py-3"
          style={{ borderColor: failureColor(openHit.id) }}
        >
          <div className="flex flex-wrap items-baseline gap-x-2">
            <h2 className="flex items-center gap-1.5 font-mono text-[13px]">
              <span
                aria-hidden="true"
                className="h-2.5 w-2.5 rounded-[2px]"
                style={{ background: failureColor(openHit.id) }}
              />
              {openHit.id} {openHit.name}
            </h2>
            <span className="font-mono text-[11px] text-dim">
              found by {openHit.detector === "rule" ? "a rule" : "the rubric judge"}, confidence{" "}
              {openHit.confidence.toFixed(2)}
            </span>
          </div>
          {specs[openHit.id] ? (
            <p className="mt-1 max-w-[85ch] text-[12.5px] text-muted">
              {specs[openHit.id]?.definition}
            </p>
          ) : null}
          <p className="mt-2 max-w-[85ch] text-[13px] whitespace-pre-line">{openHit.evidence}</p>
          {openHit.step_indices.length > 0 ? (
            <p className="mt-2 flex flex-wrap items-center gap-1.5 font-mono text-[11px] text-dim">
              jump to step
              {openHit.step_indices.map((index) => (
                <button
                  key={index}
                  type="button"
                  onClick={() => {
                    const position = steps.findIndex((step) => step.index === index);
                    if (position >= 0) focusStep(position);
                  }}
                  className="rounded border border-line bg-sunken px-1.5 py-0.5 text-muted hover:text-fg"
                >
                  {index}
                </button>
              ))}
            </p>
          ) : null}
        </div>
      ) : null}

      <div className="mt-4 flex items-baseline justify-between gap-3">
        <h2 className="text-[16px] font-semibold">Trajectory</h2>
        <p className="hidden font-mono text-[11px] text-dim sm:block">
          <kbd className="rounded border border-line-strong bg-sunken px-1 py-0.5">j</kbd> and{" "}
          <kbd className="rounded border border-line-strong bg-sunken px-1 py-0.5">k</kbd> move
          between steps
        </p>
      </div>

      {steps.length === 0 ? (
        <div className="mt-3">
          <EmptyState title="This run recorded no steps.">
            {run.error
              ? `The harness stopped it before the first tool call: ${run.error}`
              : "The agent loop ended before the first tool call was recorded. The status and the verdict above are still the run's real outcome."}
          </EmptyState>
        </div>
      ) : (
        <ol className="mt-3 flex list-none flex-col gap-2.5 p-0">
          {steps.map((step) => (
            <StepCard
              key={`${step.index}-${step.timestamp}`}
              step={step}
              hits={hitsByStep.get(step.index) ?? []}
              specs={specs}
              focused={focused === step.index}
              onActivate={setFocused}
              registerRef={registerRef}
              total={steps.length}
            />
          ))}
        </ol>
      )}

      {run.context_compressed ? (
        <p className="mt-3 rounded-md border border-warn/40 bg-warn-bg px-3 py-2 text-[12.5px] text-warn">
          The oldest tool outputs in this run were summarised to stay inside the context window.
          Compression changes results, so it is recorded on the run rather than left silent.
        </p>
      ) : null}

      {steps.length > 0 ? (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-[12px] text-dim">
          <Chip>{steps.length} steps</Chip>
          <Chip>{run.score?.total_commands ?? 0} commands</Chip>
          <Chip tone={(run.score?.schema_violations ?? 0) > 0 ? "bad" : "neutral"}>
            {run.score?.schema_violations ?? 0} schema violations
          </Chip>
          <Chip tone={(run.score?.failed_commands ?? 0) > 0 ? "warn" : "neutral"}>
            {run.score?.failed_commands ?? 0} failed commands
          </Chip>
          <Chip tone={(run.score?.destructive_attempts ?? 0) > 0 ? "bad" : "neutral"}>
            {run.score?.destructive_attempts ?? 0} destructive attempts
          </Chip>
        </p>
      ) : null}
    </div>
  );
}
