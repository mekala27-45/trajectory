# trajectory web

The leaderboard, the task pages, the trajectory replay and the failure taxonomy. Next.js 15 App
Router, TypeScript, Tailwind v4, Recharts, TanStack Table.

Four routes:

| Route         | What it is                                                                       |
| ------------- | -------------------------------------------------------------------------------- |
| `/`           | Leaderboard. One row per model, suite and sandbox backend, plus the task list.   |
| `/tasks/[id]` | One task: what is broken, and every model's result on it with the spread.        |
| `/runs/[id]`  | Trajectory replay. Every step, its arguments, its output, and the modes flagged. |
| `/failures`   | The ten failure modes, their definitions, and their distribution.                |

## Two data sources, one client

`src/lib/api.ts` is the only place that reads data. It has two implementations behind one set of
functions (`getIndex`, `getLeaderboard`, `getTasks`, `getTaskResults`, `getRun`, `getFailures`,
`listRuns`), and no page knows which one it is talking to.

**The committed bundle (the default).** With `NEXT_PUBLIC_API_URL` unset, the client reads the JSON
in `public/data` off the file system at build time. That is what makes the demo work for a stranger
with no key, no account and no network: `npm run build` emits a fully static site from the recorded
runs that ship with the repository.

**A live API.** With `NEXT_PUBLIC_API_URL` set, the same functions call the results API
(`/v1/leaderboard`, `/v1/tasks`, `/v1/tasks/{id}/results`, `/v1/runs/{id}`,
`/v1/runs/{id}/trajectory`, `/v1/failure-modes`, `/v1/index`). Responses are fetched with
`cache: "force-cache"`, because a statically exported page cannot be rendered from an uncached
request: the numbers are baked at build time, and publishing new ones means building again.

`src/lib/types.ts` mirrors `packages/core/src/trajectory_core/models.py` field for field, by hand.
When the Python contract changes, change that file in the same commit.

## Running it

```bash
npm install

npm run dev                       # http://localhost:3000, reads public/data
npm run build                     # static export into out/
npm start                         # serves out/ on http://localhost:3000
```

Against a live API:

```bash
NEXT_PUBLIC_API_URL=https://api.example.org npm run dev
NEXT_PUBLIC_API_URL=https://api.example.org npm run build
```

Deployed to GitHub Pages under a repository subpath, set the base path. It drives `basePath` and
`assetPrefix`, and every internal link and asset URL goes through Next's router, so nothing else
needs changing:

```bash
NEXT_PUBLIC_BASE_PATH=/trajectory npm run build
```

## Regenerating the data bundle

From the repository root, after a run:

```bash
python scripts/build_web_bundle.py                           # newest run directory
python scripts/build_web_bundle.py runs/matrix --out web/public/data
```

That writes `index.json`, `leaderboard.json`, `tasks.json`, `runs.json`, `failures.json`, one file
per task under `tasks/`, and one sealed run record per run under `runs/`. The bundle is committed on
purpose. Note that the repository root `.gitignore` excludes every directory named `runs/`, which
would otherwise swallow `public/data/runs`; `web/.gitignore` puts it back.

Nothing in the app hardcodes a model name, a task id or a count. Adding models or runs to the bundle
and rebuilding is the whole of the update.

## Tests

```bash
npm test                          # builds, serves out/, runs the Playwright suite
```

`playwright.config.ts` starts its own server (`npm run build && npx serve out`) because a static
export cannot be served by `next start`. A server already listening on the port is reused, which is
the fast path while iterating.

Two environment notes. The config does not set `PLAYWRIGHT_BROWSERS_PATH`, deliberately: an
earlier version defaulted it and broke CI, because `playwright install chromium` does not read the
config while `playwright test` does, so the two resolved different directories. Run
`npx playwright install chromium` and let the default cache apply, or export the variable yourself
if your browsers live elsewhere. And `@playwright/test` is pinned to an exact version: the browser build that a Playwright release
expects is part of that release, and a floating range silently stops matching preinstalled browsers.

`tests/smoke.spec.ts` covers the leaderboard rendering its rows, a task page opening from it, a
replay showing one card per recorded step, `j` and `k` moving the focused step, jump to first
failure landing on the right step, long outputs collapsing, the failure charts rendering, filtering
runs by mode, and every route staying free of horizontal scroll at 1280 and at 400 pixels wide. It
reads the same bundle the site was built from, so the expectations follow the data rather than a
snapshot of it.

`tests/screenshots.spec.ts` writes every route at 1280x800 and 400x900 into `tests/screenshots/`,
plus the replay in the light theme. Those are for looking at, not for asserting on.

## Checks

```bash
npm run typecheck                 # tsc --noEmit
npm run lint                      # eslint
npm run format:check              # prettier
```

From the repository root, `python scripts/check_no_em_dash.py` enforces the house style: no em
dashes anywhere, in copy or in code. Use a comma, a colon, parentheses, or the word "to" for ranges.

## Layout

```
src/app/                 routes, one directory per route, server components
src/components/          presentation; "use client" only where a route needs state or keys
src/lib/types.ts         the data contract, mirrored from models.py
src/lib/api.ts           the only data access, both sources
src/lib/metrics.ts       column metadata, with the scorer's own wording
src/lib/format.ts        deterministic, locale free formatting
src/lib/colors.ts        the categorical palette for the failure charts
src/lib/highlight.ts     a small JSON tokeniser, and the output display caps
```

Colour lives in CSS custom properties defined once for dark (the default) and once for light. The
theme is stamped on the `html` element before first paint, so a reader who picked light never sees a
frame of dark.

## Things worth knowing before changing it

A `MetricStat` whose `n` is 0 renders `n/a`, never `0.000`. Step efficiency is undefined on unsolved
runs, so a model that solved nothing has no efficiency at all, and printing a zero would read as a
measurement of terrible efficiency rather than as the absence of one. `renderStat` in
`src/lib/format.ts` is the mirror of `MetricStat.render` on the Python side; keep them in step.

The replay is a client component, which means every byte of every step output is serialised into the
page. Outputs are cut at 256 KB on the server and again in the pane that renders them, and long
blocks render their first lines until the reader asks for the rest. The text is one text node rather
than one element per line, so a forty thousand line log dump is one string for the browser to lay
out instead of forty thousand nodes to style.

The dataset note (`index.note`, `leaderboard.note`) is rendered at full size, not as fine print. It
is what tells a reader that some rows come from an unisolated sandbox and that `stub:` models are
scripted policies rather than language models. A reader who misses it misreads the whole table.
