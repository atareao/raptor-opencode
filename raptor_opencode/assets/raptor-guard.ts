import { appendFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, join } from "node:path";
import { fileURLToPath } from "node:url";

import {
  buildPayload,
  hookInvocation,
  interpretResult,
  selectEntry,
  type HookInvocation,
  type HookResult,
  type Policy,
  type SelectedEntry,
} from "./raptor-guard-core.mjs";

export const DEFAULT_TIMEOUT_MS = 10_000;
export const MAX_SESSIONS = 500;

const here = dirname(fileURLToPath(import.meta.url));
let policyErrorLogged = false;

export interface ToolInput {
  tool: string;
  sessionID: string;
  callID?: string;
}

export interface ToolOutput {
  args?: Record<string, unknown>;
}

export interface ChatParamsInput {
  sessionID: string;
  agent: string;
}

export interface ChatMessageInput {
  sessionID: string;
  agent?: string;
}

export interface PluginContext {
  directory?: string;
}

export type RunHook = (
  invocation: HookInvocation,
  payload: string,
  timeoutMs: number,
) => Promise<HookResult>;

export interface GuardDeps {
  policy?: Policy | null;
  projectDir?: string;
  raptorHome?: string;
  runHook?: RunHook;
  timeoutMs?: number;
  getAgent?: (sessionID: string) => string | undefined;
  log?: (line: string) => void;
  warnOnce?: (key: string, message: string) => void;
}

export interface GuardOptions extends GuardDeps {
  policyPath?: string;
}

/** Bounded LRU map of `sessionID -> agent`, fed by chat.params/chat.message. */
export class SessionRegistry {
  #agents = new Map<string, string>();
  #limit: number;

  constructor(limit: number = MAX_SESSIONS) {
    this.#limit = Math.max(1, limit);
  }

  remember(sessionID: string | undefined, agent: string | undefined): void {
    if (!sessionID || !agent) return;
    if (this.#agents.has(sessionID)) this.#agents.delete(sessionID);
    this.#agents.set(sessionID, agent);
    while (this.#agents.size > this.#limit) {
      const oldest = this.#agents.keys().next();
      if (oldest.done) break;
      this.#agents.delete(oldest.value);
    }
  }

  get(sessionID: string | undefined): string | undefined {
    if (!sessionID) return undefined;
    const agent = this.#agents.get(sessionID);
    if (agent !== undefined) {
      this.#agents.delete(sessionID);
      this.#agents.set(sessionID, agent);
    }
    return agent;
  }

  get size(): number {
    return this.#agents.size;
  }
}

function errorMessage(error: unknown): string {
  if (error && typeof error === "object" && "message" in error) {
    return String((error as { message?: unknown }).message ?? error);
  }
  return String(error);
}

/**
 * Read the generated policy relative to this plugin (never the cwd). A missing
 * or corrupt file is reported once and makes the guard fail closed on use.
 */
export function readPolicy(policyPath: string = join(here, "..", "raptor-policy.json")): {
  policy: Policy | null;
  error: string | null;
} {
  try {
    const text = readFileSync(policyPath, "utf8");
    const policy = JSON.parse(text) as Policy;
    if (!policy || typeof policy !== "object" || !policy.agents) {
      return {
        policy: null,
        error: `raptor-policy.json at ${policyPath} has no agents map; refusing guarded tools`,
      };
    }
    return { policy, error: null };
  } catch (error) {
    return {
      policy: null,
      error: `cannot load raptor-policy.json at ${policyPath}: ${errorMessage(error)}; refusing guarded tools`,
    };
  }
}

async function runWithBun(
  invocation: HookInvocation,
  payload: string,
  timeoutMs: number,
): Promise<HookResult> {
  const proc = Bun.spawn([invocation.command, ...invocation.args], {
    stdin: "pipe",
    stdout: "ignore",
    stderr: "pipe",
  });

  let timer: ReturnType<typeof setTimeout> | undefined;
  let timedOut = false;
  let stdinError: unknown = null;
  const timeout = new Promise<"timeout">((resolve) => {
    timer = setTimeout(() => {
      timedOut = true;
      try {
        proc.kill("SIGKILL");
      } catch {
        // Best effort: the caller still reports a timeout and fails closed.
      }
      resolve("timeout");
    }, timeoutMs);
  });

  try {
    // Start reading stderr immediately so a chatty hook cannot deadlock on a
    // full pipe while we are still writing stdin.
    const finished = Promise.all([proc.exited, new Response(proc.stderr).text()]);
    try {
      proc.stdin.write(payload);
      await proc.stdin.end();
    } catch (error) {
      stdinError = error;
    }
    const outcome = await Promise.race([finished, timeout]);
    if (outcome === "timeout") {
      return { code: 124, stderr: `raptor-guard: hook timed out after ${timeoutMs}ms` };
    }
    const [code, stderr] = outcome;
    if (stdinError !== null && code === 0) {
      return { code: -1, stderr: `stdin write failed: ${errorMessage(stdinError)}` };
    }
    return { code, stderr };
  } finally {
    if (timer) clearTimeout(timer);
    if (timedOut) {
      try {
        await proc.exited;
      } catch {
        // The process may already be gone; the timeout verdict stands.
      }
    }
  }
}

async function runWithNode(
  invocation: HookInvocation,
  payload: string,
  timeoutMs: number,
): Promise<HookResult> {
  const { spawn } = await import("node:child_process");
  return await new Promise<HookResult>((resolve) => {
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn(invocation.command, invocation.args, {
        stdio: ["pipe", "ignore", "pipe"],
      });
    } catch (error) {
      resolve({ code: -1, stderr: errorMessage(error) });
      return;
    }

    let stderr = "";
    let settled = false;
    let stdinError: unknown = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = (code: number, extra = ""): void => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      resolve({ code, stderr: stderr + extra });
    };

    timer = setTimeout(() => {
      try {
        child.kill("SIGKILL");
      } catch {
        // Report the timeout regardless of whether the kill landed.
      }
      finish(124, `raptor-guard: hook timed out after ${timeoutMs}ms`);
    }, timeoutMs);

    // ENOENT and friends must always win over a late close event.
    child.on("error", (error) => finish(-1, errorMessage(error)));
    child.stderr?.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.on("close", (code) => {
      // Give a pending stdin EPIPE a chance to win over a 0 exit.
      setImmediate(() => {
        if (stdinError !== null) {
          finish(-1, `stdin write failed: ${errorMessage(stdinError)}`);
          return;
        }
        finish(code ?? -1);
      });
    });

    const stdin = child.stdin;
    if (stdin) {
      stdin.on("error", (error) => {
        stdinError = error;
        finish(-1, `stdin write failed: ${errorMessage(error)}`);
      });
      try {
        stdin.write(payload);
        stdin.end();
      } catch (error) {
        stdinError = error;
        finish(-1, `stdin write failed: ${errorMessage(error)}`);
      }
    }
  });
}

