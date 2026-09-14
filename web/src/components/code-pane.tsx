"use client";

import { useMemo, useState } from "react";

import {
  HIGHLIGHT_LIMIT,
  OUTPUT_DISPLAY_CAP,
  countLines,
  headLines,
  tokenizeJson,
  type Token,
} from "@/lib/highlight";
import { bytes } from "@/lib/format";

/**
 * A code or output block that cannot take the page down with it.
 *
 * Three rules. Text over the display cap is cut before it ever reaches the DOM, and the
 * block says so. Anything longer than the collapsed height renders as its first lines
 * until the reader asks for the rest. And the text is one text node rather than one
 * element per line, so a forty thousand line log dump is a single string for the browser
 * to lay out instead of forty thousand nodes to style.
 */

export type PaneKind = "json" | "shell" | "output" | "text";

const KIND_CLASS: Record<PaneKind, string> = {
  json: "bg-sunken",
  shell: "bg-sunken",
  output: "bg-sunken",
  text: "bg-sunken",
};

function Highlighted({ text }: { text: string }) {
  const tokens = useMemo<Token[]>(() => tokenizeJson(text), [text]);
  return (
    <>
      {tokens.map((token, index) =>
        token.cls ? (
          <span key={index} className={token.cls}>
            {token.text}
          </span>
        ) : (
          token.text
        ),
      )}
    </>
  );
}

export function CodePane({
  text,
  kind = "text",
  label,
  collapsedLines = 14,
  meta,
  defaultWrap = false,
  emptyLabel = "no output",
  cut = false,
  dataRole,
}: {
  text: string;
  kind?: PaneKind;
  /** Rendered above the block. Keep it to one or two words. */
  label?: string;
  collapsedLines?: number;
  /** Extra chips for the header row, for example an exit code. */
  meta?: React.ReactNode;
  defaultWrap?: boolean;
  emptyLabel?: string;
  /** True when the server already cut this text at the display cap. */
  cut?: boolean;
  /** Tags the expand control so a keyboard shortcut elsewhere can reach it. */
  dataRole?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [wrap, setWrap] = useState(defaultWrap);

  const { display, total, cappedAt } = useMemo(() => {
    const capped = text.length > OUTPUT_DISPLAY_CAP;
    const body = capped ? text.slice(0, OUTPUT_DISPLAY_CAP) : text;
    return { display: body, total: countLines(body), cappedAt: capped };
  }, [text]);

  const collapsible = total > collapsedLines + 4;
  const body = useMemo(
    () => (collapsible && !expanded ? headLines(display, collapsedLines) : display),
    [collapsible, expanded, display, collapsedLines],
  );

  if (text.length === 0) {
    return (
      <div className="mt-2">
        {label ? <PaneLabel label={label} meta={meta} /> : null}
        <p className="rounded-md border border-line bg-sunken px-2.5 py-1.5 font-mono text-[11.5px] text-dim">
          {emptyLabel}
        </p>
      </div>
    );
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {label ? <PaneLabel label={label} meta={meta} /> : null}
        <span className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setWrap((value) => !value)}
            aria-pressed={wrap}
            className="rounded border border-line bg-raised px-1.5 py-0.5 font-mono text-[10.5px] text-muted hover:bg-hover hover:text-fg"
          >
            {wrap ? "no wrap" : "wrap"}
          </button>
          {collapsible ? (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              aria-expanded={expanded}
              data-role={dataRole ? `expand-${dataRole}` : undefined}
              className="rounded border border-accent-line bg-accent-bg px-1.5 py-0.5 font-mono text-[10.5px] text-accent hover:brightness-110"
            >
              {expanded ? "collapse" : `expand ${total} lines`}
            </button>
          ) : null}
        </span>
      </div>

      <div
        className={`scroll-x scroll-y mt-1 rounded-md border border-line ${KIND_CLASS[kind]} ${
          expanded ? "max-h-[70vh] overflow-y-auto" : ""
        }`}
      >
        <pre
          className={`m-0 px-2.5 py-2 font-mono text-[11.5px] leading-[1.5] ${
            wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"
          }`}
        >
          {kind === "json" && display.length <= HIGHLIGHT_LIMIT ? (
            <code>
              <Highlighted text={body} />
            </code>
          ) : (
            <code>{body}</code>
          )}
        </pre>
      </div>

      {/* A one line block needs no footer: the block is the whole of it. */}
      {collapsible || cappedAt || cut || total > 1 ? (
        <p className="mt-1 flex flex-wrap gap-x-3 font-mono text-[10.5px] text-dim">
          {collapsible && !expanded ? (
            <span>
              showing {collapsedLines} of {total} lines
            </span>
          ) : (
            <span>
              {total} {total === 1 ? "line" : "lines"}, {bytes(text.length)}
            </span>
          )}
          {cappedAt || cut ? (
            <span className="text-warn">
              cut at {bytes(OUTPUT_DISPLAY_CAP)} for display, the run record holds the rest
            </span>
          ) : null}
        </p>
      ) : null}
    </div>
  );
}

function PaneLabel({ label, meta }: { label: string; meta?: React.ReactNode }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="font-mono text-[10px] tracking-wide text-dim uppercase">{label}</span>
      {meta}
    </span>
  );
}
