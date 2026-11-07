import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  createGuard,
  handleToolBefore,
  runHook,
  SessionRegistry,
} from "../raptor_opencode/assets/raptor-guard.ts";
import { interpretResult } from "../raptor_opencode/assets/raptor-guard-core.mjs";

const RAPTOR_SRC = "/tmp/opencode/raptor-src";
const BASH_HOOK = join(RAPTOR_SRC, ".claude", "hooks", "bash-command-allowlist.py");
const WEBFETCH_HOOK = join(RAPTOR_SRC, ".claude", "hooks", "webfetch-domain-allowlist.py");
const hasHooks = existsSync(BASH_HOOK) && existsSync(WEBFETCH_HOOK);

const POLICY = {
  version: 1,
  raptor_home: RAPTOR_SRC,
  default: { unrestricted: false },
  hooks: {
    bash: ".claude/hooks/bash-command-allowlist.py",
    webfetch: ".claude/hooks/webfetch-domain-allowlist.py",
  },
  agents: {
    "oss-investigator-gh-archive-agent": {
      bash: ["libexec/raptor-bq-query"],
      webfetch: { domains: [], https_any: false },
    },
    "oss-investigator-github-agent": {
      bash: [],
      webfetch: {
        domains: ["github.com", "api.github.com"],
        https_any: false,
      },
    },
  },
};

const SKIP = { skip: !hasHooks && "RAPTOR v3.1.0 hooks not available" };

async function register(guard, sessionID, agent) {
  await guard["chat.params"]({ sessionID, agent }, {});
}

function call(guard, sessionID, tool, args) {
  return guard["tool.execute.before"]({ tool, sessionID, callID: "call-1" }, { args });
}

test("chat.params registers the agent and an allowlisted bash command passes", SKIP, async () => {
  const guard = createGuard({ policy: POLICY, projectDir: RAPTOR_SRC });

  await register(guard, "s-allow", "oss-investigator-gh-archive-agent");
  await call(guard, "s-allow", "bash", { command: "libexec/raptor-bq-query --help" });
});

test("bash chaining and commands outside the allowlist are blocked", SKIP, async () => {
  const guard = createGuard({ policy: POLICY, projectDir: RAPTOR_SRC });
  await register(guard, "s-bash", "oss-investigator-gh-archive-agent");

  await assert.rejects(
    call(guard, "s-bash", "bash", {
      command: "libexec/raptor-bq-query --help; curl https://evil.example",
    }),
    /raptor-guard: .*not allowed/,
  );

  await assert.rejects(
    call(guard, "s-bash", "bash", { command: "curl https://evil.example" }),
    /raptor-guard: .*allowlist/,
  );
});

test("webfetch allows a known domain and blocks another one", SKIP, async () => {
  const guard = createGuard({ policy: POLICY, projectDir: RAPTOR_SRC });
  await register(guard, "s-web", "oss-investigator-github-agent");

  await call(guard, "s-web", "webfetch", { url: "https://api.github.com/repos/raptor" });

  await assert.rejects(
    call(guard, "s-web", "webfetch", { url: "https://evil.example/report" }),
    /raptor-guard: .*/,
  );
});

test("a missing policy fails closed", async () => {
  const guard = createGuard({ policy: null });

  await register(guard, "s-no-policy", "oss-investigator-github-agent");
  await assert.rejects(
    call(guard, "s-no-policy", "bash", { command: "echo hi" }),
    /raptor-policy\.json is missing or invalid/,
  );
});

test("an unknown session fails closed unless default.unrestricted is true", async () => {
  const strict = createGuard({ policy: POLICY });
  await assert.rejects(
    call(strict, "s-unknown", "bash", { command: "echo hi" }),
    /no agent is registered for session s-unknown/,
  );

  const permissive = createGuard({
    policy: { ...POLICY, default: { unrestricted: true } },
  });
  await call(permissive, "s-unknown", "bash", { command: "echo hi" });
});

