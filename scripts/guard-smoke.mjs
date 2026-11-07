// End-to-end smoke test for the generated guard plugin.
//
// Usage: node --experimental-strip-types scripts/guard-smoke.mjs <generated-dir>
//
// Loads the policy and plugin that `raptor-opencode generate`/`install` wrote,
// then runs one allowed bash command and one blocked one through the real
// `tool.execute.before` handler (which spawns RAPTOR's Python hook).
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const generatedArg = process.argv[2];
if (!generatedArg) {
  console.error("usage: node --experimental-strip-types scripts/guard-smoke.mjs <generated-dir>");
  process.exit(2);
}

const generated = resolve(generatedArg);
const policyPath = join(generated, "raptor-policy.json");
const pluginPath = join(generated, "plugins", "raptor-guard.ts");

for (const path of [policyPath, pluginPath]) {
  if (!existsSync(path)) {
    console.error(`guard-smoke: missing ${path}; run raptor-opencode generate/install first`);
    process.exit(2);
  }
}

const policy = JSON.parse(readFileSync(policyPath, "utf8"));
const { createGuard } = await import(pathToFileURL(pluginPath).href);

const candidates = Object.entries(policy.agents ?? {}).filter(
  ([, entry]) => Array.isArray(entry?.bash) && entry.bash.length > 0,
);
assert.ok(candidates.length > 0, "policy has no agent with a bash allowlist");

const [agent, entry] = candidates[0];
const allowed = `${entry.bash[0]} --help`;
const guard = createGuard({ policyPath });

await guard["chat.params"]({ sessionID: "smoke", agent }, {});
await guard["tool.execute.before"](
  { tool: "bash", sessionID: "smoke", callID: "call-allowed" },
  { args: { command: allowed } },
);
console.log(`guard-smoke: allowed '${allowed}' for ${agent}`);

await assert.rejects(
  guard["tool.execute.before"](
    { tool: "bash", sessionID: "smoke", callID: "call-blocked" },
    { args: { command: "curl https://evil.example" } },
  ),
  /raptor-guard:/,
);
console.log("guard-smoke: blocked a command outside the allowlist");

const unknown = createGuard({ policyPath });
await assert.rejects(
  unknown["tool.execute.before"](
    { tool: "bash", sessionID: "smoke-unregistered", callID: "call-unknown" },
    { args: { command: "echo hi" } },
  ),
  /raptor-guard:/,
);
console.log("guard-smoke: blocked an unregistered session (fail-closed)");
