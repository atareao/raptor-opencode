// @ts-check
import { existsSync } from "node:fs";
import { isAbsolute, join } from "node:path";

/**
 * @typedef {Object} WebFetchRule
 * @property {string[]} [domains]
 * @property {boolean} [https_any]
 * @property {string} [anchor_file]
 */

/**
 * @typedef {Object} AgentPolicy
 * @property {string[]} [bash]
 * @property {WebFetchRule} [webfetch]
 */

/**
 * @typedef {Object} PolicyDefault
 * @property {boolean} [unrestricted]
 */

/**
 * @typedef {Object} Policy
 * @property {number} [version]
 * @property {string} [raptor_home]
 * @property {PolicyDefault} [default]
 * @property {Record<string, string>} [hooks]
 * @property {Record<string, AgentPolicy>} [agents]
 */

/**
 * @typedef {AgentPolicy & {hooks: Record<string, string>}} SelectedEntry
 */

/**
 * @typedef {Object} HookInvocation
 * @property {string} command
 * @property {string[]} args
 * @property {"bash" | "webfetch"} tool
 */

/**
 * @typedef {Object} HookResult
 * @property {number | null} [code]
 * @property {string} [stderr]
 */

/**
 * @typedef {Object} Verdict
 * @property {"allow" | "block"} action
 * @property {string} [message]
 */

/**
 * @typedef {Object} ToolPayload
 * @property {"Bash" | "WebFetch"} tool_name
 * @property {{command?: string, url?: string}} tool_input
 */

export const BASH_HOOK = ".claude/hooks/bash-command-allowlist.py";
export const WEBFETCH_HOOK = ".claude/hooks/webfetch-domain-allowlist.py";

/**
 * Return the policy entry for an agent, or null when the agent has no entry.
 * The top-level `hooks` map is attached so callers can resolve hook scripts
 * without passing two objects around.
 *
 * @param {Policy | null | undefined} policy
 * @param {string | undefined} agent
 * @returns {SelectedEntry | null}
 */
export function selectEntry(policy, agent) {
  if (!policy || typeof policy !== "object" || typeof agent !== "string") return null;
  const agents = policy.agents;
  if (!agents || typeof agents !== "object") return null;
  const entry = agents[agent];
  if (!entry || typeof entry !== "object") return null;
  const hooks = policy.hooks && typeof policy.hooks === "object" ? policy.hooks : {};
  return { ...entry, hooks };
}

/**
 * @param {string | undefined} raptorHome
 * @param {string} relative
 * @returns {string}
 */
function resolveHook(raptorHome, relative) {
  if (!relative) return "";
  if (isAbsolute(relative)) return relative;
  return join(raptorHome || "", relative);
}

/**
 * Resolve `anchor_file` against the current project first (Claude semantics),
 * then against RAPTOR_HOME.
 *
 * @param {string} anchor
 * @param {string} raptorHome
 * @param {string | undefined} projectDir
 * @returns {string}
 */
function resolveAnchor(anchor, raptorHome, projectDir) {
  if (isAbsolute(anchor)) return anchor;
  if (projectDir) {
    const candidate = join(projectDir, anchor);
    if (existsSync(candidate)) return candidate;
  }
  return join(raptorHome || "", anchor);
}

/**
 * Build the invocations that reproduce the Python hook command line, without a
 * shell. Returns null when the agent has no restriction for this tool (empty
 * lists mean "no hook in Claude Code", so parity means no restriction).
 *
 * @param {SelectedEntry | null | undefined} entry
 * @param {string | undefined} tool
 * @param {string | undefined} raptorHome
 * @param {string | undefined} [projectDir]
 * @returns {HookInvocation | null}
 */
export function hookInvocation(entry, tool, raptorHome, projectDir) {
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
    const anchor =
      typeof rule.anchor_file === "string" && rule.anchor_file.trim()
        ? rule.anchor_file.trim()
        : null;
    if (domains.length === 0 && !httpsAny && !anchor) return null;

    const script = resolveHook(raptorHome, entry.hooks?.webfetch ?? WEBFETCH_HOOK);
    const args = [script, ...domains];
    if (httpsAny) args.push("--https-any");
    if (anchor) args.push("--anchor-file", resolveAnchor(anchor, raptorHome || "", projectDir));
    return { command: "python3", args, tool: "webfetch" };
  }

  return null;
}

/**
 * Build the Claude-compatible PreToolUse payload the Python hooks expect.
 *
 * @param {string | undefined} tool
 * @param {unknown} args
 * @returns {ToolPayload | null}
 */
export function buildPayload(tool, args) {
  const name = String(tool ?? "").toLowerCase();
  const input = args && typeof args === "object" ? /** @type {Record<string, unknown>} */ (args) : {};
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

/**
 * Translate a hook result into allow/block. Unknown failures fail closed.
 *
 * @param {HookResult | null | undefined} result
 * @returns {Verdict}
 */
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
