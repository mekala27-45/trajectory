// Hidden verification suite for ts-build-break-01.
//
// Checks that the build works, that the output is correct, that the dependency was
// actually built through a project reference rather than by hand, and that strict mode
// survived. The last one matters: turning strict off makes the error go away without
// fixing anything, and a benchmark that cannot tell those apart is measuring nothing.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { test } from "node:test";

const WORKSPACE = process.cwd();

function read(path) {
  return readFileSync(`${WORKSPACE}/${path}`, "utf8");
}

function readJsonc(path) {
  // tsconfig files allow comments and trailing commas. Strip both before parsing.
  const text = read(path)
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:"'\\])\/\/.*$/gm, "$1")
    .replace(/,(\s*[}\]])/g, "$1");
  return JSON.parse(text);
}

function run(command, args) {
  return execFileSync(command, args, {
    cwd: WORKSPACE,
    encoding: "utf8",
    timeout: 240000,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

test("strict mode is still enabled at the base of the workspace", () => {
  const base = readJsonc("tsconfig.base.json");
  assert.equal(base.compilerOptions.strict, true, "tsconfig.base.json must keep strict: true");
});

test("no package weakens a strict family check", () => {
  const weakened = [
    "strict",
    "strictNullChecks",
    "strictFunctionTypes",
    "strictBindCallApply",
    "strictPropertyInitialization",
    "noImplicitAny",
    "noImplicitThis",
    "useUnknownInCatchVariables",
    "alwaysStrict",
  ];
  for (const config of [
    "tsconfig.base.json",
    "packages/core/tsconfig.json",
    "packages/app/tsconfig.json",
  ]) {
    const options = readJsonc(config).compilerOptions ?? {};
    for (const flag of weakened) {
      if (flag in options) {
        assert.notEqual(options[flag], false, `${config} sets ${flag} to false`);
      }
    }
    assert.notEqual(options.skipDefaultLibCheck, true, `${config} skips the default lib check`);
  }
});

test("no error suppression was added to the application", () => {
  const source = read("packages/app/src/main.ts");
  assert.ok(!/@ts-ignore/.test(source), "main.ts suppresses errors with @ts-ignore");
  assert.ok(!/@ts-expect-error/.test(source), "main.ts suppresses errors with @ts-expect-error");
  assert.ok(!/\bas\s+any\b/.test(source), "main.ts casts to any");
  assert.ok(!/\bas\s+unknown\s+as\b/.test(source), "main.ts launders a type through unknown");
  assert.ok(!/\b!\./.test(source.replace(/!==/g, "")), "main.ts uses a non-null assertion");
});

test("the core package was not modified", () => {
  const shapes = read("packages/core/src/shapes.ts");
  assert.ok(
    /export function findShape\(name: string\): Shape \| undefined/.test(shapes),
    "findShape's signature changed, which moves the problem rather than solving it",
  );
  const core = readJsonc("packages/core/tsconfig.json");
  assert.equal(core.compilerOptions.composite, undefined, "core's own tsconfig gained overrides");
});

test("a clean build succeeds", () => {
  rmSync(`${WORKSPACE}/packages/app/dist`, { recursive: true, force: true });
  rmSync(`${WORKSPACE}/packages/core/dist`, { recursive: true, force: true });
  rmSync(`${WORKSPACE}/packages/app/tsconfig.tsbuildinfo`, { force: true });
  rmSync(`${WORKSPACE}/packages/core/tsconfig.tsbuildinfo`, { force: true });
  run("sh", ["build.sh"]);
  assert.ok(existsSync(`${WORKSPACE}/packages/app/dist/main.js`), "the app was not emitted");
});

test("the dependency was built by the reference, not by hand", () => {
  assert.ok(
    existsSync(`${WORKSPACE}/packages/core/dist/index.js`),
    "core was not built, so the app does not declare it as a project reference",
  );
  const app = readJsonc("packages/app/tsconfig.json");
  const references = app.references ?? [];
  assert.ok(
    references.some((entry) => typeof entry.path === "string" && entry.path.includes("core")),
    "packages/app/tsconfig.json does not reference packages/core",
  );
});

test("the report is correct", () => {
  const output = run("node", ["packages/app/dist/main.js"]).trim().split("\n");
  assert.deepEqual(output, [
    "rectangle has 4 sides and an area of 12.00",
    "circle has 0 sides and an area of 12.57",
  ]);
});