/** Execute a hook without a shell. Bun first, Node fallback. */
export async function runHook(
  invocation: HookInvocation,
  payload: string,
  timeoutMs: number = DEFAULT_TIMEOUT_MS,
): Promise<HookResult> {
  if (typeof Bun !== "undefined" && typeof Bun.spawn === "function") {
    return await runWithBun(invocation, payload, timeoutMs);
  }
  return await runWithNode(invocation, payload, timeoutMs);
}

function policyRaptorHome(policy: Policy, override: string | undefined): string {
  const fromPolicy = typeof policy.raptor_home === "string" ? policy.raptor_home : "";
  const candidate = fromPolicy || override || process.env.RAPTOR_HOME || "";
  return candidate;
}

/**
 * Enforce the RAPTOR policy for one tool call. Exported so the handler can be
 * tested with injected dependencies; the plugin factory only wires I/O.
 */
export async function handleToolBefore(
  input: ToolInput,
  output: ToolOutput,
  deps: GuardDeps,
): Promise<void> {
  const tool = String(input?.tool ?? "").toLowerCase();
  if (tool !== "bash" && tool !== "webfetch") return;

  const policy = deps.policy;
  if (!policy || typeof policy !== "object" || !policy.agents) {
    throw new Error(
      `raptor-guard: raptor-policy.json is missing or invalid; refusing the ${tool} call ` +
        "(fail-closed). Regenerate the OpenCode artifacts.",
    );
  }

  const sessionID = String(input?.sessionID ?? "");
  const agent = deps.getAgent ? deps.getAgent(sessionID) : undefined;
  if (!agent) {
    if (policy.default?.unrestricted === true) {
      deps.warnOnce?.(
        `session:${sessionID}`,
        `raptor-guard: no agent registered for session ${sessionID || "<unknown>"}; ` +
          `default.unrestricted=true, allowing ${tool}`,
      );
      return;
    }
    throw new Error(
      `raptor-guard: no agent is registered for session ${sessionID || "<unknown>"}; ` +
        `refusing the ${tool} call (fail-closed). Send a new message so OpenCode reports ` +
        "the agent via chat.params, or set default.unrestricted=true explicitly.",
    );
  }

  const entry: SelectedEntry | null = selectEntry(policy, agent);
  if (!entry) {
    // Parity with Claude Code: an agent without an entry has no hook at all.
    deps.warnOnce?.(
      `agent:${agent}`,
      `raptor-guard: no policy entry for agent ${agent}; allowing ${tool} for parity ` +
        "with Claude Code (the agent had no hook).",
    );
    return;
  }

  const raptorHome = policyRaptorHome(policy, deps.raptorHome);
  if (!raptorHome || !isAbsolute(raptorHome)) {
    throw new Error(
      "raptor-guard: RAPTOR_HOME is not an absolute path; refusing the " +
        `${tool} call (fail-closed). Regenerate the artifacts with a real --raptor-home.`,
    );
  }
  if (!existsSync(raptorHome)) {
    throw new Error(
      `raptor-guard: RAPTOR_HOME points at ${raptorHome}, which does not exist; ` +
        `refusing the ${tool} call (fail-closed). Reinstall RAPTOR or set RAPTOR_HOME.`,
    );
  }

  const invocation = hookInvocation(entry, tool, raptorHome, deps.projectDir);
  if (!invocation) return;

  const script = invocation.args[0] ?? "";
  if (!script || !isAbsolute(script) || !existsSync(script)) {
    throw new Error(
      `raptor-guard: hook script ${script || "<missing>"} not found under ` +
        `RAPTOR_HOME=${raptorHome}; reinstall RAPTOR or regenerate the policy ` +
        "(fail-closed).",
    );
  }

  const payload = buildPayload(tool, output?.args ?? {});
  if (!payload) return;

  let result: HookResult;
  try {
    result = await (deps.runHook ?? runHook)(
      invocation,
      JSON.stringify(payload),
      deps.timeoutMs ?? DEFAULT_TIMEOUT_MS,
    );
  } catch (error) {
    throw new Error(
      `raptor-guard: failed to run the ${tool} hook: ${errorMessage(error)} (fail-closed)`,
    );
  }

  const verdict = interpretResult(result);
  deps.log?.(`${new Date().toISOString()} ${tool} ${verdict.action}${verdict.message ? ` ${verdict.message}` : ""}`);
  if (verdict.action === "block") {
    throw new Error(`raptor-guard: ${verdict.message ?? "blocked by RAPTOR policy"}`);
  }
}

