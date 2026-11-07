import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import {
  createGuard,
  handleToolBefore,
  runHook,
  SessionRegistry,
} from "../raptor_opencode/assets/raptor-guard.ts";
import { interpretResult } from "../raptor_opencode/assets/raptor-guard-core.mjs";

const BASH_HOOK = ".claude/hooks/bash-command-allowlist.py";
const WEBFETCH_HOOK = ".claude/hooks/webfetch-domain-allowlist.py";

/**
 * Minimal but faithful stand-in for RAPTOR's `bash-command-allowlist.py`:
 * it reads a PreToolUse payload on stdin and exits 0 (allow) or 2 (block).
 */
const BASH_HOOK_SOURCE = `#!/usr/bin/env python3
import json
import re
import sys

CHAINING = re.compile(r"[;&|\\\`]")


def has_chaining(command: str) -> bool:
    return bool(CHAINING.search(command)) or "$(" in command


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        print("invalid payload on stdin", file=sys.stderr)
        return 2

    tool_input = payload.get("tool_input") or {}
    command = str(tool_input.get("command") or "").strip()
    allowed = [arg for arg in sys.argv[1:] if arg]

    if not command:
        print("empty command", file=sys.stderr)
        return 2
    if has_chaining(command):
        print(f"chaining or substitution is not allowed: {command}", file=sys.stderr)
        return 2
    for prefix in allowed:
        if command == prefix or command.startswith(prefix + " "):
            return 0
    print(f"{command} is not in the allowlist", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
`;

/**
 * Minimal stand-in for RAPTOR's `webfetch-domain-allowlist.py`, same contract:
 * domains arrive as argv, the URL arrives in the stdin payload.
 */
const WEBFETCH_HOOK_SOURCE = `#!/usr/bin/env python3
import json
import sys
from urllib.parse import urlparse


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        print("invalid payload on stdin", file=sys.stderr)
        return 2

    tool_input = payload.get("tool_input") or {}
    url = str(tool_input.get("url") or "")
    args = sys.argv[1:]
    domains = []
    https_any = False
    index = 0
    while index < len(args):
        if args[index] == "--https-any":
            https_any = True
            index += 1
        elif args[index] == "--anchor-file":
            index += 2
        else:
            domains.append(args[index])
            index += 1

    host = urlparse(url).hostname or ""
    if https_any and url.startswith("https://"):
        return 0
    for domain in domains:
        if host == domain or host.endswith("." + domain):
            return 0
    print(f"{host or url} is not in the webfetch allowlist", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
`;

const DEFAULT_AGENTS = {
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
};

function writeHook(raptorHome, relative, source) {
  const path = join(raptorHome, relative);
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, source, "utf8");
  return path;
}

/**
 * Build a self-contained workspace: a throwaway RAPTOR_HOME with real hook
 * scripts implementing the PreToolUse contract, and its own policy file.
 */
