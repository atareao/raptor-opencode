# Hook enforcement

RAPTOR restricts `Bash` and `WebFetch` per agent with `PreToolUse` hooks declared
in each agent's frontmatter. Claude Code runs those hooks; OpenCode has plugins.
`raptor-opencode` bridges the two **without reimplementing the rules**:

- `generate` extracts each agent's hook command line into `raptor-policy.json`;
- it emits `plugins/raptor-guard.ts` + `plugins/raptor-guard-core.mjs`;
- before every `bash` or `webfetch` call the plugin runs RAPTOR's original
  Python hook (`.claude/hooks/bash-command-allowlist.py`,
  `.claude/hooks/webfetch-domain-allowlist.py`) with the Claude-compatible JSON
  payload and trusts its exit code.

```
OpenCode tool call
  └─ tool.execute.before           (raptor-guard.ts)
       ├─ sessionID → agent        (chat.params / chat.message map)
       ├─ agent     → policy entry (raptor-policy.json)
       ├─ python3 <hook> <args>    (payload on stdin)
       └─ exit 0 allow · exit 2 block · anything else block
```

## Session → agent map

`tool.execute.before` receives `{tool, sessionID, callID}` and **no agent**. The
guard therefore listens to `chat.params` (and `chat.message` when it carries an
agent) and keeps a bounded LRU map of `sessionID → agent` (500 entries). If a
guarded tool runs before any agent was reported for that session, the call is
blocked. The only escape hatch is an explicit `"default": {"unrestricted":
true}` in `raptor-policy.json`; the generated default is `false`.

## Fail-closed rules

The guard throws (blocks the call) when:

| Situation | Why |
|---|---|
| `raptor-policy.json` missing, unreadable or corrupt | no policy means no evidence the call is allowed |
| `raptor_home` is not absolute, missing, or the hook script is absent | the hook cannot be executed |
| `python3` cannot be spawned (`ENOENT`, stale binary…) | no verdict was produced |
| the hook exits with neither `0` nor `2` | unknown result |
| the hook exceeds the 10 s timeout and is killed (`SIGKILL`) | no verdict was produced |
| stdin write fails (`EPIPE`) before any verdict | the hook never saw the payload |

`raptor_home` is baked as an absolute path at generation time and is the source
of truth; `process.env.RAPTOR_HOME` is only a fallback for older policies.
`anchor_file` resolves against the project directory first (Claude semantics),
then against `RAPTOR_HOME`.

Set `RAPTOR_GUARD_LOG=1` to append decisions to
`$RAPTOR_HOME/.claude/run/guard.log`.

## Empty lists mean "no hook"

`generate` records exactly what each agent declared:

- `"bash": []` — the agent had no Bash `PreToolUse` hook, so the guard leaves
  `bash` unrestricted for it (parity with Claude Code);
- `"webfetch": {"domains": []}` with `https_any: false` and no `anchor_file` —
  same for `webfetch`;
- an agent **with no entry at all** also behaves like that, and logs a single
  warning per agent.

This is deliberate: the adapter must not invent restrictions the upstream
framework never had. If you want a stricter posture, add the hook to the agent
in RAPTOR (see below), not to the plugin.

## Adding your own hook

The pipeline only understands RAPTOR's two hook scripts, and only for `bash`
and `webfetch`. To add a restriction:

1. Write the hook (any language) so it follows the contract: JSON payload on
   stdin, exit `0` to allow, exit `2` to block, stderr is shown to the agent.
2. Declare it in the agent frontmatter under
   `.claude/agents/<name>.md`, e.g.
   ```yaml
   hooks:
     PreToolUse:
       - matcher: Bash
         hooks:
           - type: command
             command: "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/bash-command-allowlist.py my-cli"
   ```
   Use matchers `Bash` or `WebFetch`; anything else is reported in the policy's
   `warnings` and ignored.
3. Regenerate: `raptor-opencode install` (or `generate` if you link by hand).
   The new arguments appear in `raptor-policy.json`; the plugin needs no change.
4. Verify: `raptor-opencode doctor` and try one allowed and one blocked call.

For a **new tool** (for example `edit`), the pipeline has no contract to reuse.
Add it to `raptor_opencode/gen_policy.py` (extract the rule), then to
`handleToolBefore` in `raptor_opencode/assets/raptor-guard.ts` (build the payload
and run the hook), and cover both with tests.

## Limitations

- **Subagents launched through `task`.** A subagent runs in the parent session,
  so `sessionID → agent` still points at the orchestrator. The guard enforces
  the orchestrator's policy, not the subagent's. RAPTOR's own design has the
  same shape: subagents are reached through their slash commands, and the
  commands run under the session that invoked them.
- **Agents without a policy entry** are allowed for parity and only warned
  once. That includes agents you add to `~/.config/opencode/agents/` yourself.
- **Unregistered sessions** are blocked (fail-closed) until OpenCode reports an
  agent for them. If that never happens for your client, set
  `"default": {"unrestricted": true}` knowingly.
- **One process per call.** Every guarded `bash`/`webfetch` spawns `python3`
  and the hook (10 s ceiling). This keeps the policy honest at the cost of a
  few milliseconds per call.
- **`python3` must be on `PATH`** of the OpenCode process, not just your shell.
- **Not a sandbox.** The guard decides allow/block; it cannot stop a tool it
  does not intercept or an agent that writes outside the allowlisted paths
  through a tool with no hook.
