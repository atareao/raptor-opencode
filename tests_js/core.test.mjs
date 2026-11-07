import assert from "node:assert/strict";
import test from "node:test";

import {
  buildPayload,
  hookInvocation,
  interpretResult,
  selectEntry,
} from "../raptor_opencode/assets/raptor-guard-core.mjs";

const POLICY = {
  version: 1,
  hooks: {
    bash: ".claude/hooks/bash-command-allowlist.py",
    webfetch: ".claude/hooks/webfetch-domain-allowlist.py",
  },
  agents: {
    worker: {
      bash: ["libexec/raptor-bq-query", "python3 script.py"],
      webfetch: { domains: [], https_any: false },
    },
    web: {
      bash: [],
      webfetch: {
        domains: ["github.com", "api.github.com"],
        https_any: false,
        anchor_file: ".claude/run/web.anchor",
      },
    },
    any: { bash: [], webfetch: { domains: [], https_any: true } },
    empty: { bash: [], webfetch: { domains: [], https_any: false } },
  },
};
const HOME = "/opt/raptor";

test("selectEntry returns the agent entry with hooks attached", () => {
  const entry = selectEntry(POLICY, "worker");
  assert.equal(entry.bash.length, 2);
  assert.equal(entry.hooks.bash, POLICY.hooks.bash);
  assert.equal(selectEntry(POLICY, "missing"), null);
  assert.equal(selectEntry(null, "worker"), null);
});

test("hookInvocation builds bash arguments without a shell", () => {
  const invocation = hookInvocation(selectEntry(POLICY, "worker"), "bash", HOME);
  assert.deepEqual(invocation, {
    command: "python3",
    args: [
      "/opt/raptor/.claude/hooks/bash-command-allowlist.py",
      "libexec/raptor-bq-query",
      "python3 script.py",
    ],
    tool: "bash",
  });
});

test("hookInvocation builds webfetch domains, https-any and anchor", () => {
  const web = hookInvocation(selectEntry(POLICY, "web"), "webfetch", HOME);
  assert.deepEqual(web.args, [
    "/opt/raptor/.claude/hooks/webfetch-domain-allowlist.py",
    "github.com",
    "api.github.com",
    "--anchor-file",
    "/opt/raptor/.claude/run/web.anchor",
  ]);

  const any = hookInvocation(selectEntry(POLICY, "any"), "webfetch", HOME);
  assert.deepEqual(any.args, [
    "/opt/raptor/.claude/hooks/webfetch-domain-allowlist.py",
    "--https-any",
  ]);
});

test("hookInvocation is null when the agent has no restriction", () => {
  assert.equal(hookInvocation(selectEntry(POLICY, "empty"), "bash", HOME), null);
  assert.equal(hookInvocation(selectEntry(POLICY, "empty"), "webfetch", HOME), null);
  assert.equal(hookInvocation(selectEntry(POLICY, "worker"), "edit", HOME), null);
});

test("buildPayload matches the Claude PreToolUse contract", () => {
  assert.deepEqual(buildPayload("bash", { command: "ls" }), {
    tool_name: "Bash",
    tool_input: { command: "ls" },
  });
  assert.deepEqual(buildPayload("webfetch", { url: "https://x" }), {
    tool_name: "WebFetch",
    tool_input: { url: "https://x" },
  });
  assert.equal(buildPayload("edit", {}), null);
});

test("interpretResult allows exit 0", () => {
  assert.deepEqual(interpretResult({ code: 0, stderr: "" }), { action: "allow" });
});

test("interpretResult blocks exit 2 with the stderr reason", () => {
  assert.deepEqual(interpretResult({ code: 2, stderr: "domain denied\n" }), {
    action: "block",
    message: "domain denied",
  });
});

test("interpretResult fails closed on unexpected exit codes", () => {
  const verdict = interpretResult({ code: 127, stderr: "python3: not found" });
  assert.equal(verdict.action, "block");
  assert.match(verdict.message, /exit 127/);
  assert.match(verdict.message, /python3: not found/);

  const spawnFailure = interpretResult({ code: -1, stderr: "" });
  assert.equal(spawnFailure.action, "block");
  assert.match(spawnFailure.message, /spawn failure|exit -1/);
});