function makeWorkspace(t, overrides = {}) {
  const root = mkdtempSync(join(tmpdir(), "raptor-guard-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));

  const raptorHome = join(root, "raptor-home");
  writeHook(raptorHome, BASH_HOOK, BASH_HOOK_SOURCE);
  writeHook(raptorHome, WEBFETCH_HOOK, WEBFETCH_HOOK_SOURCE);

  const policy = {
    version: 1,
    raptor_home: raptorHome,
    default: { unrestricted: false },
    hooks: { bash: BASH_HOOK, webfetch: WEBFETCH_HOOK },
    agents: DEFAULT_AGENTS,
    ...overrides,
  };

  const policyPath = join(root, "raptor-policy.json");
  writeFileSync(policyPath, `${JSON.stringify(policy, null, 2)}\n`, "utf8");

  return { root, raptorHome, policy, policyPath, projectDir: root };
}

async function register(guard, sessionID, agent) {
  await guard["chat.params"]({ sessionID, agent }, {});
}

function call(guard, sessionID, tool, args) {
  return guard["tool.execute.before"]({ tool, sessionID, callID: "call-1" }, { args });
}

test("chat.params registers the agent and an allowlisted bash command passes", async (t) => {
  const { policy, projectDir } = makeWorkspace(t);
  const guard = createGuard({ policy, projectDir });

  await register(guard, "s-allow", "oss-investigator-gh-archive-agent");
  await call(guard, "s-allow", "bash", { command: "libexec/raptor-bq-query --help" });
});

test("bash chaining and commands outside the allowlist are blocked", async (t) => {
  const { policy, projectDir } = makeWorkspace(t);
  const guard = createGuard({ policy, projectDir });
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

test("webfetch allows a known domain and blocks another one", async (t) => {
  const { policy, projectDir } = makeWorkspace(t);
  const guard = createGuard({ policy, projectDir });
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

test("an unknown session fails closed unless default.unrestricted is true", async (t) => {
  const { policy, projectDir } = makeWorkspace(t);
  const strict = createGuard({ policy, projectDir });
  await assert.rejects(
    call(strict, "s-unknown", "bash", { command: "echo hi" }),
    /no agent is registered for session s-unknown/,
  );

  const permissive = createGuard({
    policy: { ...policy, default: { unrestricted: true } },
    projectDir,
  });
  await call(permissive, "s-unknown", "bash", { command: "echo hi" });
});

test("an agent without a policy entry is allowed once and logged once", async (t) => {
  const { policy, projectDir } = makeWorkspace(t, { agents: {} });
  const messages = [];
  const guard = createGuard({
    policy,
    projectDir,
    warnOnce: (_key, message) => messages.push(message),
  });
  await register(guard, "s-parity", "plain-agent");

  await call(guard, "s-parity", "bash", { command: "echo hi" });
  await call(guard, "s-parity", "bash", { command: "echo again" });

  assert.equal(messages.length, 1);
  assert.match(messages[0], /no policy entry for agent plain-agent/);
});

test("a hook that exceeds the timeout is killed and blocked", async (t) => {
  const { root, policy, projectDir } = makeWorkspace(t);
  const sleeping = join(root, "sleep.py");
  writeFileSync(sleeping, "import time\ntime.sleep(5)\n", "utf8");

  const slowPolicy = {
    ...policy,
    hooks: { bash: sleeping },
    agents: { sleeper: { bash: ["anything"], webfetch: { domains: [], https_any: false } } },
  };

  const guard = createGuard({ policy: slowPolicy, projectDir, timeoutMs: 300 });
  await register(guard, "s-timeout", "sleeper");

  const started = Date.now();
  await assert.rejects(
    call(guard, "s-timeout", "bash", { command: "anything" }),
    /timed out after 300ms/,
  );
  assert.ok(Date.now() - started < 2000, "timeout must fire in under 2s");
});

test("a policy whose raptor_home does not exist fails closed", async (t) => {
  const { root, policyPath, projectDir } = makeWorkspace(t);
  const missingHome = join(root, "raptor-home-that-was-moved");
  const broken = {
    version: 1,
    raptor_home: missingHome,
    default: { unrestricted: false },
    hooks: { bash: BASH_HOOK, webfetch: WEBFETCH_HOOK },
    agents: DEFAULT_AGENTS,
  };
  writeFileSync(policyPath, `${JSON.stringify(broken, null, 2)}\n`, "utf8");

  const guard = createGuard({ policyPath, projectDir });
  await register(guard, "s-missing-home", "oss-investigator-gh-archive-agent");

  await assert.rejects(
    call(guard, "s-missing-home", "bash", { command: "libexec/raptor-bq-query --help" }),
    /RAPTOR_HOME points at .*does not exist.*fail-closed/,
  );
});

test("a relative raptor_home fails closed", async (t) => {
  const { policy, projectDir } = makeWorkspace(t, { raptor_home: "relative/raptor" });
  const guard = createGuard({ policy, projectDir });
  await register(guard, "s-relative-home", "oss-investigator-gh-archive-agent");

  await assert.rejects(
    call(guard, "s-relative-home", "bash", { command: "libexec/raptor-bq-query" }),
    /RAPTOR_HOME is not an absolute path.*fail-closed/,
  );
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

test("an EPIPE while writing stdin is reported as a block", async (t) => {
  const { policy, projectDir } = makeWorkspace(t);
  const guard = createGuard({
    policy,
    projectDir,
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

test("a missing or corrupt policy file fails closed", async (t) => {
  const root = mkdtempSync(join(tmpdir(), "raptor-policy-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
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
  }
});
