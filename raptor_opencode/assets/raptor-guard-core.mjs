import { isAbsolute, join } from "node:path";

export const BASH_HOOK = ".claude/hooks/bash-command-allowlist.py";
export const WEBFETCH_HOOK = ".claude/hooks/webfetch-domain-allowlist.py";

/**
 * Return the policy entry for an agent, or null when the agent has no entry.
 * The top-level `hooks` map is attached so callers can resolve hook scripts
 * without passing two objects around.
 */
export function selectEntry(policy, agent) {
  if (!policy || typeof policy !== "object" || typeof agent !== "string") return null;
  const agents = policy.agents;
  if (!agents || typeof agents !== "object") return null;
  const entry = agents[agent];
  if (!entry || typeof entry !== "object") return null;
  return { ...entry, hooks: policy.hooks && typeof policy.hooks === "object" ? policy.hooks : {} };
}

/** Resolve a hook path against RAPTOR_HOME, leaving absolute paths untouched. */
function resolveHook(raptorHome, relative) {
  const path = String(relative ?? "");
  if (!path) return "";
  if (isAbsolute(path)) return path;
  return join(raptorHome || "", path);
}

/**
 * Build the invocations that reproduce the Python hook command line, without a
 * shell. Returns null when the agent has no restriction for this tool.
 */
export function hookInvocation(entry, tool, raptorHome) {
  const name = String(tool ?? "").toLowerCase();
  if (!entry || typeof entry !== "object") return null;

  if (name === "bash") {
    const prefixes = Array.isArray(entry.bash) ? entry.bash.map(String).filter(Boolean) : [];
    if (prefixes.length === 0) return null;
    const script = resolveHook(raptorHome, entry.hooks?.bash ?? BASH_HOOK);
    return { command: "python3", args: [script, ...prefixes], tool: "bash" };
  }

  if (name === "webfetch") {
    const rule = entry.webfetch;
    if (!rule || typeof rule !== "object") return null;
    const domains = Array.isArray(rule.domains) ? rule.domains.map(String).filter(Boolean) : [];
    const httpsAny = rule.https_any === true;
    const anchor = typeof rule.anchor_file === "string" && rule.anchor_file.trim()
      ? rule.anchor_file.trim()
      : null;
    if (domains.length === 0 && !httpsAny && !anchor) return null;

    const script = resolveHook(raptorHome, entry.hooks?.webfetch ?? WEBFETCH_HOOK);
    const args = [script, ...domains];
    if (httpsAny) args.push("--https-any");
    if (anchor) args.push("--anchor-file", resolveHook(raptorHome, anchor));
    return { command: "python3", args, tool: "webfetch" };
  }

  return null;
}

/** Build the Claude-compatible PreToolUse payload the Python hooks expect. */
export function buildPayload(tool, args) {
  const name = String(tool ?? "").toLowerCase();
  const input = args && typeof args === "object" ? args : {};
  if (name === "bash") {
    const command = typeof input.command === "string" ? input.command : "";
    return { tool_name: "Bash", tool_input: { command } };
  }
  if (name === "webfetch") {
    const url = typeof input.url === "string" ? input.url : "";
    return { tool_name: "WebFetch", tool_input: { url } };
  }
  return null;
}

/** Translate a hook result into allow/block. Unknown failures fail closed. */
export function interpretResult(result) {
  const code = result && typeof result === "object" ? result.code : undefined;
  const stderr = result && typeof result.stderr === "string" ? result.stderr.trim() : "";

  if (code === 0) return { action: "allow" };
  if (code === 2) {
    return { action: "block", message: stderr || "blocked by RAPTOR policy" };
  }
  const detail = stderr || "no stderr";
  const exit = code === undefined || code === null ? "spawn failure" : `exit ${code}`;
  return { action: "block", message: `raptor hook failed (${exit}): ${detail}` };
}
