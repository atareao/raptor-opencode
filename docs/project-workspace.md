# One workspace, many targets

`raptor-opencode` can install RAPTOR **globally** (once per user) or **per
project** (`--scope project`). For security work the per-project model is the
recommended one: a single OpenCode workspace holds the agents, commands, skills
and configuration, while RAPTOR's `/project` registry keeps each target isolated.

## Why one workspace

- One OpenCode session sees RAPTOR's 18 agents, 37 commands and 11 skills, but
  the artifacts do not leak into every unrelated project on the machine.
- Targets are isolated by RAPTOR's own `/project` machinery, not by copying the
  adapters into every repository.
- Findings never enter the workspace repository: they live in RAPTOR's output
  tree, outside the project.

## Layout

```text
/data/security/                  # example workspace
├── opencode.json                # project config (generated fragment merged)
├── .opencode/
│   ├── agents/                  # 18 agent symlinks
│   ├── commands/                # 37 command symlinks
│   ├── generated/               # AGENTS.md fragment, policy, plugin
│   ├── plugins/                 # raptor-guard.ts symlink
│   └── raptor-manifest.json     # links, backups and config record
├── .agents/skills/              # 11 skill symlinks
├── targets/                     # marker only; clones do NOT go here
└── findings -> ~/.local/share/raptor/out/projects
```

The adapter keeps everything inside the workspace except the shared Python
virtualenv, which lives under `$XDG_DATA_HOME/raptor-opencode/venvs/<slug>`.

`opencode.json` is the canonical project config. The installer refuses to run
when several OpenCode project config files exist (`opencode.json`,
`opencode.jsonc`, `.opencode/opencode.json`, `.opencode/opencode.jsonc`),
because OpenCode would load all of them and later files would win.

## Install

```bash
cd /data/security
/path/to/raptor-opencode/install.sh \
  --scope project \
  --raptor-home ~/.local/share/raptor \
  --merge-config
```

`--skip-deps` reuses an existing RAPTOR checkout without building its virtualenv.
Restart OpenCode and verify from the workspace:

```bash
opencode debug agents
raptor-opencode doctor --scope project --json
```

`doctor` exits non-zero on any failed check. Remember to point it at the same
checkout used at install time (`--raptor-home`, or `RAPTOR_HOME` in the
environment); otherwise it inspects the default `~/.local/share/raptor`.

## The target cycle

1. **Create** a target: `/project create <name> --target <repo-or-url>`.
   RAPTOR registers `~/.raptor/projects/<name>.json` and creates
   `<RAPTOR_HOME>/out/projects/<name>/` with a `repo/` clone.
2. **Analyze** with the RAPTOR commands (`/scan`, `/agentic`, `/codeql`, ...).
   Output goes to the active project's directory.
3. **Switch** with `/project switch <name>` (or `/project use <name>`) when
   working on another target.
4. **Close** with `/project close` when the engagement ends. The registry entry
   and output stay on disk until purged.

One target, one name, one output directory. Do not reuse a project name for a
different repository: the registry and the `findings` merge assume a stable
target.

## Where findings live

RAPTOR derives its output base from the checkout that runs it:

```text
<RAPTOR_HOME>/out/projects/<name>/repo/       # cloned target
<RAPTOR_HOME>/out/projects/<name>/findings/   # merged findings
```

With the default checkout that is
`~/.local/share/raptor/out/projects/<name>/`. The workspace's `findings`
symlink should point at the *base* directory (`.../out/projects`), so each
project appears as `findings/<name>/`.

If you install against a different checkout (for example a test copy under
`/tmp`), RAPTOR writes there instead. Update the symlink or move the checkout so
the workspace keeps pointing at the live output tree.

## `RAPTOR_OUT_DIR` limitation

`RAPTOR_OUT_DIR` can relocate the output base, but RAPTOR rejects paths outside
`$HOME`. A workspace under `/data` therefore **cannot** host the real findings
directory, even though it can host the adapters and the repository. This is why
`findings` is a symlink into the home directory rather than a real directory
next to `opencode.json`.

`RAPTOR_HOME` itself may live anywhere (including `/tmp` for a disposable
checkout); only the output override is restricted.

## Purging one target

Deleting a target is destructive. Review first, then confirm explicitly:

```text
/project status                     # list runs for the active project
/project delete <name> --purge --yes
```

Without `--purge`, only the registry entry is removed and the output tree stays
orphaned. `--purge` refuses to delete output outside the expected base, or while
a live run is using it; `--force` overrides the live-run check, not the path
containment.

To remove a target manually, delete both sides:

```bash
rm -rf ~/.local/share/raptor/out/projects/<name>
rm -f ~/.raptor/projects/<name>.json
```

Never delete a whole `out/projects` tree: other targets share that base.

## Uninstalling the workspace

From the workspace, with the same scope and project directory:

```bash
raptor-opencode uninstall --scope project --dry-run
raptor-opencode uninstall --scope project
```

Uninstall reads `.opencode/raptor-manifest.json`, removes the symlinks and the
generated tree, deletes the shared virtualenv for that workspace, and restores
the previous `opencode.json` (or deletes it when the installer created it). It
does not touch `AGENTS.md`, `README.md`, `targets/`, `findings`, RAPTOR's
registry or the target output directories.
