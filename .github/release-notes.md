## trajectory v0.3.0

A panel of real models instead of one, a grader that checks the report it is given, and the
first measurement of how strong the hidden tests are as a reward. Every model figure in this
release comes from one harness version, on the Docker backend, from a committed request.

### A panel of real models

`model-requests/panel.json` names the models, seeds, caps and judge, and changing it on
`main` starts `model-panel.yml`: one probe call per model, then one job per model that
answered, each under its own call ceiling and an even share of the free key's rate limit.
A model that fails its probe is left out with the reason, and a panel's records reach a
branch named for its run before a reviewed commit promotes them. `model-probe.yml` probes
candidates without running a task, and every probe result is raised as an annotation, so it
can be read without the logs.

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
the slip a model makes, and runs the hidden tests on the result. A survivor is a near miss
the reward would pay in full, and every one is listed by file, line and change in
`docs/verifier-strength.md`, held to its report in CI and reproduced on Docker by
`mutation.yml` whenever a task or the verifier changes.

### How sure a number is

`trajectory report` adds a 95% two stage bootstrap over tasks and then seeds for every
model, unbiased pass@k, and every pair of models head to head on shared tasks. The seed
spread says whether a re-run would move a number; this says whether it would hold on tasks
the suite does not have. `MODEL-RESULTS.md` reads the RL signal per model and works one GRPO
group through to its advantages.

### Provenance

Every run record now carries the commit the harness ran from, and the run page links it.
