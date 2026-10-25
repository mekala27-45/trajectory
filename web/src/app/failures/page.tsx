import { FailureCharts } from "@/components/failure-charts";
import { Chip, Field, Panel, PlainNote } from "@/components/ui";
import { getFailures, getIndex, getJudgeAgreement, getTasks, listRuns } from "@/lib/api";
import { count, percent } from "@/lib/format";
import { failureColor } from "@/lib/colors";
import type { ModeAgreementEntry } from "@/lib/types";

/** The model's own name, without the provider and publisher in front of it. */
function shortModel(model: string): string {
  return model.split("/").pop() ?? model;
}

/** One line on how far a second reader agreed about a judged mode. */
function agreementLine(second: string, entry: ModeAgreementEntry): string {
  const total = entry.both_present + entry.first_only + entry.second_only + entry.neither_present;
  const agreed = entry.both_present + entry.neither_present;
  const kappa =
    entry.cohens_kappa == null
      ? "kappa not computable, since neither judge found it"
      : `kappa ${entry.cohens_kappa.toFixed(2)}`;
  return `A second judge, ${shortModel(second)}, agreed on ${agreed} of ${total} runs; ${kappa}.`;
}

export const metadata = {
  title: "Failure modes",
  description:
    "The failure modes the harness classifies, how each one is detected, and how often each fired, by model and by task difficulty.",
};

export default async function FailuresPage() {
  const [breakdown, runs, tasks, index, agreement] = await Promise.all([
    getFailures(),
    listRuns(),
    getTasks(),
    getIndex(),
    getJudgeAgreement(),
  ]);

  const difficultyByTask = Object.fromEntries(tasks.map((task) => [task.id, task.difficulty]));
  const titleByTask = Object.fromEntries(tasks.map((task) => [task.id, task.title]));
  const ruleCount = breakdown.taxonomy.filter((spec) => spec.detection === "rule").length;
  const modeCount = breakdown.taxonomy.length;

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h1 className="text-[22px] font-semibold sm:text-[26px]">Failure modes</h1>
        <p className="max-w-[80ch] text-[14px] text-muted">
          A pass rate says a run failed. It does not say how. Every run is classified against this
          taxonomy: {ruleCount} of the {modeCount} modes by deterministic rules over the trajectory,
          on every run, and the rest by a rubric judge that reads the runs that failed. Each hit
          carries the evidence a human can check it against. Two models with the same solve rate
          usually fail for different reasons, and the reason is what a model team can act on. Runs
          that passed are classified by the rules too, which is the view no pass rate can produce: a
          run that made malformed tool calls, invented paths and got the hidden tests green anyway
          is a process problem that happened to succeed.
        </p>
      </section>

      <PlainNote>
        A failure mode is a named, specific way a program can go wrong: quitting early, repeating
        the same command, running something destructive, and so on. There are eleven. This page
        counts how often each one happened, and it counts the rule based ones even on runs that
        passed, because a program can reach the right answer in a careless way.
      </PlainNote>

      <Panel className="px-4 py-3">
        <h2 className="sr-only">Denominator</h2>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <Field label="Runs">{count(index.run_count)}</Field>
          <Field label="Solved">{count(index.solved_count)}</Field>
          <Field
            label="Unsolved"
            title="The denominator behind the shares in the taxonomy table. A mode firing on 19 percent of failed runs is an actionable number; the same count as a share of every run buries it under the tasks that went fine. The charts below count every run, because three of these policies solve everything and a chart scoped to failures would be empty for them."
          >
            {count(breakdown.unsolved_runs)}
          </Field>
          <Field label="Judge">{index.judge_model ?? "did not run"}</Field>
          {agreement ? (
            <Field
              label="Second judge"
              title="A model from another family read a fixed sample of the runs the first judge read. Agreement for each judged mode is on its card below."
            >
              {shortModel(agreement.second_judge)}, {agreement.compared_runs} runs
            </Field>
          ) : null}
        </dl>
      </Panel>

      <section aria-labelledby="distribution">
        <h2 id="distribution" className="mb-3 text-[16px] font-semibold">
          Distribution
        </h2>
        <FailureCharts
          breakdown={breakdown}
          runs={runs}
          difficultyByTask={difficultyByTask}
          titleByTask={titleByTask}
        />
      </section>

      <section aria-labelledby="taxonomy">
        <h2 id="taxonomy" className="mb-1 text-[16px] font-semibold">
          The taxonomy
        </h2>
        <p className="mb-3 max-w-[80ch] text-[13px] text-muted">
          {modeCount} modes. A rule based hit is deterministic and reproducible from the trajectory
          alone. A judge hit is a model reading the trajectory against a rubric, which is the only
          way to reach the modes that need reading comprehension, and it is labelled as such
          everywhere it appears.
        </p>
        <ul className="grid gap-3 lg:grid-cols-2">
          {breakdown.taxonomy.map((spec) => {
            const overall = breakdown.overall.find((entry) => entry.id === spec.id);
            return (
              <li
                key={spec.id}
                className="rounded-lg border border-line border-l-4 bg-raised px-4 py-3"
                style={{ borderLeftColor: failureColor(spec.id) }}
              >
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <h3 className="flex items-center gap-1.5 font-mono text-[13px]">
                    <span
                      aria-hidden="true"
                      className="h-2.5 w-2.5 rounded-[2px]"
                      style={{ background: failureColor(spec.id) }}
                    />
                    {spec.id} {spec.name}
                  </h3>
                  <Chip
                    tone={spec.detection === "rule" ? "accent" : "warn"}
                    title={
                      spec.detection === "rule"
                        ? "Found by a deterministic rule over the trajectory."
                        : "Found by the rubric judge reading the trajectory."
                    }
                  >
                    {spec.detection}
                  </Chip>
                  <span className="tnum ml-auto font-mono text-[11px] text-dim">
                    {overall
                      ? `${overall.count} ${overall.count === 1 ? "run" : "runs"}, ${percent(overall.share_of_failed_runs, 0)} of unsolved`
                      : "0 runs"}
                  </span>
                </div>
                <p className="mt-1.5 max-w-[70ch] text-[13px] text-fg">{spec.definition}</p>
                <p className="mt-1.5 font-mono text-[10px] tracking-wide text-dim uppercase">
                  Example
                </p>
                <p className="max-w-[70ch] text-[12.5px] text-muted">{spec.example}</p>
                {agreement && agreement.per_mode[spec.id] ? (
                  <p className="mt-2 max-w-[70ch] border-t border-line pt-2 text-[12.5px] text-muted">
                    {agreementLine(agreement.second_judge, agreement.per_mode[spec.id]!)}
                  </p>
                ) : null}
              </li>
            );
          })}
        </ul>
      </section>
    </div>
  );
}
