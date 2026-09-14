import { test } from "@playwright/test";

import { longestRun, tasks } from "./bundle";

/**
 * Every route at both sizes, written to tests/screenshots for a human to look at. This is
 * not an assertion suite: it exists so that a layout regression is something you can see
 * rather than something you have to guess at from a passing test.
 */
const SIZES = [
  { label: "1280x800", width: 1280, height: 800 },
  { label: "400x900", width: 400, height: 900 },
] as const;

test.describe("screenshots", () => {
  for (const size of SIZES) {
    test(`captures every route at ${size.label}`, async ({ page }) => {
      await page.setViewportSize({ width: size.width, height: size.height });
      const run = longestRun();
      const task = tasks()[0]!;
      const routes = [
        { name: "leaderboard", url: "/" },
        { name: "task", url: `/tasks/${task.id}/` },
        { name: "run", url: `/runs/${run.id}/` },
        { name: "failures", url: "/failures/" },
      ];

      for (const route of routes) {
        await page.goto(route.url);
        await page.waitForLoadState("networkidle");
        await page.screenshot({
          path: `tests/screenshots/${route.name}-${size.label}.png`,
          fullPage: true,
        });
      }

      // The light theme has to work too, so it gets captured on the densest page.
      await page.goto(`/runs/${run.id}/`);
      await page.getByRole("button", { name: /switch to the light theme/i }).click();
      await page.waitForLoadState("networkidle");
      await page.screenshot({
        path: `tests/screenshots/run-light-${size.label}.png`,
        fullPage: true,
      });
    });
  }
});
