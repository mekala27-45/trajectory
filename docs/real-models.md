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
with a monthly credit allowance and no card. Reached through LiteLLM with the `nvidia_nim/`
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
rather than on call four hundred of a tier that refills once a month. The smoke also offers
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
capable it is in the abstract, and the smoke is what tells the difference cheaply, before
the credits are spent.
