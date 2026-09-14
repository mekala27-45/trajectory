"use client";

import { useState } from "react";

import { CodePane } from "./code-pane";
import { Chip, FailureChip } from "./ui";
import { failureColor } from "@/lib/colors";
import { clockTime, millis, usd } from "@/lib/format";
import { stringifyArgs } from "@/lib/highlight";
import type { FailureModeHit, FailureModeSpec, Step } from "@/lib/types";

/** A string argument long enough to deserve its own block rather than a JSON line. */
function isLongString(value: unknown): value is string {
  return typeof value === "string" && (value.includes("\n") || value.length > 120);
}

/**
 * Tool arguments, as the model supplied them.
 *
 * Three shapes have to render. Normal arguments are JSON. A bash command and any other
 * long string get their own block, because a shell one liner escaped into a JSON string
 * is unreadable and a written file's contents are worse. And when the model emitted
 * something that was not parseable JSON at all, the harness keeps the raw text under
 * `__raw__`: that is the most interesting case on the page and it is labelled rather than
 * silently rendered as an object.
 */
function ToolArgs({ step }: { step: Step }) {
  const args = step.tool_args ?? {};
  const raw = args["__raw__"];

  if (typeof raw === "string") {
    const others = Object.entries(args).filter(([key]) => key !== "__raw__");
    return (
      <>
        <CodePane
          label="arguments the model emitted"
          kind="text"
          text={raw}
          defaultWrap
          meta={
            <Chip
              tone="bad"
              title="The arguments were not parseable JSON, so they were kept verbatim."
            >
              unparsed
            </Chip>
          }
        />
        {others.length > 0 ? (
          <CodePane
            label="other arguments"
            kind="json"
            text={stringifyArgs(Object.fromEntries(others))}
          />
        ) : null}
      </>
    );
  }

  const entries = Object.entries(args);
  const longKeys = entries
    .filter(
      ([key, value]) => (step.tool_name === "bash" && key === "command") || isLongString(value),
    )
    .map(([key]) => key);
  const short = entries.filter(([key]) => !longKeys.includes(key));

  return (
    <>
      {longKeys.map((key) => (
        <CodePane
          key={key}
          label={step.tool_name === "bash" && key === "command" ? "bash command" : key}
          kind={key === "command" ? "shell" : "text"}
          text={String(args[key] ?? "")}
          defaultWrap={key === "command"}
          collapsedLines={key === "command" ? 8 : 18}
        />
      ))}
      {short.length > 0 ? (
        <CodePane
          label="arguments"
          kind="json"
          text={stringifyArgs(Object.fromEntries(short))}
          collapsedLines={12}
        />
      ) : null}
      {entries.length === 0 ? (
        <p className="mt-2 font-mono text-[11px] text-dim">no arguments</p>
      ) : null}
    </>
  );
}

function ExitCodeChip({ code }: { code: number | null }) {
  if (code == null) {
    return (
      <Chip title="This tool does not run a process, so there is no exit code.">no exit code</Chip>
    );
  }
  return (
    <Chip tone={code === 0 ? "ok" : "bad"} title="Exit code of the command.">
      exit {code}
    </Chip>
  );
}

