import { defineConfig, devices } from "@playwright/test";

/**
 * Where the browsers live is the environment's business, not this file's. An earlier
 * version defaulted PLAYWRIGHT_BROWSERS_PATH here, which broke CI: `playwright install`
 * with an explicit browser name does not read this config, so it installed to the default
 * cache, while `playwright test` does read it and then looked somewhere else. Set the
 * variable in your shell if your browsers are not in the default cache.
 */

const PORT = Number(process.env.PORT ?? 3100);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : [["list"]],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    // The bundled chromium, rather than a channel, so the suite does not depend on a
    // browser being installed system wide.
    ...devices["Desktop Chrome"],
    channel: undefined,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], channel: undefined } }],
  /**
   * The site is a static export, so `next start` does not apply: the suite builds and
   * then serves `out/`, which is the same artefact that gets published.
   */
  webServer: {
    command: `npm run build && npx serve out --listen ${PORT} --no-clipboard`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 600_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
