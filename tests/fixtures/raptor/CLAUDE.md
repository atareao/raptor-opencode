# RAPTOR fixture

Safe operations (install, scan, read, generate): DO IT.

---

## SLASH-COMMAND DISPATCH

1. Read `.claude/commands/<name>.md` frontmatter.
2. When unsure, check `ls libexec/raptor-<name>*`.
3. Project root: `$CLAUDE_PROJECT_DIR`.

| Command | Entry point |
|---|---|
| `/audit` | `libexec/raptor-audit` |
| `/scan` | `python3 raptor.py scan` |

```bash
libexec/raptor-run-lifecycle start audit --target "$TARGET"
~/.claude/settings.json
```

Settings live in `~/.claude/settings.json`; Claude Code reads them.