function decisionLogger(raptorHome: string | undefined): (line: string) => void {
  return (line: string): void => {
    if (process.env.RAPTOR_GUARD_LOG !== "1" || !raptorHome) return;
    try {
      const runDir = join(raptorHome, ".claude", "run");
      mkdirSync(runDir, { recursive: true });
      appendFileSync(join(runDir, "guard.log"), `${line}\n`);
    } catch {
      // Logging must never change a decision.
    }
  };
}

export interface GuardHooks {
  "chat.params": (input: ChatParamsInput) => Promise<void>;
  "chat.message": (input: ChatMessageInput) => Promise<void>;
  "tool.execute.before": (input: ToolInput, output: ToolOutput) => Promise<void>;
}

/** Build the hook map with an injected/loaded policy and session registry. */
export function createGuard(options: GuardOptions = {}): GuardHooks {
  const registry = new SessionRegistry(MAX_SESSIONS);
  const warned = new Set<string>();
  const loaded =
    options.policy !== undefined
      ? { policy: options.policy, error: null }
      : readPolicy(options.policyPath);

  if (loaded.error && !policyErrorLogged) {
    policyErrorLogged = true;
    console.error(`raptor-guard: ${loaded.error}`);
  }

  const raptorHome =
    (loaded.policy && typeof loaded.policy.raptor_home === "string"
      ? loaded.policy.raptor_home
      : "") ||
    options.raptorHome ||
    process.env.RAPTOR_HOME ||
    "";

  const deps: GuardDeps = {
    policy: loaded.policy,
    projectDir: options.projectDir,
    raptorHome: options.raptorHome,
    runHook: options.runHook,
    timeoutMs: options.timeoutMs,
    getAgent: (sessionID: string) => registry.get(sessionID),
    log: options.log ?? decisionLogger(raptorHome),
    warnOnce: (key: string, message: string) => {
      if (warned.has(key)) return;
      warned.add(key);
      if (options.warnOnce) options.warnOnce(key, message);
      else console.error(message);
    },
  };

  return {
    "chat.params": async (input: ChatParamsInput): Promise<void> => {
      registry.remember(input?.sessionID, input?.agent);
    },
    "chat.message": async (input: ChatMessageInput): Promise<void> => {
      if (input?.agent) registry.remember(input.sessionID, input.agent);
    },
    "tool.execute.before": async (input: ToolInput, output: ToolOutput): Promise<void> => {
      await handleToolBefore(input, output, deps);
    },
  };
}

export const RaptorGuard = async (ctx: PluginContext): Promise<GuardHooks> =>
  createGuard({ projectDir: ctx?.directory });
