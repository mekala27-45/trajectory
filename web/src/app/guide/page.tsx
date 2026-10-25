import Link from "next/link";

export const metadata = {
  title: "Guide",
  description:
    "A plain-language guide to the whole project, for anyone new to it. It assumes only that you know what an AI model is.",
};

const REPO = "https://github.com/mekala27-45/trajectory";

/** A monospaced inline term, for the vocabulary of the field. */
function Term({ children }: { children: React.ReactNode }) {
  return <span className="font-mono text-[0.92em] text-accent">{children}</span>;
}

/** A section with a serif heading and a readable prose measure. */
function Section({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t border-line pt-7">
      <h2 className="flex items-baseline gap-3 text-[20px] sm:text-[22px]">
        <span className="font-mono text-[13px] font-normal text-dim">{n}</span>
        <span>{title}</span>
      </h2>
      <div className="flex max-w-[68ch] flex-col gap-4 text-[15px] leading-[1.65] text-fg">
        {children}
      </div>
    </section>
  );
}

export default function GuidePage() {
  return (
    <div className="flex flex-col gap-8 pb-4">
      <header className="flex flex-col gap-4">
        <p className="font-mono text-[12.5px] text-accent">a plain-language guide</p>
        <h1 className="max-w-[18ch] text-[30px] leading-[1.08] sm:text-[40px]">
          Understanding trajectory
        </h1>
        <p className="max-w-[68ch] text-[16px] leading-[1.6] text-muted">
          This page assumes you know roughly what an AI model is. It assumes nothing else. By the
          end you will understand what this project is, what problem it solves, how it works, and
          what you are looking at on every page of this site. Read it like a short lesson. Each
          section builds on the one before it.
        </p>
        <p className="max-w-[68ch] text-[14px] leading-[1.6] text-dim">
          If you are an engineer who wants the precise version, the{" "}
          <a className="text-accent hover:underline" href={REPO} target="_blank" rel="noreferrer">
            repository
          </a>{" "}
          has it. This is the gentle on-ramp, not the specification.
        </p>
      </header>

      <Section n="01" title="The short version">
        <p>
          An AI coding agent is a program that fixes or writes code on its own. This project,{" "}
          <Term>trajectory</Term>, is a way to test how good a coding agent really is.
        </p>
        <p>
          Most tests ask one question: did the agent fix the bug, yes or no. That single answer
          hides a lot. trajectory watches the whole attempt, step by step, and measures how the
          agent worked, not only whether it got there. That is the whole idea, and the name says it.
          A trajectory is the path something takes, not just where it ends up.
        </p>
      </Section>

      <Section n="02" title="What is a coding agent?">
        <p>
          Start with a plain AI model, the kind that answers questions. On its own it only produces
          text. It cannot open a file, run a program, or see whether its idea actually worked.
        </p>
        <p>
          A coding <Term>agent</Term> is that model wired up so it can act. It is given tools: read
          a file, run a command, edit code, run the tests. It works in a loop. It looks at the
          problem, takes an action, sees what happened, and decides what to do next, over and over,
          until it thinks the job is done.
        </p>
        <p>
          So a coding agent does not just talk about fixing a bug. It opens the broken project,
          pokes around, makes changes, runs things, and keeps going until it believes the code
          works. The question this project answers is: when it does that, how well does it do it.
        </p>
      </Section>

      <Section n="03" title="The problem: a pass rate does not tell you much">
        <p>
          The usual way to score a coding agent is the <Term>pass rate</Term>. You give it a hundred
          broken programs and count how many it fixed. If it fixed forty-one, the pass rate is 41
          percent.
        </p>
        <p>
          That number is real, but it is thin. Imagine two students who both score 41 on the same
          exam. One worked carefully, showed clear reasoning, and ran out of time. The other
          guessed, copied, and got lucky on a few questions. Same score, completely different
          students. The score alone cannot tell them apart.
        </p>
        <p>
          Coding agents are the same. Two can have the same pass rate for very different reasons.
          One might be close on the tasks it missed. The other might be flailing. One might fix
          things cleanly. The other might delete half the project along the way and still pass. A
          pass rate hides all of that, and the hidden part is exactly what you need to know if your
          job is to make the agent better. trajectory exists to make the hidden part visible.
        </p>
      </Section>

      <Section n="04" title="The idea: watch the whole attempt, not just the result">
        <p>
          Here is the core move. Instead of recording only the final yes or no, trajectory records
          every step the agent took, and then scores the steps.
        </p>
        <p>
          Think of it as grading how a student worked, not only the final answer. Did they take a
          sensible route, or a long messy one. When something failed, did they adapt, or just try
          the same thing again. Did they claim they were done before they had actually finished.
        </p>
        <p>
          That recorded step-by-step attempt is the <Term>trajectory</Term>. This site lets you
          replay one, one step at a time, which is the clearest way to understand what the project
          produces.
        </p>
      </Section>

      <Section n="05" title="How one task works (the sealed exam)">
        <p>
          A single test is called a <Term>task</Term>. There are twelve of them. Each is built like
          a sealed exam, with three parts.
        </p>
        <ul className="flex flex-col gap-2.5 pl-1">
          <li>
            <b className="font-semibold">A broken workspace.</b> A small software project with
            something wrong in it. This is what the agent sees and works in.
          </li>
          <li>
            <b className="font-semibold">Hidden tests.</b> The checks that decide whether the bug is
            truly fixed. The agent never sees these while it works. They go in only after it stops.
            If the agent could see the tests, it could satisfy them without really solving the
            problem, the way a student who steals the answer key proves nothing.
          </li>
          <li>
            <b className="font-semibold">A reference solution.</b> A known-good fix, written by a
            person. It proves the task is fair, because the reference must pass the hidden tests,
            and it gives a yardstick for efficiency, because we can compare the agent step count
            against the reference.
          </li>
        </ul>
        <p>
          The agent works inside a <Term>sandbox</Term>, a sealed container with no internet and no
          way out. That keeps every run clean and comparable, and it keeps the agent away from the
          hidden tests. When the agent stops, the tests are copied in and run, and then every step
          of the attempt is scored.
        </p>
      </Section>

      <Section n="06" title="What the harness measures (the ten readings)">
        <p>
          Once a run is finished, trajectory computes ten numbers from it. Skim them once so the
          leaderboard makes sense later.
        </p>
        <ol className="flex flex-col gap-2 pl-5 [list-style:decimal]">
          <li>
            <b className="font-semibold">Solved.</b> Did every hidden test pass. The plain yes or
            no.
          </li>
          <li>
            <b className="font-semibold">Partial credit.</b> What fraction of the hidden tests
            passed. Four failing tests down to one is progress, and this number can see it.
          </li>
          <li>
            <b className="font-semibold">Step efficiency.</b> How direct the route was, compared to
            the reference solution.
          </li>
          <li>
            <b className="font-semibold">Tool call validity.</b> How often the agent used its tools
            correctly, rather than sending a malformed action.
          </li>
          <li>
            <b className="font-semibold">Redundant action rate.</b> How often it repeated the exact
            same command, which usually means it was stuck.
          </li>
          <li>
            <b className="font-semibold">Recovery rate.</b> When a command failed, did the agent
            change course or just try the same thing again.
          </li>
          <li>
            <b className="font-semibold">Premature termination.</b> Did it announce it was finished
            while the hidden tests were still failing. Confidently wrong.
          </li>
          <li>
            <b className="font-semibold">Context drift.</b> Did it stay on task, or wander off.
          </li>
          <li>
            <b className="font-semibold">Cost and wall clock.</b> How much it would cost and how
            long it took.
          </li>
          <li>
            <b className="font-semibold">Destructive attempts.</b> Did it reach for dangerous
            commands, the kind that throw away work.
          </li>
        </ol>
        <p>
          Two of these come from an AI judge reading the run. The rest are mechanical rules, which
          means anyone can recompute them from the record and get the same answer.
        </p>
      </Section>

      <Section n="07" title="The named ways an agent goes wrong (failure modes)">
        <p>
          A number tells you something went wrong. It does not tell you what. So trajectory also has
          a list of <Term>failure modes</Term>: named, specific ways an agent can go off the rails.
          There are eleven, labeled F01 to F11. A few, in plain terms:
        </p>
        <ul className="flex flex-col gap-2.5 pl-1">
          <li>
            <b className="font-semibold">Premature success (F03).</b> It said it was done while the
            tests were still red.
          </li>
          <li>
            <b className="font-semibold">Retry loop (F04).</b> It ran the same failing command again
            and again without changing anything.
          </li>
          <li>
            <b className="font-semibold">Destructive action (F08).</b> It ran a command that
            destroys work before understanding the problem.
          </li>
          <li>
            <b className="font-semibold">Grader tampering (F11).</b> It left behind a file whose
            only purpose is to trick the scorer into reporting success. More on this below.
          </li>
        </ul>
        <p>
          Eight of the eleven are decided by plain rules. Three need judgement about intent, so an
          AI judge decides them. The useful part is that failure modes are computed even on runs
          that passed. An agent that got the tests green but made a mess, or reached for a
          destructive command and got lucky, is a problem that succeeded. A pass rate cannot show
          that. The failure modes can.
        </p>
      </Section>

      <Section n="08" title="A tour of this site, page by page">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <h3 className="text-[16px] text-fg">The leaderboard</h3>
            <p>
              A table with one row per agent. The headline column is the solve rate. The others are
              the readings above. Two things to notice. Every number carries a spread, like{" "}
              <Term>35.4% +/- 2.1</Term>, which says how much the result wobbled between runs, so a
              fluke cannot pass for a finding. And there are two kinds of agent, clearly labeled.
              Most rows are <Term>scripted policies</Term>, small hand-written stand-ins that each
              imitate one bad habit on purpose, used to check the harness itself. Only the rows
              without a <Term>stub:</Term> prefix, tagged real model, are actual AI models.
            </p>
            <p>
              Below the table, <Term>How sure these numbers are</Term> draws each real model as a
              dot inside a band: the range its score could land in on a different set of similar
              tasks. A head to head table then says, for every pair, whether these twelve tasks can
              tell the two apart at all.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="text-[16px] text-fg">A run replay</h3>
            <p>
              Click a row, then a run, and step through the whole trajectory. Each step shows what
              the agent was thinking, which tool it used, and what came back, with any failure mode
              flagged in place. This is the heart of the project: the difference between being told
              an agent failed and seeing exactly how and where.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="text-[16px] text-fg">The failure modes page</h3>
            <p>
              How often each of the eleven modes fired, broken down by agent and by task difficulty.
              The high-level view of what tends to go wrong, across everything.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="text-[16px] text-fg">The tasks</h3>
            <p>
              Each task has a page describing what is broken and what counts as fixed. This is where
              you see the actual problems the agents are asked to solve. It also says how strong the
              hidden tests are: how many small deliberate mistakes in the correct solution they
              caught, and which ones slipped through.
            </p>
          </div>
        </div>
      </Section>

      <Section n="09" title="Five bigger ideas">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <h3 className="text-[16px] text-fg">Running the tasks in other systems</h3>
            <p>
              Different labs use different software. A test that only works inside its own tool is a
              walled garden. So trajectory speaks two shared formats, Harbor and ATIF, which let
              these tasks run inside other labs systems and let trajectory read and score a run
              produced by another agent such as Claude Code or Codex. The work is portable, not
              locked in.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="text-[16px] text-fg">Can the grader be fooled?</h3>
            <p>
              The scorer runs the hidden tests against whatever the agent left behind, which makes
              the scorer a target. A clever agent might try to raise its score without solving the
              problem. In the field this is called <Term>reward hacking</Term>. Picture an exam.
              trajectory marks the work in a <Term>fresh room</Term>: only the finished work is
              carried in, and anything else the agent left lying around stays behind. It slips in a{" "}
              <Term>trap question</Term> that must come back wrong: a marking sheet that says every
              question was right, that one included, has been tampered with and is thrown out. And
              it locks the doors the cheapest tricks would use. The trap catches a sheet changed as
              a whole, not one changed answer by answer, and the documentation says so plainly.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="text-[16px] text-fg">Running real models for free</h3>
            <p>
              Running real AI models normally costs money. This project runs a whole{" "}
              <Term>panel</Term> of them at no cost, on a free tier, with limits so it cannot
              overrun. Each model is first sent one tiny test message, so a model that is offline or
              too slow costs one message, not an afternoon. Running real models also found a real
              bug in the harness: files the agent wrote were locked so it could not edit them
              afterwards. It was fixed, and every published model result was run again after the
              fix. Finding your own bugs is the point of a measurement tool, not an embarrassment.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="text-[16px] text-fg">How sure are the numbers?</h3>
            <p>
              Twelve tasks is a small exam. Sit the same students in front of twelve other questions
              of the same kind and their scores would move. So every real model on the site comes
              with a <Term>range</Term>: the band its score could plausibly land in on a different
              set of similar tasks. Two programs whose ranges overlap a lot are ones this exam
              cannot honestly rank. The site also shows <Term>pass@k</Term>, the chance that at
              least one of k attempts at a task succeeds.
            </p>
          </div>
          <div className="flex flex-col gap-2">
            <h3 className="text-[16px] text-fg">How strong are the hidden tests?</h3>
            <p>
              A test is only useful if it can tell a right answer from an almost right one. So the
              project takes each correct solution, makes one tiny mistake in it, the kind a program
              makes, and runs the hidden tests. If they notice, good. If not, that{" "}
              <Term>near miss</Term> would have been marked correct, and an AI trained on these
              tests would be rewarded for it. This is <Term>mutation testing</Term>, and every near
              miss that slipped through is listed by name on its task page.
            </p>
          </div>
        </div>
      </Section>

      <Section n="10" title="What this project shows">
        <p>
          Building trajectory means designing a fair test, running untrusted code safely in
          sandboxes, defining and computing meaningful measurements, building a site to explore the
          results, making the work portable across other labs tools, thinking like an attacker about
          how a scorer could be cheated, measuring how sure each number is and how strong the tests
          behind it are, and being honest about what is measured and what is not. It is, in
          miniature, the job of evaluating AI systems carefully. That is the field this project
          belongs to.
        </p>
        <p className="pt-1 text-[14px] text-muted">
          Ready to look? Start with the{" "}
          <Link className="text-accent hover:underline" href="/">
            leaderboard
          </Link>
          , open any row, and replay a run.
        </p>
      </Section>
    </div>
  );
}
