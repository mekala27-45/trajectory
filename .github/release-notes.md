## trajectory v0.3.0

A panel of real models instead of one, a grader that checks the report it is given, and the
first measurement of how strong the hidden tests are as a reward. Every model figure in this
release comes from one harness version, on the Docker backend, from committed requests.

### A panel of real models

`model-requests/panel.json` names the models, seeds, caps and judge, and changing it on
`main` starts `model-panel.yml`: one probe call per model, then one job per model that
answered, each under its own call ceiling and an even share of the free key's rate limit.
A model that fails its probe is left out with the reason, and a panel's records reach a
branch named for its run before a reviewed commit promotes them. `model-probe.yml` probes
candidates without running a task, and every probe result is raised as an annotation, so it
can be read without the logs.

The first panel: five free hosted models from four publishers, all twelve tasks at seeds 0
and 1. With the runs the provider ended run once more, `nemotron-3-ultra` solved 21 of 24,
`minimax-m3.1-flash` 16, `nemotron-3.5-lightning` 12, `gpt-oss-20b` 11 and `glm-5.3` 10. The
leader is separated from the bottom three by intervals that sit entirely above zero; most
other pairs are not separated by twelve tasks, and `MODEL-RESULTS.md` says which. No model
solved `sql-migration-01`, and five of its ten runs ended below the score the untouched
workspace already has.

### When the provider ends a run

The free tier ended 48 of the panel's first 120 runs: a timeout or a 429 through every
retry, or an HTTP 400. Those records measure the provider, not the model, so the model
figures now count them in a column of their own, and a panel can rerun exactly those runs.
At promotion each rerun replaces the run with the same model, task and seed under a rule
fixed before any rerun was read: only a run the provider ended can be replaced, and the
rerun stands whatever it scores. The provider ended 23 reruns too; they stand, and the
provenance file lists every replacement. A 429 now backs off for about 10 seconds, then 20,
then 40, instead of retrying inside the same rate window, and a refusal with no reason in
it, which `gpt-oss-20b`'s endpoint gave about one call in thirty, is retried.

### A bug in the harness, found by the first real run

Files the agent wrote with `write_file` were unpacked as root inside the container. The
agent could replace them but not change them in place: `go fmt`, an append, `chmod`, or a
script rewriting the file all failed with permission denied. On go-race-01 the first real
model lost steps to exactly this on both seeds. Files are now written as the agent user, a
container test edits them in place, and the panels that were running on the old code were
cancelled so no published figure rests on it.

### The grader checks the report

Verification no longer happens in the agent's container. When the agent stops, its
processes are stopped and its workspace alone is copied into a fresh container from the same
clean image. Nothing it left running, and nothing it wrote outside the workspace, is present
when the hidden tests run.

A test written fresh for every run, with a random name and an ordinary body, is planted
among the hidden tests and fails for any correct runner. A report that names it failing is
believed and leaves it out of the counts. A report that claims success with it in the set is
not: nothing counts as passed, the record is marked `tampered`, and F11 fires at full
confidence. pytest, `go test` and the Node runner all carry one, so every task does. It is a
tripwire for a report bent as a whole, not for one bent test by test, and the grader audit
says so.

### How strong the hidden tests are

`trajectory tasks mutate` replays each reference with one small change in a file it writes,
the slip a model makes, and runs the hidden tests on the result. On Docker the hidden tests
caught 75 of 94 near misses across eleven tasks; the twelfth repairs its repository with git
commands and has nothing to mutate. Each of the 19 survivors is listed by file, line and
change in `docs/verifier-strength.md` and read by hand: 6 are equivalent to the original, 5
pass only because of the task's data, and 8 are real gaps: an empty input, the two ends of
a range, and properties no test uses, such as value equality and a frozen type. Leaving out
the equivalent ones, the tests catch 75 of 88.

A kill has to happen twice. The first local run counted one catch that a container run did
not. The mutant is equivalent on the task's data, so a test failed for another reason, most
likely the suite's time budget on a loaded machine. Every kill is
now replayed once more, and one that does not happen again is scored as unstable, not
caught. The figures are held to their report in CI and reproduced on Docker by
`mutation.yml` whenever a task, the mutation code, the verifier or the planted test changes.

### The judge, read twice

Three failure modes and the drift metric come from a model reading the trajectory, so this
release checks the reader. `step-5-preview` sent each mode's verdict as JSON text inside its
tool call, which the schema refused, so its first pass sealed every unsolved run with no
verdict. The reply is now read as the object it encodes, and the 22 first pass runs still
without one were read again, and only those, by `rejudge.yml`, under a promotion that checks
nothing but the judge's output changed. All 50 unsolved runs carry a verdict.

Then `judge-agreement.yml` had a second judge, from a family that is neither the first
judge's nor any agent's, read the same runs. `deepseek-v4.1-flash` returned a verdict on 49
of the 50. The two agree on most runs, because most runs have none of the judged modes, and
mostly disagree on which runs have one: Cohen's kappa 0.26 for ignored test output and 0.08
for context loss. `MODEL-RESULTS.md` reads the judged counts as one model's reading for that
reason, and the request, the provider's timeouts on the first attempt and the run left out
are all on record.

### How sure a number is

`trajectory report` adds a 95% two stage bootstrap over tasks and then seeds for every
model, unbiased pass@k, and every pair of models head to head on shared tasks. The seed
spread says whether a re-run would move a number; this says whether it would hold on tasks
the suite does not have. `MODEL-RESULTS.md` reads the RL signal per model and works one GRPO
group through to its advantages.

### Provenance

Every run record now carries the commit the harness ran from, and the run page links it.
