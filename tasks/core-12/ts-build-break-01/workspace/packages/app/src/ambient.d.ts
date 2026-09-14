// This workspace builds with no node_modules and no network, so the handful of host
// globals it touches are declared here rather than pulled from @types/node.
declare const console: { log(...args: unknown[]): void };
declare const process: { argv: readonly string[] };
