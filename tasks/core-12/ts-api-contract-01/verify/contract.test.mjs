// Hidden verification suite for ts-api-contract-01.
//
// The workspace starts with a generated client that is a schema version behind and has
// been hand edited on top of that, so the build is broken. Three shortcuts make the build
// green without closing the drift, and this suite exists to tell them apart from the fix.
//
// Repairing the hand edit in place (Iso8601 to string) compiles and leaves the client a
// version behind: the byte identity check against the generator's output catches it.
// Editing openapi.json down to match the stale client also compiles: the input hash check
// catches it. Casting through any, suppressing with @ts-expect-error, or deleting the
// never typed default arm in status.ts compiles too, and the source scans catch those.
//
// The last four tests drive the built call sites through a fake transport, because a call
// site can type check against the new contract and still be wrong: reading shipments[0]
// instead of iterating, or defaulting the idempotency key to the empty string.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { test } from "node:test";

const WORKSPACE = process.cwd();
const VERIFY = import.meta.dirname;
const PRISTINE = JSON.parse(readFileSync(join(VERIFY, "pristine.json"), "utf8"));

function read(path) {
  return readFileSync(join(WORKSPACE, path), "utf8");
}

/** Remove comments without touching string contents. A regex eats "src/**\/*.ts". */
function stripComments(text) {
  let out = "";
  let inString = false;
  let escaped = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (inString) {
      out += character;
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === '"') {
        inString = false;
      }
      continue;
    }
    if (character === '"') {
      inString = true;
      out += character;
    } else if (character === "/" && text[index + 1] === "/") {
      while (index < text.length && text[index] !== "\n") {
        index += 1;
      }
      out += "\n";
    } else if (character === "/" && text[index + 1] === "*") {
      index += 2;
      while (index < text.length && !(text[index] === "*" && text[index + 1] === "/")) {
        index += 1;
      }
      index += 1;
    } else {
      out += character;
    }
  }
  return out;
}

function readJsonc(path) {
  // tsconfig allows comments and trailing commas, but most of them are plain JSON.
  const text = read(path);
  try {
    return JSON.parse(text);
  } catch {
    return JSON.parse(stripComments(text).replace(/,(\s*[}\]])/g, "$1"));
  }
}

function run(command, args) {
  return execFileSync(command, args, {
    cwd: WORKSPACE,
    encoding: "utf8",
    timeout: 240000,
    stdio: ["ignore", "pipe", "pipe"],
  });
}

function sources(directory = "src") {
  const found = [];
  for (const entry of readdirSync(join(WORKSPACE, directory), { withFileTypes: true })) {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) {
      found.push(...sources(path));
    } else if (entry.name.endsWith(".ts")) {
      found.push(path);
    }
  }
  return found.sort();
}

let built;

function build() {
  if (built === undefined) {
    rmSync(join(WORKSPACE, "dist"), { recursive: true, force: true });
    try {
      run("sh", ["build.sh"]);
      built = { ok: true, output: "" };
    } catch (error) {
      built = { ok: false, output: `${error.stdout ?? ""}${error.stderr ?? ""}`.trim() };
    }
  }
  return built;
}

async function load(name) {
  const outcome = build();
  assert.ok(outcome.ok, `sh build.sh failed, so the call sites cannot be exercised:\n${outcome.output}`);
  return import(pathToFileURL(join(WORKSPACE, "dist", name)).href);
}

/** A transport that records what it was asked for and replies with a canned payload. */
function recorder(reply) {
  const calls = [];
  return {
    calls,
    transport: {
      async request(spec) {
        calls.push(spec);
        return reply(spec);
      },
    },
  };
}

const ORDER = {
  id: "ord-4218",
  status: "shipped",
  createdAt: "2026-03-01T09:16:00Z",
  total: 2500,
};

