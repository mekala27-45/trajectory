/**
 * The categorical palette for failure mode charts.
 *
 * Ten hues, fixed per taxonomy identifier so a mode keeps its colour across both charts
 * and the run list. Mid tone on purpose: each one clears 3:1 against the dark page
 * background and against the light one, which is what a chart fill needs to be a
 * distinguishable graphical object rather than decoration. Colour is never the only
 * channel: every segment is also labelled, listed in a legend, and repeated in a table
 * underneath the chart.
 */

import type { FailureModeId } from "./types";

export const FAILURE_COLORS: Record<FailureModeId, string> = {
  F01: "#4c8eda",
  F02: "#e0803c",
  F03: "#d2536a",
  F04: "#3fa66b",
  F05: "#9b6bd6",
  F06: "#29a3a3",
  F07: "#c9a227",
  F08: "#b5603c",
  F09: "#d46fb5",
  F10: "#7c8896",
};

/** Fallback for an identifier a newer harness added that this build does not know. */
export const UNKNOWN_COLOR = "#7c8896";

export function failureColor(id: string): string {
  return FAILURE_COLORS[id as FailureModeId] ?? UNKNOWN_COLOR;
}

/** Every taxonomy identifier, in order, so a chart stacks the same way every render. */
export const FAILURE_IDS: readonly FailureModeId[] = [
  "F01",
  "F02",
  "F03",
  "F04",
  "F05",
  "F06",
  "F07",
  "F08",
  "F09",
  "F10",
];

/**
 * The mode colour as a wash, for a chip background.
 *
 * The hue identifies the mode and the text stays at the page foreground colour. Coloured
 * 11px text over a panel does not clear 4.5:1 in both themes, and a chip that is only
 * legible in the dark one is not a chip.
 */
export function failureTint(id: string, percent = 12): string {
  return `color-mix(in srgb, ${failureColor(id)} ${percent}%, transparent)`;
}
