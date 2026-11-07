# raptor-opencode

Adapts the RAPTOR autonomous security-research framework to OpenCode.

## What it is

[RAPTOR](https://github.com/gadievron/raptor) is an autonomous security-research
framework originally built around Claude Code. It ships specialised subagents,
slash commands, skills and per-agent security hooks.

`raptor-opencode` does not fork RAPTOR. It reads an unmodified RAPTOR checkout
and generates native OpenCode configuration from it: agents, commands, skills,
permission policy, the guard plugin, `AGENTS.md` and an `opencode.jsonc`
fragment. RAPTOR remains the source of truth; this repository is the adapter.

## Requirements

| Dependency | Minimum | Notes |
|---|---|---|
| Linux or macOS | - | Windows is not tested |
| Python | 3.11 | CLI and RAPTOR hooks |
| git | any recent | clone and pin the RAPTOR checkout |
| Node | 20 | test suite only; the plugin runs on Bun inside OpenCode |
| OpenCode | 2.2 | agent, command, skill and plugin support |

RAPTOR itself may need additional tooling for some commands; see
[Scope and status](#scope-and-status).

## Install

**Option A - the wrapper (no pipx needed).** It checks Python 3.11+, installs
the adapter with `pipx` when available (a local `.venv` otherwise) and forwards
every flag to `raptor-opencode install`:

```bash
git clone https://github.com/atareao/raptor-opencode.git
cd raptor-opencode && ./install.sh --merge-config
# restart OpenCode, then:
raptor-opencode doctor
```

Use `./install.sh --help` for the flag list; `--dry-run` prints the plan without
touching anything, and `--skip-deps` lets the CLI skip RAPTOR's own virtualenv.

**Option B - pipx directly.**

```bash
git clone https://github.com/atareao/raptor-opencode.git
cd raptor-opencode && pipx install . && raptor-opencode install --merge-config
# restart OpenCode, then:
raptor-opencode doctor
```

`install` clones RAPTOR at the pinned ref, creates its virtualenv, generates the
OpenCode artifacts, links them into `~/.config/opencode` and `~/.agents/skills`,
writes the install manifest and merges the config fragment. Use `--dry-run` to
inspect the plan, or `--copy` instead of symlinks.

## What gets installed

| Artifact | Count | Location |
|---|---|---|
| Subagents | 18 | `~/.config/opencode/agents/` |
| Slash commands | 37 | `~/.config/opencode/commands/` |
| Skills | 11 | `~/.agents/skills/` |
| Security hooks | 2 | `plugins/raptor-guard.ts` + `raptor-policy.json` |
| Permissions, environment, instructions | - | `opencode.jsonc` fragment, `AGENTS.md` |

The two hooks enforce the upstream policies: the bash command allowlist and the
webfetch domain allowlist. The generated policy keeps each agent's own rules, so
one agent's permissions are never widened by another's.

## How it works

- **Adapter, not fork.** A Python pipeline parses `.claude/agents`,
  `.claude/commands` and `.claude/skills`, then emits OpenCode-native Markdown,
  JSONC and TypeScript. Updating means moving the checkout to another tag and
  regenerating.
- **Per-agent policy, fail-closed.** Each agent keeps only the tools and hooks
  declared upstream. Missing or malformed policy blocks the tool instead of
  allowing it.
- **Original Python hooks reused.** The TypeScript plugin invokes RAPTOR's own
  `bash-command-allowlist.py` and `webfetch-domain-allowlist.py` with the
  Claude-compatible JSON payload and trusts their exit codes; it does not
  reimplement the rules.
- **Symlinks plus manifest.** Installation links generated artifacts instead of
  copying them by default, records every link in `install-manifest.json`, and
  backs up conflicting user files with a timestamped suffix.

## Mapping Claude Code to OpenCode

| Claude Code / RAPTOR | OpenCode | Transformation |
|---|---|---|
| `.claude/agents/*.md` | `agents/*.md` | frontmatter becomes `description`, `mode: subagent`, `tools` and `permission` |
| `.claude/commands/*.md` | `commands/*.md` | thin wrapper; reads the original and forwards `$ARGUMENTS` verbatim |
| `.claude/skills/**` | `~/.agents/skills/**` | whole skill directory linked; collisions get a `raptor-` prefix |
| `.claude/hooks/*.py` | `plugins/raptor-guard.ts` | plugin executes the original scripts; exit code maps to allow or deny |
| `.claude/settings.json` | `opencode.jsonc` fragment | permissions, environment variables and `PATH` entries |
| `CLAUDE.md` | `AGENTS.md` + instructions | path rewrites only; the upstream text is preserved |

Unknown tool names abort generation rather than being silently dropped.

## Scope and status

This adapter is pinned to RAPTOR `v3.1.0`: 18 agents, 37 commands and 11 skills.
Upstream `main` currently has 39 commands; they are not part of the pinned
contract until a new tag is adopted.

All commands and agents in `v3.1.0` are ported. `/web` and `/fuzz` still require
RAPTOR's external dependencies (semgrep, AFL++) to be installed and available on
`PATH`; the adapter does not bundle them.

## Updating

```bash
raptor-opencode install --ref v3.2.0
raptor-opencode doctor --ref v3.2.0
```

`install` aborts if the RAPTOR checkout has local changes. If the new tag changes
artifact counts or hook names, update the expectations in
`raptor_opencode/doctor.py` in the same commit. See
[`docs/upgrading.md`](docs/upgrading.md) for the full procedure and rollback.

## Documentation

- [`docs/install.md`](docs/install.md) - installation, flags and layout.
- [`docs/hooks.md`](docs/hooks.md) - guard behavior and how to extend it.
- [`docs/port.md`](docs/port.md) - mapping decisions and known limits.
- [`docs/upgrading.md`](docs/upgrading.md) - moving to a new RAPTOR tag.
- [`docs/troubleshooting.md`](docs/troubleshooting.md) - common failures.
- [`docs/release.md`](docs/release.md) - version bump, changelog and tagging.
- [`plans/PLAN-001-ARCHIVED.md`](plans/PLAN-001-ARCHIVED.md) - design plan and status.

## Legal notice

Use only against systems you own or have explicit, documented authorization to
test. You are responsible for complying with applicable law and with the rules of
any engagement. The authors accept no liability for misuse.

RAPTOR is MIT-licensed. This adapter inherits that license and credits the
original authors: Gadi Evron, Daniel Cuthbert, Thomas Dullien, Michael Bargury and John Cartwright.

## License

MIT. RAPTOR and its third-party components retain their own licenses; see the
upstream repository for details.
