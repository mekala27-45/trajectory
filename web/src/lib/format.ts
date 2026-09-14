/**
 * Formatting helpers.
 *
 * Everything here is deterministic and locale independent. The site is prerendered, so a
 * value formatted with the build machine's locale and then reformatted with the reader's
 * would produce a hydration mismatch, and timestamps are shown in UTC because that is
 * what the harness records.
 */

import type { MetricStat } from "./types";

/**
 * Format a `MetricStat` the way `MetricStat.render` does on the Python side.
 *
 * A group with nothing in it renders `n/a`, never `0.000 +/- 0.000`. Step efficiency is
 * undefined on unsolved runs, so a model that solved nothing has no efficiency at all,
 * and printing a zero would read as a measurement of terrible efficiency rather than as
 * the absence of one.
 */
export function renderStat(
  stat: MetricStat | undefined,
  { digits = 3, percent = false }: { digits?: number; percent?: boolean } = {},
): string {
  if (!stat || stat.n === 0) return "n/a";
  const scale = percent ? 100 : 1;
  const suffix = percent ? "%" : "";
  return `${(stat.mean * scale).toFixed(digits)}${suffix} +/- ${(stat.stdev * scale).toFixed(digits)}${suffix}`;
}

/** The mean alone, or `n/a` for an empty group. */
export function renderMean(
  stat: MetricStat | undefined,
  { digits = 3, percent = false }: { digits?: number; percent?: boolean } = {},
): string {
  if (!stat || stat.n === 0) return "n/a";
  const scale = percent ? 100 : 1;
  const suffix = percent ? "%" : "";
  return `${(stat.mean * scale).toFixed(digits)}${suffix}`;
}

/** The spread alone, or an empty string when there is nothing to show. */
export function renderSpread(
  stat: MetricStat | undefined,
  { digits = 3, percent = false }: { digits?: number; percent?: boolean } = {},
): string {
  if (!stat || stat.n === 0) return "";
  const scale = percent ? 100 : 1;
  const suffix = percent ? "%" : "";
  return `+/- ${(stat.stdev * scale).toFixed(digits)}${suffix}`;
}

/** A ratio in the range 0 to 1 as a percentage, or `n/a` for null. */
export function percent(value: number | null | undefined, digits = 1): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  return `${(value * 100).toFixed(digits)}%`;
}

/** A plain number, or `n/a` for null. */
export function ratio(value: number | null | undefined, digits = 3): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  return value.toFixed(digits);
}

/**
 * US dollars. Sub cent amounts keep four decimals, because a per step cost of 0.0004 is
 * a real number and rounding it to 0.00 hides the only signal in the column.
 */
export function usd(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  if (value === 0) return "$0.00";
  if (Math.abs(value) < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

/** Wall clock seconds, scaled to the unit a reader can hold in their head. */
export function seconds(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  if (value < 1) return `${(value * 1000).toFixed(0)} ms`;
  if (value < 90) return `${value.toFixed(value < 10 ? 2 : 1)} s`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  if (minutes < 60) return `${minutes}m ${rest}s`;
  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes % 60}m`;
}

/** Milliseconds, as recorded per step. */
export function millis(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  if (value < 1000) return `${value} ms`;
  return seconds(value / 1000);
}

/** An integer with thousands separators, using a fixed separator rather than a locale. */
export function count(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  return Math.round(value)
    .toString()
    .replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

function two(value: number): string {
  return value.toString().padStart(2, "0");
}

/** An ISO timestamp as `14 Sep 2026, 02:53 UTC`. Always UTC, never the reader's zone. */
export function timestamp(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "n/a";
  const month = MONTHS[date.getUTCMonth()] ?? "";
  return `${date.getUTCDate()} ${month} ${date.getUTCFullYear()}, ${two(date.getUTCHours())}:${two(date.getUTCMinutes())} UTC`;
}

/** An ISO timestamp as `02:53:15.159`, for ordering steps inside one run. */
export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const ms = date.getUTCMilliseconds().toString().padStart(3, "0");
  return `${two(date.getUTCHours())}:${two(date.getUTCMinutes())}:${two(date.getUTCSeconds())}.${ms}`;
}

/** Byte counts for output sizes. */
export function bytes(value: number): string {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

/** Split a model identifier into its provider prefix and the rest, for two line display. */
export function splitModel(model: string): { prefix: string | null; name: string } {
  const slash = model.indexOf("/");
  if (slash > 0) {
    return { prefix: model.slice(0, slash), name: model.slice(slash + 1) };
  }
  const colon = model.indexOf(":");
  if (colon > 0) {
    return { prefix: model.slice(0, colon), name: model.slice(colon + 1) };
  }
  return { prefix: null, name: model };
}

/** True for the scripted offline policies, which are not language models. */
export function isStubModel(model: string): boolean {
  return model.startsWith("stub:");
}
