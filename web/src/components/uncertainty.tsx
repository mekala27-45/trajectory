import { splitModel } from "@/lib/format";
import type { ModelStatisticsEntry, PairComparison } from "@/lib/types";

/**
 * How sure a solve rate is.
 *
 * Each row draws the 95% interval over tasks as a band on a 0 to 100 track, with the
 * estimate as a dot inside it. Two bands that overlap a lot are two models these twelve
 * tasks cannot tell apart, which the head to head below says in numbers.
 */

function name(model: string): string {
  const { name: rest } = splitModel(model);
  return rest.split("/").pop() ?? rest;
}

function pct(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function IntervalChart({ entries }: { entries: ModelStatisticsEntry[] }) {
  const sorted = [...entries].sort(
    (a, b) => b.solve_rate.estimate - a.solve_rate.estimate || a.model.localeCompare(b.model),
  );
  const ks = [...new Set(sorted.flatMap((entry) => Object.keys(entry.pass_at).map(Number)))].sort(
    (a, b) => a - b,
  );
  return (
    <div className="rounded-lg border border-line bg-raised">
      <ul className="divide-y divide-[color:var(--line)]">
        {sorted.map((entry) => {
          const { estimate, low, high } = entry.solve_rate;
          return (
            <li
              key={entry.model}
              className="grid grid-cols-1 gap-x-4 gap-y-1.5 px-4 py-3 sm:grid-cols-[minmax(0,16rem)_minmax(0,1fr)_auto] sm:items-center"
            >
              <span className="min-w-0 truncate font-mono text-[12.5px]" title={entry.model}>
                {name(entry.model)}
              </span>
              <span
                className="relative block h-5"
                role="img"
                aria-label={`${name(entry.model)}: solve rate ${pct(estimate, 1)}, 95% interval over tasks ${pct(low, 1)} to ${pct(high, 1)}`}
              >
                <span className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-line-strong" />
                {[0.25, 0.5, 0.75].map((tick) => (
                  <span
                    key={tick}
                    aria-hidden="true"
                    className="absolute top-1/2 h-2 w-px -translate-y-1/2 bg-line-strong"
                    style={{ left: `${tick * 100}%` }}
                  />
                ))}
                <span
                  className="absolute top-1/2 h-2.5 -translate-y-1/2 rounded-full bg-accent/35"
                  style={{
                    left: `${low * 100}%`,
                    width: `max(4px, ${(high - low) * 100}%)`,
                  }}
                />
                <span
                  className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-raised bg-accent"
                  style={{ left: `${estimate * 100}%` }}
                />
              </span>
              <span className="tnum font-mono text-[11.5px] whitespace-nowrap text-muted">
                <span className="text-fg">{pct(estimate)}</span> [{pct(low)}, {pct(high)}]
                {ks.map((k) =>
                  entry.pass_at[String(k)] == null ? null : (
                    <span key={k} className="ml-3 text-dim">
                      pass@{k} {pct(entry.pass_at[String(k)]!)}
                    </span>
                  ),
                )}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="border-t border-line px-4 py-2 font-mono text-[10.5px] text-dim">
        0% to 100% solve rate. Band: 95% interval over tasks, then seeds. Dot: the estimate.
      </p>
    </div>
  );
}

export function HeadToHead({ pairs }: { pairs: PairComparison[] }) {
  const sorted = [...pairs].sort(
    (a, b) => Math.abs(b.difference.estimate) - Math.abs(a.difference.estimate),
  );
  return (
    <div className="scroll-x rounded-lg border border-line bg-raised">
      <table className="w-full min-w-[560px] text-[12.5px]">
        <caption className="sr-only">Every pair of models, compared on shared tasks</caption>
        <thead>
          <tr className="border-b border-line-strong text-left font-mono text-[10.5px] tracking-wide text-dim uppercase">
            <th scope="col" className="px-4 py-2 font-normal">
              Pair
            </th>
            <th scope="col" className="px-4 py-2 text-right font-normal">
              Difference, 95% interval
            </th>
            <th scope="col" className="px-4 py-2 text-right font-normal">
              First ahead
            </th>
            <th scope="col" className="px-4 py-2 font-normal">
              Verdict
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[color:var(--line)]">
          {sorted.map((pair) => {
            const { estimate, low, high } = pair.difference;
            const separated = low > 0 || high < 0;
            const leader = estimate >= 0 ? pair.first : pair.second;
            return (
              <tr key={`${pair.first}|${pair.second}`}>
                <td className="px-4 py-2 font-mono">
                  {name(pair.first)} <span className="text-dim">vs</span> {name(pair.second)}
                </td>
                <td className="tnum px-4 py-2 text-right font-mono whitespace-nowrap">
                  {(estimate * 100).toFixed(1)} pts{" "}
                  <span className="text-dim">
                    [{(low * 100).toFixed(1)}, {(high * 100).toFixed(1)}]
                  </span>
                </td>
                <td className="tnum px-4 py-2 text-right font-mono">
                  {(pair.first_ahead * 100).toFixed(0)}%
                </td>
                <td className="px-4 py-2 text-muted">
                  {separated ? (
                    <span>
                      <span className="text-fg">{name(leader)}</span> ahead on these tasks
                    </span>
                  ) : (
                    "not separated by these tasks"
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
