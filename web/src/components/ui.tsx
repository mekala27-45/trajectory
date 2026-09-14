import type { ReactNode } from "react";

import { failureColor, failureTint } from "@/lib/colors";
import { BACKEND_NOTE, STATUS_NOTE } from "@/lib/metrics";
import { renderMean, renderSpread } from "@/lib/format";
import type { MetricStat, RunStatus, SandboxBackend } from "@/lib/types";

/** Small presentational pieces shared by every route. No hooks, no data access. */

type Tone = "neutral" | "accent" | "ok" | "warn" | "bad";

const TONE_CLASS: Record<Tone, string> = {
  neutral: "border-line bg-sunken text-muted",
  accent: "border-accent-line bg-accent-bg text-accent",
  ok: "border-ok/40 bg-ok-bg text-ok",
  warn: "border-warn/40 bg-warn-bg text-warn",
  bad: "border-bad/40 bg-bad-bg text-bad",
};

export function Chip({
  children,
  tone = "neutral",
  title,
  className = "",
}: {
  children: ReactNode;
  tone?: Tone;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex max-w-full items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11px] leading-tight ${TONE_CLASS[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

/** The sandbox a run came from. Part of a row's identity, so it is always visible. */
export function BackendChip({ backend }: { backend: SandboxBackend }) {
  return (
    <Chip tone={backend === "local" ? "warn" : "neutral"} title={BACKEND_NOTE[backend]}>
      {backend}
    </Chip>
  );
}

/** Why the agent loop stopped. Never whether the task was solved. */
export function StatusChip({ status }: { status: RunStatus }) {
  const tone: Tone = status === "completed" ? "neutral" : status === "error" ? "bad" : "warn";
  return (
    <Chip tone={tone} title={STATUS_NOTE[status]}>
      {status.replace(/_/g, " ")}
    </Chip>
  );
}

/** The verdict: hidden tests passed or they did not. */
export function VerdictChip({
  solved,
  passed,
  total,
}: {
  solved: boolean;
  passed?: number;
  total?: number;
}) {
  const tests = total != null && total > 0 ? ` ${passed ?? 0}/${total} hidden tests` : "";
  return (
    <Chip
      tone={solved ? "ok" : "bad"}
      title={
        solved
          ? "Every hidden test passed."
          : "At least one hidden test failed, or verification never ran."
      }
    >
      {solved ? "solved" : "not solved"}
      {tests ? <span className="text-fg/70">{tests}</span> : null}
    </Chip>
  );
}

/**
 * One failure mode, as a chip.
 *
 * Used in three places (the replay header, a flagged step card, and the failure page
 * filter) so that a mode looks the same everywhere. The swatch carries the colour, the
 * text carries the meaning, and neither depends on the other.
 */
export function FailureChip({
  id,
  name,
  detector,
  confidence,
  active = false,
  disabled = false,
  suffix,
  onClick,
  expanded,
  pressed,
  title,
}: {
  id: string;
  name: string;
  detector?: string;
  confidence?: number;
  active?: boolean;
  disabled?: boolean;
  suffix?: string;
  onClick?: () => void;
  expanded?: boolean;
  pressed?: boolean;
  title?: string;
}) {
  const color = failureColor(id);
  const style = disabled
    ? undefined
    : { borderColor: color, background: failureTint(id, active ? 26 : 10) };
  const className = `inline-flex shrink-0 items-center gap-1.5 rounded border px-1.5 py-0.5 font-mono text-[11px] ${
    disabled ? "border-line bg-sunken text-dim" : "border-line text-fg"
  } ${active ? "font-semibold" : ""}`;

  const body = (
    <>
      <span
        aria-hidden="true"
        className="h-2 w-2 shrink-0 rounded-[2px]"
        style={{ background: disabled ? "var(--line-strong)" : color }}
      />
      <span>
        {id} {name}
      </span>
      {detector ? (
        <span className="text-dim">
          {detector}
          {confidence == null ? "" : ` ${confidence.toFixed(2)}`}
        </span>
      ) : null}
      {suffix ? <span className="text-dim">{suffix}</span> : null}
    </>
  );

  if (!onClick) {
    return (
      <span className={className} style={style} title={title}>
        {body}
      </span>
    );
  }
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-expanded={expanded}
      aria-pressed={pressed}
      title={title}
      className={`${className} enabled:hover:brightness-110 disabled:cursor-not-allowed`}
      style={style}
    >
      {body}
    </button>
  );
}

export function Tag({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex rounded border border-line bg-sunken px-1.5 py-0.5 font-mono text-[11px] text-muted">
      {children}
    </span>
  );
}

/**
 * Difficulty as five pips.
 *
 * The tier is an ordinal from 1 to 5 and reads faster as a shape than as a digit. The
 * digit is still in the label for anyone who wants the number, and the whole thing
 * carries an accessible text alternative.
 */
export function DifficultyMeter({ tier, className = "" }: { tier: number; className?: string }) {
  const clamped = Math.min(5, Math.max(1, Math.round(tier)));
  // One class per pip: two background utilities on the same element would resolve by
  // stylesheet order rather than by the order they are written here.
  const filled = clamped >= 4 ? "bg-warn" : "bg-accent";
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <span className="flex gap-[3px]" aria-hidden="true">
        {[1, 2, 3, 4, 5].map((pip) => (
          <span
            key={pip}
            className={`h-3 w-[7px] rounded-[2px] ${pip <= clamped ? filled : "bg-line"}`}
          />
        ))}
      </span>
      <span className="font-mono text-[11px] text-muted">
        <span className="sr-only">Difficulty tier </span>
        {clamped}/5
      </span>
    </span>
  );
}

/**
 * Solve rate as a bar inside the cell.
 *
 * The bar is the comparison and the number is the value; both are needed. An empty group
 * gets no bar at all, because a zero length bar reads as a measured zero.
 */
export function SolveRateBar({
  stat,
  width = 84,
}: {
  stat: MetricStat | undefined;
  width?: number;
}) {
  if (!stat || stat.n === 0) {
    return (
      <span className="font-mono text-[12px] text-dim" title="No runs behind this number.">
        n/a
      </span>
    );
  }
  const mean = Math.min(1, Math.max(0, stat.mean));
  const low = Math.max(0, mean - stat.stdev);
  const high = Math.min(1, mean + stat.stdev);
  return (
    <span className="flex items-center gap-2">
      <span
        className="relative h-[18px] shrink-0 overflow-hidden rounded-[3px] border border-line bg-sunken"
        style={{ width }}
        role="img"
        aria-label={`Solve rate ${(mean * 100).toFixed(1)} percent, standard deviation ${(stat.stdev * 100).toFixed(1)} percent, over ${stat.n} seeds`}
      >
        <span
          className="absolute inset-y-0 left-0 bg-accent/70"
          style={{ width: `${mean * 100}%` }}
        />
        {/* The spread as an error bar rather than a second fill: a lighter block on a
            dark track reads as a gap, and a whisker reads as a range. */}
        {stat.stdev > 0 ? (
          <>
            <span
              className="absolute top-1/2 h-[2px] -translate-y-1/2 bg-fg/55"
              style={{ left: `${low * 100}%`, width: `${(high - low) * 100}%` }}
            />
            <span
              className="absolute inset-y-[3px] w-[2px] bg-fg/70"
              style={{ left: `${low * 100}%` }}
            />
            <span
              className="absolute inset-y-[3px] w-[2px] bg-fg/70"
              style={{ left: `calc(${high * 100}% - 2px)` }}
            />
          </>
        ) : null}
      </span>
      <span className="tnum whitespace-nowrap font-mono text-[12px]">
        {(mean * 100).toFixed(1)}%
      </span>
    </span>
  );
}

/**
 * A mean with its spread.
 *
 * A pass rate reported without variance across seeds is not a result, so the spread is
 * shown on every cell that has one rather than hidden behind a hover.
 */
export function StatCell({
  stat,
  percent = false,
  digits = 3,
}: {
  stat: MetricStat | undefined;
  percent?: boolean;
  digits?: number;
}) {
  if (!stat || stat.n === 0) {
    return (
      <span
        className="font-mono text-[12px] text-dim"
        title="No runs behind this number, so there is nothing to report."
      >
        n/a
      </span>
    );
  }
  return (
    <span className="flex flex-col leading-tight">
      <span className="tnum font-mono text-[12px]">{renderMean(stat, { percent, digits })}</span>
      <span className="tnum font-mono text-[10px] text-dim">
        {renderSpread(stat, { percent, digits })}
      </span>
    </span>
  );
}

/**
 * The spread alone, for the places where the mean is already on screen as a bar.
 *
 * A pass rate reported without variance across seeds is not a result, so the spread
 * follows the bar rather than being dropped for being redundant with it.
 */
export function StatSpread({
  stat,
  percent = true,
  digits = 1,
}: {
  stat: MetricStat | undefined;
  percent?: boolean;
  digits?: number;
}) {
  if (!stat || stat.n === 0) {
    return <span className="font-mono text-[11px] text-dim">no runs behind this</span>;
  }
  return (
    <span className="tnum font-mono text-[11px] text-dim">
      {renderSpread(stat, { percent, digits })} over {stat.n} {stat.n === 1 ? "seed" : "seeds"}
    </span>
  );
}

/**
 * The dataset note, at full size.
 *
 * It explains that some rows come from an unisolated sandbox and that stub models are
 * scripted policies rather than language models. A reader who misses that misreads the
 * whole table, so it is not fine print.
 */
export function NoteBanner({ note }: { note: string }) {
  if (!note.trim()) return null;
  return (
    <aside
      aria-label="How to read these numbers"
      className="rounded-lg border border-warn/45 bg-warn-bg px-4 py-3"
    >
      <p className="mb-1 font-mono text-[11px] font-semibold tracking-wide text-warn uppercase">
        How to read these numbers
      </p>
      <p className="max-w-[95ch] text-[13.5px] leading-relaxed text-fg">{note}</p>
    </aside>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-line-strong bg-raised px-4 py-6 text-center">
      <p className="text-[13.5px] font-medium">{title}</p>
      {children ? (
        <p className="mx-auto mt-1 max-w-[60ch] text-[13px] text-muted">{children}</p>
      ) : null}
    </div>
  );
}

export function Panel({
  children,
  className = "",
  as: Element = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "article" | "figure";
}) {
  return (
    <Element className={`rounded-lg border border-line bg-raised ${className}`}>{children}</Element>
  );
}

/** A label above a value, for the header strips. */
export function Field({
  label,
  children,
  title,
}: {
  label: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <div className="min-w-0" title={title}>
      <dt className="font-mono text-[10px] tracking-wide text-dim uppercase">{label}</dt>
      <dd className="tnum mt-0.5 font-mono text-[12.5px] break-words text-fg">{children}</dd>
    </div>
  );
}
