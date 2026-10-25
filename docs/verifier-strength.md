# Verifier strength: how much of a near miss the hidden tests catch

A task's hidden tests are its reward function. Under reinforcement learning a policy is
pushed toward whatever the reward pays for, so the question that matters about a verifier
is not whether the reference solution passes it. Every task here already proves that in CI.
The question is whether a solution that is almost right passes it too. A suite that cannot
tell the reference from a near miss pays the near miss in full, and a policy trained on it
learns the near miss.

This page measures that directly, with mutation testing.

## How it is measured

`trajectory tasks mutate` takes each task's reference playbook, the expert trajectory that
solves it, and replays it with one small change in one of the files it writes: a comparison
off by one, a sign flipped, a condition inverted, a constant nudged, a version pin turned
around. Those are the classic mutation operators, and they are also the slips a model
makes. The hidden tests then run on the result, through the same verifier every agent run
gets, planted test and fresh container included.

- **Killed.** The hidden tests failed the mutant. Counted separately when the mutant broke
  the build or crashed the suite, because catching a crash says less about a suite than
  catching a wrong answer.
- **Survived.** The hidden tests passed the mutant. Every survivor is listed by file, line
  and change, so a reader can judge it.
- **The score** is killed over scored.

Four rules keep the number honest.

- The change goes in where the reference writes the file, before the rest of the playbook
  runs, so a build step that follows compiles the mutant and not the original.
- Each task is first replayed with no change at all. A task whose unchanged replay does not
  pass would make every mutant look killed, so it is reported and not scored.
- Only source files the reference writes are mutated, never tests, fixtures or data, and a
  reference that edits a file in place with `sed` has its replacement text mutated.
- The sample is fixed per task: the same mutants on every machine, so two runs compare.

A survivor is not automatically a weak test. Some mutants are equivalent to the original,
and no test could tell them apart; a flag that only changes a log message, or a bound that
no input ever reaches, is one. The survivors are listed so that judgement can be made per
mutant rather than hidden in a percentage.

## Measured

<!-- measured: rendered from fixtures/mutation/core-12.json by scripts/check_mutation_numbers.py -->

<!-- end measured -->

## What it does not measure

Mutation testing asks whether the tests notice a small wrong change. It does not ask
whether they notice a large one, such as a solution that special cases the test inputs, or
one that passes by changing what the grader sees; those are what the
[grader audit](grader-audit.md) is about. Two tasks have no mutable source at all, because
their reference fixes the task with commands rather than by writing a file, and they are
listed as such rather than scored.