export function StepCard({
  step,
  hits,
  specs,
  focused,
  onActivate,
  registerRef,
  total,
}: {
  step: Step;
  /** The failure mode hits whose step_indices include this step. */
  hits: FailureModeHit[];
  specs: Record<string, FailureModeSpec | undefined>;
  focused: boolean;
  onActivate: (index: number) => void;
  registerRef: (index: number, element: HTMLElement | null) => void;
  total: number;
}) {
  const [openMode, setOpenMode] = useState<string | null>(null);
  const primary = hits[0];
  const failed = step.exit_code != null && step.exit_code !== 0;

  const label = [
    `Step ${step.index + 1} of ${total}`,
    step.tool_name,
    step.exit_code == null ? null : `exit code ${step.exit_code}`,
    hits.length > 0 ? `flagged ${hits.map((hit) => hit.id).join(", ")}` : null,
  ]
    .filter(Boolean)
    .join(", ");

  return (
    <li className="relative">
      <article
        ref={(element) => registerRef(step.index, element)}
        data-step-index={step.index}
        data-focused={focused ? "true" : undefined}
        data-flagged={hits.length > 0 ? "true" : undefined}
        tabIndex={0}
        aria-label={label}
        onFocus={() => onActivate(step.index)}
        className={`rounded-lg border border-line border-l-4 bg-raised px-3 py-2.5 sm:px-4 ${
          focused ? "step-focused" : ""
        }`}
        style={{
          borderLeftColor: primary
            ? failureColor(primary.id)
            : failed
              ? "var(--warn)"
              : "var(--line-strong)",
        }}
      >
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <span className="tnum shrink-0 rounded bg-sunken px-1.5 py-0.5 font-mono text-[11px] text-muted">
            {step.index}
          </span>
          <span className="font-mono text-[12.5px] font-medium">{step.tool_name}</span>
          <ExitCodeChip code={step.exit_code} />
          {step.schema_violation ? (
            <Chip
              tone="bad"
              title="The model called a tool that does not exist, or sent arguments that failed validation. The step is recorded and the loop continues, because the rate of these is one of the reported metrics."
            >
              schema violation
            </Chip>
          ) : null}
          {step.truncated ? (
            <Chip tone="warn" title="The output was cut at the per command output cap.">
              output truncated
            </Chip>
          ) : null}
          {step.error ? (
            <Chip tone="bad" title="The harness could not run the tool call.">
              harness error
            </Chip>
          ) : null}
          <span className="tnum ml-auto flex shrink-0 flex-wrap items-center gap-x-2.5 font-mono text-[10.5px] text-dim">
            <span title="Wall clock for the tool call itself.">{millis(step.duration_ms)}</span>
            <span title="Provider cost attributed to this step.">{usd(step.cost_usd)}</span>
            <span
              className="hidden sm:inline"
              title="Prompt and completion tokens billed for this step."
            >
              {step.tokens_in}/{step.tokens_out} tok
            </span>
            <span className="hidden sm:inline">{clockTime(step.timestamp)}</span>
          </span>
        </div>

        {hits.length > 0 ? (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {hits.map((hit) => (
              <FailureChip
                key={`${hit.id}-${hit.detector}`}
                id={hit.id}
                name={hit.name}
                detector={hit.detector}
                confidence={hit.confidence}
                active={openMode === hit.id}
                expanded={openMode === hit.id}
                onClick={() => setOpenMode(openMode === hit.id ? null : hit.id)}
                title="Show the evidence behind this hit."
              />
            ))}
          </div>
        ) : null}

        {openMode ? (
          <FailureEvidence
            hit={hits.find((hit) => hit.id === openMode) ?? null}
            spec={specs[openMode]}
          />
        ) : null}

        {step.thought ? (
          <p className="mt-2.5 max-w-[95ch] border-l-2 border-line pl-2.5 text-[13px] leading-relaxed text-fg/90">
            {step.thought}
          </p>
        ) : (
          <p className="mt-2.5 font-mono text-[11px] text-dim">
            no assistant text accompanied this call
          </p>
        )}

        <ToolArgs step={step} />

        <CodePane
          label="output"
          kind="output"
          text={step.tool_output}
          collapsedLines={16}
          dataRole="output"
          emptyLabel={failed ? "no output, and the command exited non-zero" : "no output"}
          meta={
            step.truncated ? (
              <Chip tone="warn" title="Output past the cap was cut by the harness, not here.">
                capped by the harness
              </Chip>
            ) : undefined
          }
        />

        {step.error ? (
          <p className="mt-2 rounded-md border border-bad/40 bg-bad-bg px-2.5 py-1.5 font-mono text-[11.5px] text-bad">
            {step.error}
          </p>
        ) : null}
      </article>
    </li>
  );
}

function FailureEvidence({
  hit,
  spec,
}: {
  hit: FailureModeHit | null;
  spec: FailureModeSpec | undefined;
}) {
  if (!hit) return null;
  return (
    <div
      className="mt-2 rounded-md border bg-sunken px-2.5 py-2"
      style={{ borderColor: failureColor(hit.id) }}
    >
      <p className="font-mono text-[11px] text-muted">
        {hit.id} {hit.name}, found by {hit.detector === "rule" ? "a rule" : "the rubric judge"},
        confidence {hit.confidence.toFixed(2)}
      </p>
      {spec ? <p className="mt-1 text-[12.5px] text-muted">{spec.definition}</p> : null}
      <p className="mt-1.5 text-[13px] whitespace-pre-line text-fg">{hit.evidence}</p>
    </div>
  );
}