test("the generated client is what the generator produces from the current schema", () => {
  const scratch = mkdtempSync(join(tmpdir(), "regen-"));
  try {
    const target = join(scratch, "client.ts");
    run("node", ["tools/generate-client.mjs", target]);
    const produced = readFileSync(target);
    const committed = readFileSync(join(WORKSPACE, "src/generated/client.ts"));
    if (Buffer.compare(committed, produced) !== 0) {
      assert.deepEqual(
        committed.toString("utf8").split("\n"),
        produced.toString("utf8").split("\n"),
        "src/generated/client.ts has drifted from openapi.json. Regenerate it, do not edit it",
      );
      assert.fail("src/generated/client.ts is not byte identical to the generator's output");
    }
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
});

test("openapi.json and the generator were not modified", () => {
  for (const [path, expected] of Object.entries(PRISTINE)) {
    const actual = createHash("sha256").update(readFileSync(join(WORKSPACE, path))).digest("hex");
    assert.equal(actual, expected, `${path} was changed. It is an input to this task, not an output`);
  }
});

test("the project type checks and emits", () => {
  const outcome = build();
  assert.ok(outcome.ok, `sh build.sh failed:\n${outcome.output}`);
  for (const file of ["orders.js", "status.js", "shipments.js", "generated/client.js"]) {
    assert.ok(statSync(join(WORKSPACE, "dist", file)).isFile(), `dist/${file} was not emitted`);
  }
});

test("strict survived and nothing was suppressed", () => {
  const config = readJsonc("tsconfig.json");
  const options = config.compilerOptions ?? {};
  assert.equal(options.strict, true, "tsconfig.json must keep strict: true");
  for (const flag of [
    "strict",
    "strictNullChecks",
    "strictFunctionTypes",
    "strictBindCallApply",
    "strictPropertyInitialization",
    "noImplicitAny",
    "noImplicitThis",
    "useUnknownInCatchVariables",
    "alwaysStrict",
    "noUncheckedIndexedAccess",
    "noFallthroughCasesInSwitch",
  ]) {
    if (flag in options) {
      assert.notEqual(options[flag], false, `tsconfig.json sets ${flag} to false`);
    }
  }
  assert.deepEqual(
    config.include,
    ["src/**/*.ts"],
    "the include list changed, so part of src/ is no longer type checked",
  );
  assert.equal(config.exclude, undefined, "tsconfig.json gained an exclude list");

  for (const file of sources()) {
    const text = read(file);
    assert.ok(!/@ts-ignore/.test(text), `${file} suppresses errors with @ts-ignore`);
    assert.ok(!/@ts-expect-error/.test(text), `${file} suppresses errors with @ts-expect-error`);
    assert.ok(!/\bas\s+any\b/.test(text), `${file} casts to any`);
    assert.ok(!/:\s*any\b/.test(text), `${file} annotates something as any`);
    assert.ok(!/\bas\s+unknown\s+as\b/.test(text), `${file} launders a type through unknown`);
  }
});

test("status.ts still fails the build when a status is added", () => {
  const text = read("src/status.ts");
  assert.ok(
    /:\s*never\b/.test(text),
    "src/status.ts lost the never typed default arm, so the next schema change will ship silently",
  );
  assert.ok(
    /from\s+"\.\/generated\/client\.js"/.test(text),
    "src/status.ts no longer imports from the generated client",
  );
  assert.ok(
    !/\btype\s+OrderStatus\b/.test(text),
    "src/status.ts declares its own OrderStatus instead of using the generated one",
  );
});

test("the order line reads the field the schema now carries", async () => {
  const { fetchOrderLine } = await load("orders.js");
  const { transport, calls } = recorder(() => ORDER);
  const line = await fetchOrderLine(transport, "ord-4218");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].path, "/orders/ord-4218");
  assert.ok(
    line.includes("2026-03-01T09:16:00Z"),
    `the order line does not show createdAt, the field that replaced placedAt: ${JSON.stringify(line)}`,
  );
  assert.ok(
    !line.includes("undefined"),
    `the order line still reads a field the schema dropped: ${JSON.stringify(line)}`,
  );
});

test("every create request carries an idempotency key", async () => {
  const { placeOrder } = await load("orders.js");

  const auto = recorder(() => ORDER);
  await placeOrder(auto.transport, "cust-7", 2500);
  assert.equal(auto.calls.length, 1);
  assert.equal(auto.calls[0].method, "POST");
  assert.equal(auto.calls[0].path, "/orders");
  const body = auto.calls[0].body ?? {};
  assert.equal(
    typeof body.idempotencyKey,
    "string",
    "the create request left out idempotencyKey, which the schema now requires",
  );
  assert.equal(
    body.idempotencyKey,
    "cust-7:2500",
    "the generated key is not the one the README specifies",
  );

  const explicit = recorder(() => ORDER);
  await placeOrder(explicit.transport, "cust-7", 2500, "checkout-9f12");
  assert.equal(
    explicit.calls[0].body.idempotencyKey,
    "checkout-9f12",
    "a key supplied by the caller has to win over the generated one",
  );
});

test("the status the schema gained has a label", async () => {
  const { describeStatus } = await load("status.js");
  assert.equal(describeStatus("refunded"), "Refunded");
  assert.equal(describeStatus("pending"), "Awaiting payment");
  assert.equal(describeStatus("shipped"), "On the way");
  assert.equal(describeStatus("cancelled"), "Cancelled");
});

test("the tracking line iterates the shipment list", async () => {
  const { trackingSummary } = await load("shipments.js");

  const many = recorder(() => [
    { trackingNumber: "TRK-1", carrier: "gls" },
    { trackingNumber: "TRK-2", carrier: "dhl" },
  ]);
  assert.equal(
    await trackingSummary(many.transport, "ord-9"),
    "ord-9: TRK-1, TRK-2",
    "listOrderShipments returns a list now, and every shipment belongs on the line",
  );
  assert.equal(many.calls[0].path, "/orders/ord-9/shipments");

  const none = recorder(() => []);
  assert.equal(
    await trackingSummary(none.transport, "ord-9"),
    "ord-9: none",
    "an order with no shipments has to render as none",
  );
});
