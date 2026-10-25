# Understanding trajectory

A plain-language guide to this project, for anyone who is new to it.

This page assumes you know roughly what an AI model is. It assumes nothing else. By the end
you will understand what this project is, what problem it solves, how it works, and what you
are looking at on every page of the website. Read it like a short lesson. Each section builds
on the one before it.

If you are an engineer who wants the precise, technical version, read the
[README](../README.md), [docs/metrics.md](metrics.md) and [RESULTS.md](../RESULTS.md) instead.
This guide is the gentle on-ramp, not the specification.

---

## 1. The short version

An AI coding agent is a program that fixes or writes code on its own. This project,
**trajectory**, is a way to test how good a coding agent really is.

Most tests ask one question: did the agent fix the bug, yes or no. That single answer hides a
lot. trajectory watches the whole attempt, step by step, and measures how the agent worked,
not only whether it got there. That is the whole idea, and the name says it: a trajectory is
the path something takes, not just where it ends up.

---

## 2. What is a coding agent?

Start with a plain AI model, the kind that answers questions. On its own it only produces
text. It cannot open a file, run a program, or see whether its idea actually worked.

A coding **agent** is that model wired up so it can act. It is given tools: read a file, run a
command, edit code, run the tests. It works in a loop. It looks at the problem, takes an
action, sees what happened, and decides what to do next, over and over, until it thinks the
job is done.

So a coding agent is a model that does not just talk about fixing a bug. It opens the broken
project, pokes around, makes changes, runs things, and keeps going until it believes the code
works. The question this project answers is: when it does that, how well does it do it.

---

## 3. The problem: a pass rate does not tell you much

The usual way to score a coding agent is the **pass rate**. You give it a hundred broken
programs, and you count how many it fixed. If it fixed forty-one, the pass rate is 41 percent.

That number is real, but it is thin. Imagine two students who both score 41 on the same exam.
One worked carefully, showed clear reasoning, and ran out of time. The other guessed, copied,
and got lucky on a few questions. Same score, completely different students. The score alone
cannot tell them apart.

Coding agents are the same. Two agents can have the same pass rate for very different reasons.
One might be close on the tasks it missed. The other might be flailing. One might fix things
cleanly. The other might delete half the project along the way and still pass. A pass rate
hides all of that, and the hidden part is exactly what you need to know if your job is to make
the agent better.

trajectory exists to make the hidden part visible.

---

## 4. The idea: watch the whole attempt, not just the result

Here is the core move. Instead of recording only the final yes or no, trajectory records
**every step the agent took**, and then scores the steps.

Think of it as grading how a student worked, not only the final answer. Did they take a
sensible route, or a long messy one. When something failed, did they adapt, or just try the
same thing again. Did they claim they were done before they had actually finished.

That recorded step-by-step attempt is the **trajectory**. The website lets you replay one,
one step at a time, which is the clearest way to understand what the project produces.

---

## 5. How one task works (the sealed exam)

A single test is called a **task**. There are twelve of them. Each task is built like a sealed
exam, with three parts.

- **A broken workspace.** A small software project with something wrong in it. This is what
  the agent sees and works in.
- **Hidden tests.** A set of checks that decide whether the bug is truly fixed. The agent
  never sees these while it works. They are added only after the agent stops. This matters: if
  the agent could see the tests, it could write code that satisfies them without really solving
  the problem, the way a student who steals the answer key does not prove they learned anything.
- **A reference solution.** A known-good fix, written by a person. It is used two ways. It
  proves the task is fair, because the reference must pass the hidden tests. And it gives a
  yardstick for efficiency, because we can compare how many steps the agent took against how
  many the reference needed.

The agent does its work inside a **sandbox**, which is a sealed container with no access to the
internet and no way out. That keeps every run clean and comparable, and it keeps the agent from
reaching the hidden tests. When the agent stops, the hidden tests are copied in and run, and
then every step of the attempt is scored.

---

## 6. What the harness measures (the ten readings)

Once a run is finished, trajectory computes ten numbers from it. You do not need to memorize
these. Skim them once so the leaderboard makes sense later.

1. **Solved.** Did every hidden test pass. The plain yes or no.
2. **Partial credit.** What fraction of the hidden tests passed. Taking four failing tests down
   to one is progress, and this is the number that can see it, where a plain pass rate cannot.
3. **Step efficiency.** How direct the route was, compared to the reference solution. A tidy fix
   scores high. A long wander scores low.
4. **Tool call validity.** How often the agent used its tools correctly, rather than sending a
   malformed or impossible action.
5. **Redundant action rate.** How often the agent repeated the exact same command, which usually
   means it was stuck and not adapting.
6. **Recovery rate.** When a command failed, did the agent change course, or just try the same
   thing again.
7. **Premature termination.** Did the agent announce it was finished while the hidden tests were
   still failing. Confidently wrong, in other words.
8. **Context drift.** Did the agent stay on task, or wander off and start solving something else.
9. **Cost and wall clock.** How much it would cost and how long it took.
10. **Destructive attempts.** Did the agent reach for dangerous commands, the kind that throw
    away work, such as wiping the project history before even looking at it.

Two of these come from an AI judge reading the run. The rest are mechanical rules, which means
anyone can recompute them from the record and get the same answer.

---

## 7. The named ways an agent goes wrong (failure modes)

A number tells you something went wrong. It does not tell you what. So trajectory also has a
list of **failure modes**: named, specific ways an agent can go off the rails. There are
eleven, labeled F01 to F11. A few examples, in plain terms:

