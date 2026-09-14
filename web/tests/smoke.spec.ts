import { expect, test, type Page } from "@playwright/test";

import {
  failures,
  firstFailureStep,
  index,
  leaderboard,
  longestRun,
  runWithAFailure,
  tasks,
} from "./bundle";

/** Nothing may scroll sideways except the containers that opt in. */
async function horizontalOverflow(page: Page): Promise<number> {
  return page.evaluate(() => {
    const doc = document.documentElement;
    return doc.scrollWidth - doc.clientWidth;
  });
}

test.describe("leaderboard", () => {
  test("renders one row per leaderboard entry", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();

    const expected = leaderboard().rows.length;
    const rows = page.locator("table tbody tr");
    await expect(rows).toHaveCount(expected);
    expect(expected).toBeGreaterThan(0);

    // The note is the thing a reader must not miss, so it is asserted, not assumed.
    await expect(
      page.getByRole("complementary", { name: /how to read these numbers/i }),
    ).toBeVisible();

    // Solve rate renders as a bar with an accessible label, not a bare number.
    await expect(page.getByRole("img", { name: /solve rate .* percent/i }).first()).toBeVisible();

    // Every model in the dataset has a task list entry to reach its runs from.
    for (const suite of index().suites) {
      await expect(page.getByRole("option", { name: suite })).toHaveCount(1);
    }
  });

  test("sorts when a header is clicked", async ({ page }) => {
    await page.goto("/");
    const header = page.getByRole("columnheader", { name: /step efficiency/i });
    await expect(header).toHaveAttribute("aria-sort", "none");
    await header.getByRole("button").click();
    await expect(header).toHaveAttribute("aria-sort", /ascending|descending/);
  });
});

test.describe("task page", () => {
  test("opens from the leaderboard and lists results by model", async ({ page }) => {
    await page.goto("/");
    const first = tasks()[0]!;
    await page.getByRole("link", { name: new RegExp(first.title.slice(0, 40), "i") }).click();
    await expect(page).toHaveURL(new RegExp(`/tasks/${first.id}/?$`));
    await expect(page.getByRole("heading", { level: 1, name: first.title })).toBeVisible();
    await expect(page.getByRole("heading", { name: /results by model/i })).toBeVisible();
    await expect(page.getByText(/difficulty tier/i).first()).toBeVisible();
  });
});

test.describe("trajectory replay", () => {
  test("shows one card per recorded step", async ({ page }) => {
    const run = longestRun();
    await page.goto(`/runs/${run.id}/`);
    await expect(page.locator("[data-step-index]")).toHaveCount(run.steps.length);
    await expect(page.getByRole("heading", { name: /^trajectory$/i })).toBeVisible();
    await expect(page.getByText(/hidden tests|verification did not run/i).first()).toBeVisible();
  });

  test("j and k move the focused step", async ({ page }) => {
    const run = longestRun();
    expect(run.steps.length).toBeGreaterThan(2);
    await page.goto(`/runs/${run.id}/`);

    await page.locator("[data-step-index]").first().waitFor();
    await page.keyboard.press("j");
    await expect(page.locator('[data-step-index="0"]')).toHaveAttribute("data-focused", "true");

    await page.keyboard.press("j");
    await expect(page.locator('[data-step-index="1"]')).toHaveAttribute("data-focused", "true");
    await expect(page.locator('[data-step-index="0"]')).not.toHaveAttribute("data-focused", "true");

    await page.keyboard.press("k");
    await expect(page.locator('[data-step-index="0"]')).toHaveAttribute("data-focused", "true");

    // The focused card is the active element, so the focus ring is real focus.
    const focusedIndex = await page.evaluate(() =>
      document.activeElement?.getAttribute("data-step-index"),
    );
    expect(focusedIndex).toBe("0");
  });

  test("jump to first failure lands on the first flagged or failing step", async ({ page }) => {
    const run = runWithAFailure();
    test.skip(run == null, "no run in this bundle has a flagged step or a non-zero exit code");
    const target = firstFailureStep(run!);
    await page.goto(`/runs/${run!.id}/`);

    const control = page.getByRole("button", { name: /jump to first failure/i });
    await expect(control).toBeEnabled();
    await control.click();
    await expect(page.locator(`[data-step-index="${target}"]`)).toHaveAttribute(
      "data-focused",
      "true",
    );
  });

  test("long outputs collapse behind an expand control", async ({ page }) => {
    const run = longestRun();
    await page.goto(`/runs/${run.id}/`);
    const expander = page.locator('button[data-role="expand-output"]').first();
    const count = await expander.count();
    test.skip(count === 0, "no step in this run has an output long enough to collapse");
    await expect(expander).toHaveAttribute("aria-expanded", "false");
    await expander.click();
    await expect(expander).toHaveAttribute("aria-expanded", "true");
  });
});

test.describe("failure modes", () => {
  test("charts render and the taxonomy is listed", async ({ page }) => {
    await page.goto("/failures/");
    await expect(page.getByRole("heading", { level: 1, name: /failure modes/i })).toBeVisible();

    // Two charts: by model and by task difficulty. One wrapper each, and the legend
    // inside each one draws its own surface, so the wrapper is what to count.
    const charts = page.locator(".recharts-wrapper");
    await expect(charts).toHaveCount(2);
    await expect(charts.first()).toBeVisible();
    await expect(page.locator("svg.recharts-surface").first()).toBeVisible();
    await expect(page.getByRole("img", { name: /failure modes by model/i })).toBeVisible();
    await expect(
      page.getByRole("img", { name: /failure modes by task difficulty/i }),
    ).toBeVisible();

    const taxonomy = failures().taxonomy;
    for (const spec of taxonomy) {
      await expect(
        page.getByRole("heading", { name: new RegExp(`${spec.id}\\s+${spec.name}`) }),
      ).toBeVisible();
    }
  });

  test("selecting a mode filters the run list", async ({ page }) => {
    await page.goto("/failures/");
    const withRuns = failures().overall.filter((entry) => entry.count > 0);

    if (withRuns.length === 0) {
      // Nothing fired in this bundle. The buttons must say so rather than mislead.
      await expect(page.getByText(/no failure modes fired in this dataset/i)).toBeVisible();
      await expect(page.getByRole("button", { name: /^F01/ })).toBeDisabled();
      return;
    }

    const target = withRuns[0]!;
    await page.getByRole("button", { name: new RegExp(`^${target.id}`) }).click();
    const items = page.locator("ul li a[href*='/runs/']");
    await expect(items.first()).toBeVisible();
    expect(await items.count()).toBeGreaterThan(0);
  });
});

test.describe("layout", () => {
  const widths = [
    { name: "desktop", size: { width: 1280, height: 800 } },
    { name: "phone", size: { width: 400, height: 900 } },
  ];

  for (const { name, size } of widths) {
    test(`no page scrolls horizontally at ${name} width`, async ({ page }) => {
      await page.setViewportSize(size);
      const run = longestRun();
      const first = tasks()[0]!;
      for (const route of ["/", `/tasks/${first.id}/`, `/runs/${run.id}/`, "/failures/"]) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        expect(await horizontalOverflow(page), `${route} overflows at ${name}`).toBeLessThanOrEqual(
          1,
        );
      }
    });
  }
});
