# raptor-opencode

Port del framework RAPTOR (https://github.com/gadievron/raptor) a OpenCode.

Ver `plans/PLAN-001.md`.

## How hooks are enforced

`generate` emits `plugins/raptor-guard.ts`, an OpenCode plugin that reuses
RAPTOR's original Python hooks (`bash-command-allowlist.py` and
`webfetch-domain-allowlist.py`) instead of reimplementing their rules. Before
each `bash` or `webfetch` call it runs the matching hook for the current agent
with the Claude-compatible JSON payload and trusts its exit code; any unexpected
failure (spawn error, timeout, missing `RAPTOR_HOME`) blocks the tool, so the
guard always fails closed.