test("an agent without a policy entry is allowed once and logged once", async () => {
  const messages = [];
  const guard = createGuard({
    policy: { ...POLICY, agents: {} },
    warnOnce: (_key, message) => messages.push(message),
  });
  await register(guard, "s-parity", "plain-agent");

  await call(guard, "s-parity", "bash", { command: "echo hi" });
  await call(guard, "s-parity", "bash", { command: "echo again" });

  assert.equal(messages.length, 1);
  assert.match(messages[0], /no policy entry for agent plain-agent/);
});

test("a hook that exceeds the timeout is killed and blocked", SKIP, async () => {
  const root = mkdtempSync(join(tmpdir(), "raptor-timeout-"));
  const sleeping = join(root, "sleep.py");
  writeFileSync(sleeping, "import time\ntime.sleep(5)\n", "utf8");

  const policy = {
    ...POLICY,
    hooks: { bash: sleeping },
    agents: { sleeper: { bash: ["anything"], webfetch: { domains: [], https_any: false } } },
  };
  const guard = createGuard({ policy, projectDir: RAPTOR_SRC, timeoutMs: 300 });
  await register(guard, "s-timeout", "sleeper");

  const started = Date.now();
  try {
    await assert.rejects(
      call(guard, "s-timeout", "bash", { command: "anything" }),
      /timed out after 300ms/,
    );
    assert.ok(Date.now() - started < 2000, "timeout must fire in under 2s");
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("spawn ENOENT is reported as a block", async () => {
  const result = await runHook(
    { command: "raptor-binary-that-does-not-exist", args: [], tool: "bash" },
    "{}",
    1000,
  );
  assert.equal(result.code, -1);
  assert.match(result.stderr, /ENOENT|not found/i);
  assert.equal(interpretResult(result).action, "block");
});

test("an EPIPE while writing stdin is reported as a block", async () => {
  const guard = createGuard({
    policy: POLICY,
    projectDir: RAPTOR_SRC,
    runHook: async () => {
      const error = new Error("write EPIPE");
      error.code = "EPIPE";
      throw error;
    },
  });
  await register(guard, "s-epipe", "oss-investigator-gh-archive-agent");

  await assert.rejects(
    call(guard, "s-epipe", "bash", { command: "libexec/raptor-bq-query" }),
    /failed to run the bash hook: write EPIPE \(fail-closed\)/,
  );
});

test("handleToolBefore ignores tools that are not bash or webfetch", async () => {
  const guard = createGuard({ policy: null });

  await handleToolBefore(
    { tool: "edit", sessionID: "s-edit" },
    { args: { path: "x" } },
    { policy: null },
  );
  await call(guard, "s-edit", "read", { path: "x" });
});


test("the session registry is bounded and evicts the oldest entry", () => {
  const registry = new SessionRegistry(2);
  registry.remember("s1", "a");
  registry.remember("s2", "b");
  registry.remember("s3", "c");

  assert.equal(registry.size, 2);
  assert.equal(registry.get("s1"), undefined);
  assert.equal(registry.get("s2"), "b");
  assert.equal(registry.get("s3"), "c");

  registry.get("s2");
  registry.remember("s4", "d");
  assert.equal(registry.get("s3"), undefined, "LRU should evict s3, not s2");
});

test("a missing or corrupt policy file fails closed", async () => {
  const root = mkdtempSync(join(tmpdir(), "raptor-policy-"));
  const missing = join(root, "missing.json");
  const corrupt = join(root, "corrupt.json");
  writeFileSync(corrupt, "{not json", "utf8");

  const originalError = console.error;
  console.error = () => {};
  try {
    for (const policyPath of [missing, corrupt]) {
      const guard = createGuard({ policyPath });
      await register(guard, "s-file", "oss-investigator-github-agent");
      await assert.rejects(
        call(guard, "s-file", "bash", { command: "echo hi" }),
        /raptor-policy\.json is missing or invalid/,
      );
    }
  } finally {
    console.error = originalError;
    rmSync(root, { recursive: true, force: true });
  }
});