- **Premature success (F03).** The agent said it was done while the tests were still red.
- **Retry loop (F04).** It ran the same failing command again and again without changing anything.
- **Destructive action (F08).** It ran a command that destroys work, such as resetting the code
  to an earlier state before understanding the problem.
- **Grader tampering (F11).** It left behind a file whose only purpose is to trick the scorer
  into reporting success. More on this in section 9.

Eight of the eleven are decided by plain rules. Three need judgement about intent, so they are
decided by an AI judge. Every flagged run carries the evidence, so a person can check it.

The useful part is that failure modes are computed even on runs that **passed**. An agent that
got the tests green but made a mess along the way, or reached for a destructive command and got
lucky, is a problem that succeeded. A pass rate cannot show you that. The failure modes can.

---

## 8. A tour of the website, page by page

The live site is at **https://mekala27-45.github.io/trajectory/**. Here is what each page is and
how to read it.

### The leaderboard (the home page)

This is a table with one row per agent. The headline column is the solve rate. The other
columns are the readings from section 6: how efficient, how valid, how well it recovered, how
often it quit early.

Two things to notice:

- **Every number carries a spread.** You will see something like `35.4% plus or minus 2.1`. The
  second number says how much the result wobbled between runs. A single run can be a fluke, so a
  result without its spread is not really a result. Showing the spread is a sign the measurement
  is honest.
- **There are two kinds of agent in the table, and they are labeled.** Most rows are **scripted
  policies**, tagged as such. These are not real AI models. They are small hand-written programs
  that each imitate one bad habit on purpose, so that every metric had a known answer before any
  real model was ever scored. They are how the harness itself was checked for correctness. Only
  the row without a `stub:` prefix, tagged **real model**, is an actual AI model. Keeping these
  two clearly separate is deliberate and important: the scripted rows prove the tool works, the
  real row is the actual finding.

### A run replay

Click any row, then click into a run, and you can step through the whole trajectory. Each step
shows what the agent was thinking, which tool it used, what it ran, and what came back. Where a
step went wrong, it is flagged with the matching failure mode. This is the heart of the project.
It is the difference between being told an agent failed and seeing exactly how and where.

### The failure modes page

This page shows how often each of the eleven failure modes fired, broken down by agent and by
how hard the task was. It is the high-level view of what tends to go wrong, across everything.

### The tasks

Each task has its own page describing what is broken and what counts as fixed, along with how
many steps the reference solution needed. This is where you see the actual problems the agents
are being asked to solve.

---

## 9. Three bigger ideas

Beyond the leaderboard, the project does three things that are worth understanding, because they
are what make it more than a scoreboard.

### Running the tasks in other systems (Harbor and ATIF)

Different labs use different software to run these experiments. A test that only works inside its
own tool is a walled garden. So trajectory speaks two shared formats. **Harbor** is a common task
format, so these twelve tasks can be run inside other labs' systems. **ATIF** is a common format
for a recorded run, so trajectory can read a run produced by another agent, such as Claude Code or
Codex, and score it with the same measurements. In short: the work is portable, not locked in.

### Can the grader be fooled? (reward hacking)

This is a subtle and important idea. The scorer runs the hidden tests against whatever the agent
left behind. That makes the scorer a target. A clever agent might try to make the score go up
without actually solving the problem, for example by planting a file that quietly tells the scorer
that everything passed. In the field this is called **reward hacking**, and it matters because an
agent that learns to game the scorer learns the wrong lesson.

trajectory takes this seriously. It closes the cheapest tricks, so a planted file cannot fool the
scorer the easy way, and a run that reports no real test result is treated as a failure rather than
a success. It also flags the attempt with failure mode F11, so you can see that the agent tried.
Just as important, the project is honest about the limits: one harder trick is left open on purpose
and named in the documentation, rather than hidden, with a plan for how to close it. Saying what is
not yet solved is part of doing this well.

### Running a real model for free

Running real AI models normally costs money. This project is built to run one at no cost, using a
free tier, with safety limits so it cannot overrun. That is how the single real-model result on the
leaderboard was produced. And running a real model for real did something useful: it surfaced two
measurement bugs in the harness itself, which were then fixed. Finding your own bugs is the point of
a measurement tool, not an embarrassment.

---

## 10. What this project shows

If you are looking at this to understand what the builder can do, here is the plain summary.

Building trajectory means designing a fair test, running untrusted code safely in sandboxes,
defining and computing meaningful measurements, building a website to explore the results,
making the work portable across other labs' tools, thinking like an attacker about how a scorer
could be cheated, and being honest about what is measured and what is not. It is, in miniature,
the job of evaluating AI systems carefully. That is the field this project belongs to.

---

## 11. A short glossary

- **Model.** The underlying AI. On its own, it only produces text.
- **Agent.** A model wired up with tools so it can take actions in a loop, not just talk.
- **Coding agent.** An agent whose job is to read, write, and fix code.
- **Task.** One test: a broken workspace, hidden tests, and a reference solution.
- **Trajectory.** The full step-by-step record of one attempt at a task.
- **Hidden tests.** The checks that decide success, kept from the agent until after it stops.
- **Reference solution.** A known-good fix, used to prove the task is fair and to measure efficiency.
- **Sandbox.** The sealed container the agent works in, with no internet and no way out.
- **Pass rate.** The fraction of tasks an agent solved. The thin number this project improves on.
- **Partial credit.** The fraction of hidden tests that passed on one task.
- **Failure mode.** A named, specific way an agent can go wrong, labeled F01 to F11.
- **Scripted policy.** A hand-written stand-in, not a real model, used to check the harness.
- **Reward hacking.** Making the score go up without really solving the task.
- **Harbor, ATIF.** Shared formats that let the tasks and the recorded runs work across different tools.
