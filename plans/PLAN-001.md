# PLAN-001 — raptor-opencode: port de RAPTOR a OpenCode

## Objetivo
Crear el repositorio `raptor-opencode` (GitHub `atareao`, público, licencia MIT) que adapta el framework RAPTOR (https://github.com/gadievron/raptor, tag `v3.1.0`) a OpenCode, replicando lo que RAPTOR ofrece hoy en Claude Code: 18 subagentes, 39 comandos, skills, hooks de seguridad, permisos, instrucciones y MCP.

## Decisiones ya tomadas
- Versión objetivo: `v3.1.0` (upstream no publica v2; tags disponibles: v3.0.0, v3.1.0).
- Repo: `atareao/raptor-opencode`, público.
- Alcance: los 39 comandos, 18 agentes, todas las skills y los 3 hooks.
- Instalación: symlinks desde `~/.config/opencode` y `~/.agents/skills` hacia la config generada; `--copy` como fallback.
- Arquitectura: adaptador (generador) sobre un checkout de RAPTOR, NO fork. Mantenimiento: actualizar upstream y regenerar.

## Arquitectura
```
raptor-opencode/
├── PLAN.md -> plans/PLAN-001.md
├── README.md, LICENSE, install.sh, uninstall.sh
├── raptor_opencode/        # paquete Python del adaptador
│   ├── cli.py              # generate | install | uninstall | doctor | version
│   ├── paths.py            # RAPTOR_HOME, destino OpenCode, XDG
│   ├── parse_claude.py     # parser de frontmatter .claude/agents|commands
│   ├── gen_agents.py
│   ├── gen_commands.py
│   ├── gen_skills.py
│   ├── gen_config.py
│   ├── gen_plugin.py
│   ├── toolmap.py          # nombres de tools Claude -> OpenCode
│   └── doctor.py
├── templates/
│   ├── AGENTS.md.j2
│   ├── raptor-guard.ts.j2
│   └── opencode.fragment.jsonc
├── generated/              # salida versionada (agents/, commands/, skills/, plugins/)
├── tests/                  # pytest + fixtures de RAPTOR simulado
└── docs/                   # un .md por feature
```

## Mapeo Claude Code -> OpenCode
| Origen | Destino | Notas |
|---|---|---|
| `.claude/agents/*.md` (18) | `agents/*.md` | frontmatter `name/description/tools/model` -> `description/mode: subagent/tools/permission`; mapear nombres de tools (Bash->bash, Read->read, Write->write, Edit->edit, Grep->grep, Glob->glob, WebFetch->webfetch, Task->task) |
| `.claude/commands/*.md` (39) | `commands/*.md` | wrapper fino: lee `$RAPTOR_HOME/.claude/commands/<name>.md`, aplica las reglas de dispatch de CLAUDE.md y pasa `$ARGUMENTS` verbatim; conservar `description` y `argument-hint` |
| `.claude/skills/**` | `~/.agents/skills/**` | ya son `SKILL.md`; symlink del directorio completo para conservar rutas relativas y scripts; detectar colisiones y prefijar `raptor-` solo si colisiona |
| `.claude/hooks/{bash-command,webfetch-domain,write-path}-allowlist.py` | `plugins/raptor-guard.ts` | el plugin TS invoca los .py originales por stdin con el payload de la tool y traduce exit code a allow/deny; no reimplementar la política |
| `.claude/settings.json` | `opencode.jsonc` (fragmento) | traducir `permissions.allow` a `permissions`; exportar `CLAUDE_ENV_FILE`, `CLAUDECODE=1`, `CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR=1`, `BASH_DEFAULT_TIMEOUT_MS=300000`, `RAPTOR_HOME`; añadir `$RAPTOR_HOME/libexec` al PATH |
| `CLAUDE.md` | `AGENTS.md` + `instructions` | sustituir rutas `.claude/` por `$RAPTOR_HOME/.claude/`; conservar reglas de ejecución literal, dispatch y seguridad |
| `.mcp.json` / SAGE | `mcp.servers` | solo si existe; si no, omitir en silencio |
| `libexec/raptor-*` + paquete Python | sin cambios | se ejecutan tal cual; `install.sh` crea el venv y valida dependencias |

## Tareas
1. **Bootstrap del repo**: README, LICENSE MIT con aviso de autoría original (gadievron et al.), `.gitignore`, `plans/`, symlink `PLAN.md`.
2. **paths.py + cli.py**: resolución de `RAPTOR_HOME` (`~/.local/share/raptor` por defecto, override por env/flag), destinos `~/.config/opencode/{agents,commands,plugins}` y `~/.agents/skills`, subcomandos `generate|install|uninstall|doctor|version`.
3. **parse_claude.py**: parser YAML frontmatter tolerante (sin dependencia obligatoria de PyYAML; fallback propio) con tests de casos reales de RAPTOR.
4. **toolmap.py**: tabla de equivalencia de tools con tests; tools desconocidas -> error explícito de generación, nunca silencio.
5. **gen_agents.py**: 18 agentes. `mode: subagent`; `tools` sólo las permitidas; `permission` para bash/edit cuando el original lo restrinja.
6. **gen_commands.py**: 39 comandos wrapper. Preservar `$ARGUMENTS` sin escapar ni filtrar. Incluir `raptor-version` y los alias `raptor-*`.
7. **gen_skills.py**: symlink de skills; normalizar frontmatter para OpenCode; detectar colisiones; incluir sub-skills (`crash-analysis/*`, `code-understanding/*`, `oss-forensics/*`, `SecOpsAgentKit`).
8. **gen_plugin.py + raptor-guard.ts**: hooks `tool.execute.before` para bash, webfetch, write/edit; invocación a los .py con payload JSON; deny con mensaje accionable; test unitario del traductor de políticas.
9. **gen_config.py**: fragmento `opencode.jsonc` con `permissions` (incluida la excepción que permite skills de RAPTOR pese al `skill * deny` global del usuario), `env`, `instructions`, `mcp`, `command`/`agent` si hiciera falta.
10. **install.sh / uninstall.sh**: idempotentes; clonan/actualizan RAPTOR al tag fijado; crean venv e instalan requirements; generan; symlinkean; backup de ficheros previos con sufijo `.bak-raptor-opencode-<ts>`; `--copy` y `--dry-run`.
11. **doctor.py**: comprueba RAPTOR_HOME, tag correcto, symlinks, PATH de libexec, venv, permisos de skills, resolución de agentes/comandos, hooks cargados.
12. **Tests**: pytest sobre fixtures (árbol `.claude` sintético) + integración real contra `v3.1.0` en CI; casos: frontmatter raro, tool desconocida, colisión de skills, comando sin `dispatch`, hook que deniega, hook que permite.
13. **CI GitHub Actions**: ruff + pytest en ubuntu, instalación en `HOME` temporal, `doctor` verde, shellcheck de los scripts.
14. **Smoke test end-to-end** con OpenCode real: `/version`, `/scan --help`, carga de un subagente, skill visible, hook que bloquea un dominio no permitido.
15. **Docs**: `docs/port.md` (mapeo y límites), `docs/hooks.md` (política y cómo extenderla), `docs/troubleshooting.md`, `docs/upgrading.md` (subir de tag de RAPTOR).
16. **Cierre**: marcar tareas, `docs/` completo, renombrar a `plans/PLAN-001-ARCHIVED.md`.

## Riesgos y mitigaciones
- Scripts que asumen `CLAUDECODE` o `~/.claude`: shim de env + `doctor` que detecta rutas duras; documentar parches si aparecen.
- Divergencia al actualizar RAPTOR: `generated/` versionado + diff en CI; regenerar es una tarea de un comando.
- Hooks: si OpenCode no expone el payload equivalente, degradar a allowlist propia en TS y marcar la diferencia en `docs/hooks.md`.
- Skills bloqueadas globalmente: el fragmento de config incluye la excepción; `doctor` falla con mensaje claro si sigue bloqueado.
- Superficie ofensiva: README con aviso de uso autorizado y responsabilidad; LICENSE MIT heredada.

## Criterios de aceptación
1. `./install.sh` en máquina limpia deja OpenCode con 39 comandos, 18 agentes, todas las skills y los 3 hooks activos.
2. `doctor` sale 0 y reporta versión de RAPTOR y conteos.
3. `/scan --help` ejecuta el CLI real de RAPTOR.
4. Un webfetch a dominio no permitido y un write fuera de allowlist son bloqueados por el plugin.
5. `git pull && ./install.sh` actualiza sin duplicar ni romper symlinks.
6. CI verde: ruff, pytest, shellcheck, doctor.