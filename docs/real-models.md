# Running real models

The published matrix is five scripted offline policies. They validate the harness and give
the public replay real trajectories, and every run they produce carries a `stub:` model
identifier so nothing downstream can read them as model results. This document is about the
other path: driving the same twelve tasks with a real language model.

The design constraint was that it had to cost nothing. No card, no trial that lapses into a
bill. That rules out a dollar budget as the safety mechanism and changes what the controls
have to be.

## Free paths

Two work with no payment method.

**NVIDIA build.** An OpenAI-compatible endpoint at `https://integrate.api.nvidia.com/v1`
on a free developer tier with no card, rate limited rather than metered in dollars. Reached
through LiteLLM with the `nvidia_nim/`
prefix and a key in `NVIDIA_NIM_API_KEY`. This is the default for the hosted matrix.

**A local Ollama server.** Free and unlimited, reached through the same provider with the
`ollama_chat/` prefix and `OLLAMA_API_BASE`. Inference on a CPU runner is slow, so in CI
this path is a reachability check rather than a full matrix; pointed at a machine with a
GPU it runs the whole suite.

Both arrive through the one `LiteLLMProvider`, so the agent loop has no idea which vendor
it is talking to. The only vendor-specific detail is the base URL, and it stays in the
provider factory.

## The controls a free tier needs

A metered-by-the-request tier overruns on call count, not dollars, so the matrix is capped
by both:

- **A call ceiling** (`--max-calls`), shared across the whole run and reserved before each
  call under a lock, so parallel workers cannot overshoot it. A run that reaches the
  ceiling stops cleanly and reports `budget_exceeded`, the same finding a dollar ceiling
  produces, because an operator cap is one finding whatever its unit.
- **A shared rate limit** (`--rpm`), which spaces calls across every worker so a burst from
  the thread pool does not trip the tier's per-minute limit.
- **Seed-major ordering**, so a run the ceiling cuts off leaves whole seeds finished rather
  than most tasks at seed 0 and a scattering at seed 1. `pass@1` averaged over complete
  seeds is a figure you can report; an average over a ragged edge is not.

## One call before the many

The real matrix will not start until a one-call smoke has passed. `scripts/smoke_model.py`
makes a single call with the same provider the matrix uses and a hard wall-clock cap, so a
missing key, a mistyped model id, a retired model or a wrong base URL fails on call one
rather than on call four hundred of a long matrix. The smoke also offers
a tool and checks the model calls it, because the agent loop acts only through tools: a
model that chats well but cannot call a tool is useless to the run, and it is better to
learn that for free.

## Choosing a model

The model has to do three things: answer quickly, because the loop makes many calls per
task; call tools, because that is the only way the agent acts; and cost nothing. Reasoning
models spend their token budget on a hidden thinking channel before they write a visible
word, so the provider now captures that channel for the replay and the smoke reports its
length, which is how a model that "returned nothing" is correctly read as one that reasoned
past a small token cap rather than one that failed.

A slow reasoning model that does not advertise tool calling is the wrong tool here however
capable it is in the abstract, and the smoke is what tells the difference cheaply, before a
full matrix runs.

## A panel of models

One model on the leaderboard says how that model did. It cannot say whether the tasks tell
models apart, which is the question an evaluation has to answer before anyone trusts its
numbers. A panel answers it.

A panel is a committed request, [`model-requests/panel.json`](../model-requests/panel.json):
the models, the seeds, the suite, the temperature, the total request rate, and an optional
judge. Changing that file on `main` starts
[`model-panel.yml`](../.github/workflows/model-panel.yml), so every model figure the
repository publishes has a reviewable record of what was asked for, in the same history as
the code that produced it. `scripts/plan_panel.py` validates the request before anything is
spent, and refuses a scripted policy, an unknown task, a duplicate seed, or a rate above the
key's limit.

How the workflow spends a free key:

- **One call per model first.** Every model, and the judge, is probed with a single tool
  call. A model that fails is left out of the panel with its reason in the run summary, and
  the rest run regardless. A judge that fails its probe means the panel runs without one.
- **One job per model.** Each job runs its model on the Docker backend under its own call
  ceiling. The default ceiling is the most calls the requested runs could ever make, every
  run taken to its step ceiling, so no seed is cut part way through.
- **One rate limit, shared.** The free key is limited across every model it calls, so the
  request's total rate is divided evenly between the jobs that run at once. The ceiling and
  the limit count the agent's calls. The judge's calls, and the provider's own retries of a
  call that failed, fall outside both.
- **A slice when a run was cut.** An entry can name tasks and seeds, so a seed a ceiling cut
  short is completed on its own instead of repeating what already finished, or exact task
  and seed pairs, so a run the provider ended can be run once more and nothing else.
- **Records survive a failure.** Each run is sealed to disk the moment it ends, so a job
  that fails or runs out of time still delivers what it finished. Everything arrives on a
  branch named for the workflow run, and promoting it onto the leaderboard is a separate,
  reviewed commit.

Provider latency on a shared free tier counts against each task's wall clock ceiling, the
same as the model's own time. A slow hour on the free tier can show up as timeouts, so
compare models from the same panel rather than across days.

## When the provider ends a run

The first five model panel showed how much of a free tier's behaviour lands in the
results. Of 120 runs, the provider ended 48: a call failed with a timeout or a 429 through
every retry, or the provider refused it with HTTP 400, 13 of them before the first step. A
record like that measures the provider at the time, not the model, so the harness treats
it as its own ending:

- **Counted apart.** The model figures carry a table of how every run ended, and a run the
  provider ended has its own column, apart from the agent finishing, a ceiling, or another
  error. The test is one function beside the provider message it reads.
- **Rerun under a rule fixed in advance.** A panel can rerun exactly the runs the provider
  ended. At promotion each rerun replaces the run with the same model, task and seed, only a
  provider ended run can be replaced, and the rerun stands whatever it scores, so nothing is
  chosen by outcome. A rerun of a run the agent finished is refused.
- **Retries that fit the failure.** A 429 now backs off for about 10 seconds, then 20, then
  40, each with jitter, where it used to retry within a second or two and land in the same
  rate window, and a refusal with no reason in it, which the gpt-oss endpoint gave about one
  call in thirty, is retried as a fresh sample.

The panel's results, the rerun, and the runs the provider ended again are in
[MODEL-RESULTS.md](../MODEL-RESULTS.md).
