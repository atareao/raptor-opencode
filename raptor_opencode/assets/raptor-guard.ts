import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  buildPayload,
  hookInvocation,
  interpretResult,
  selectEntry,
} from "./raptor-guard-core.mjs";

const TIMEOUT_MS = 10_000;
const here = dirname(fileURLToPath(import.meta.url));

/** Load the generated policy once, relative to this plugin (not the cwd). */
function loadPolicy() {
  try {
    return JSON.parse(readFileSync(join(here, "..", "raptor-policy.json"), "utf8"));
  } catch {
    return null;
  }
}

const policy = loadPolicy();

function logDecision(raptorHome, tool, verdict) {
  if (process.env.RAPTOR_GUARD_LOG !== "1") return;
  try {
    const runDir = join(raptorHome, ".claude", "run");
    mkdirSync(runDir, { recursive: true });
    const suffix = verdict.message ? ` ${verdict.message}` : "";
    const line = `${new Date().toISOString()} ${tool} ${verdict.action}${suffix}\n`;
    appendFileSync(join(runDir, "guard.log"), line);
  } catch {
    // Logging must never turn an allow into a block (or vice versa).
  }
}

async function runWithBun(invocation, payload) {
  const proc = Bun.spawn([invocation.command, ...invocation.args], {
    stdin: "pipe",
    stdout: "ignore",
    stderr: "pipe",
  });
  proc.stdin.write(payload);
  proc.stdin.end();

  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => {
      try {
        proc.kill("SIGKILL");
      } catch {
        // Best effort: the result below still fails closed.
      }
      resolve({ code: 124, stderr: "raptor-guard: hook timed out after 10s" });
    }, TIMEOUT_MS);
  });
  const finished = (async () => {
    const code = await proc.exited;
    const stderr = await new Response(proc.stderr).text();
    return { code, stderr };
  })();
  try {
    return await Promise.race([finished, timeout]);
  } finally {
    clearTimeout(timer);
  }
}

async function runWithNode(invocation, payload) {
  const { spawn } = await import("node:child_process");
  return await new Promise((resolve) => {
    let child;
    try {
      child = spawn(invocation.command, invocation.args, {
        stdio: ["pipe", "ignore", "pipe"],
      });
    } catch (error) {
      resolve({ code: -1, stderr: String(error?.message ?? error) });
      return;
    }

    let stderr = "";
    let done = false;
    const finish = (code, extra = "") => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      resolve({ code, stderr: stderr + extra });
    };
    const timer = setTimeout(() => {
      try {
        child.kill("SIGKILL");
      } catch {
        // Ignore: finish() below reports the timeout.
      }
      finish(124, "raptor-guard: hook timed out after 10s");
    }, TIMEOUT_MS);

    child.on("error", (error) => finish(-1, String(error?.message ?? error)));
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    child.on("close", (code) => finish(code ?? -1));
    child.stdin.on("error", () => {});
    child.stdin.write(payload);
    child.stdin.end();
  });
}

async function runHook(invocation, payload) {
  if (typeof Bun !== "undefined" && typeof Bun.spawn === "function") {
    return await runWithBun(invocation, payload);
  }
  return await runWithNode(invocation, payload);
}

export const RaptorGuard = async (_ctx) => ({
  "tool.execute.before": async (input, output) => {
    const entry = selectEntry(policy, input?.agent);
    if (!entry) return;

    const tool = String(input?.tool ?? "").toLowerCase();
    const raptorHome = process.env.RAPTOR_HOME ?? "";
    const invocation = hookInvocation(entry, tool, raptorHome);
    if (!invocation) return;

    if (!raptorHome || !existsSync(raptorHome)) {
      throw new Error(
        "raptor-guard: RAPTOR_HOME is not set or does not exist; point it at the " +
          "RAPTOR checkout so the Python policy hooks can run",
      );
    }

    const payload = buildPayload(tool, output?.args ?? {});
    if (!payload) return;

    let result;
    try {
      result = await runHook(invocation, JSON.stringify(payload));
    } catch (error) {
      throw new Error(
        "raptor-guard: failed to run hook: " + String(error?.message ?? error),
      );
    }

    const verdict = interpretResult(result);
    logDecision(raptorHome, tool, verdict);
    if (verdict.action === "block") {
      throw new Error("raptor-guard: " + verdict.message);
    }
  },
});
