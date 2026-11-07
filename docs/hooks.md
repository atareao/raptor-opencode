# Hook enforcement

`generate` emits `plugins/raptor-guard.ts` plus `raptor-policy.json`. The plugin
does not reimplement RAPTOR's rules: it runs the original Python hooks
(`bash-command-allowlist.py`, `webfetch-domain-allowlist.py`) with the Claude
`PreToolUse` JSON payload and trusts their exit codes (0 allow, 2 block).

## Session → agent model

`tool.execute.before` receives `{tool, sessionID, callID}` and **no agent**.
The plugin therefore listens to `chat.params` (and, when present, `chat.message`)
to remember `sessionID → agent` in a bounded LRU map (500 entries). If a guarded
tool runs before any agent was reported, the call is blocked. The only escape
hatch is an explicit `"default": {"unrestricted": true}` in
`raptor-policy.json`; the generated default is `false`.

## Fail-closed rules

The guard blocks (throws) when:

- `raptor-policy.json` is missing, unreadable, or corrupt.
- `raptor_home` is not absolute, does not exist, or a hook script is missing.
- The Python hook exits with anything other than `0` or `2`.
- The hook times out (10 s) or spawn fails (`ENOENT`, stale binary…).
- Stdin cannot be written (`EPIPE`) and the hook never produced a verdict.

Empty `bash`/`webfetch` rules mean the agent had no such hook in Claude Code, so
the guard leaves that tool unrestricted (parity). Agents without an entry behave
the same way and log a single warning.

`raptor_home` is baked as an absolute path at generation time and is the source
of truth; `process.env.RAPTOR_HOME` is only a fallback for old policies.
`anchor_file` resolves against the project directory first (Claude semantics),
then against `RAPTOR_HOME`.

Set `RAPTOR_GUARD_LOG=1` to append decisions to
`$RAPTOR_HOME/.claude/run/guard.log`.
