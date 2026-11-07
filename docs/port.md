# Port de RAPTOR a OpenCode: mapeo y decisiones

Este documento explica **qué** se traduce, **cómo** y **por qué**. La referencia
es RAPTOR `v3.1.0` (<https://github.com/gadievron/raptor>).

## Tabla de mapeo

| Origen (Claude Code / RAPTOR) | Destino (OpenCode) | Transformación |
|---|---|---|
| `.claude/agents/*.md` (18) | `<out>/agents/*.md` | frontmatter `name`/`description`/`model` → `description`/`mode: subagent`/`model`; `tools` → mapa `tools` con nombres OpenCode; `permission` deniega `bash`/`edit` si el original no las concedía |
| `.claude/commands/*.md` (37) | `<out>/commands/*.md` | wrapper fino: `description` + `argument-hint` + cuerpo que manda leer el fichero original y pasar `$ARGUMENTS` verbatim |
| `.claude/skills/**` (11) | `<out>/skills/**` | symlink (o `--copy`) de cada skill completa; `.md` sueltos se envuelven en `<name>/SKILL.md`; `name`/`description` garantizados; colisiones → prefijo `raptor-` y mapa en `skills-map.json` |
| `.claude/hooks/bash-command-allowlist.py` | `raptor-policy.json` + `plugins/raptor-guard.ts` | no se reescribe: sus argumentos se extraen por agente y el plugin ejecuta el `.py` original |
| `.claude/hooks/webfetch-domain-allowlist.py` | ídem | incluye `--https-any` y `--anchor-file` |
| `.claude/settings.json` (permisos) | `<out>/opencode.fragment.jsonc` | `permissions` con la excepción de skills; `env` con `RAPTOR_HOME`, `CLAUDECODE`, timeouts y `PATH` con `$RAPTOR_HOME/libexec` |
| `CLAUDE.md` | `<out>/AGENTS.md` | se reescriben `$CLAUDE_PROJECT_DIR`, `~/.claude`, `.claude/` relativo y `libexec/` sin prefijo; el texto se conserva |
| `.mcp.json` | `mcp.servers` del fragmento | `url` → `type: remote`; `command`/`args`/`env` → `type: local`; si no existe, se omite |
| `libexec/raptor-*` + paquete Python | sin cambios | el `PATH` de OpenCode los expone; el venv lo crea `install` (o `--skip-deps`) |

Nombres de tools: `Bash→bash`, `Read→read`, `Write→write`, `Edit→edit`,
`Grep→grep`, `Glob→glob`, `WebFetch→webfetch`, `Task→task`. Una tool
desconocida **aborta la generación**; nunca se ignora en silencio.

## Decisiones

### Adaptador, no fork

Mantener un fork de RAPTOR obliga a resolver conflictos en cada release y a
duplicar su política. El adaptador parte de un checkout intacto, genera una
copia en un directorio aparte y enlaza esa copia. Actualizar = mover el checkout
a otro tag y regenerar. El coste es que un cambio de formato upstream requiere
tocar el generador; el beneficio es que la lógica de seguridad (los hooks) es
literalmente la de upstream.

### Tag fijado (`v3.1.0`)

La generación asume el layout de `v3.1.0` (18/37/11). Los conteos y el ref
esperado están en `doctor.py` y en `DEFAULT_REF`. Subir de tag es una decisión
explícita, documentada en [`upgrading.md`](upgrading.md), no un efecto
colateral de `git pull`.

### Symlinks por defecto, `--copy` como escape

Los symlinks mantienen una sola fuente de verdad: `generated/` dentro de
`$XDG_DATA_HOME/raptor-opencode`, y la configuración de OpenCode solo apunta
ahí. Si el sistema de ficheros no permite symlinks (contenedores, algunos
volúmenes), `--copy` duplica; `uninstall` borra únicamente copias que siguen
siendo idénticas byte a byte.

Cada enlace queda en `install-manifest.json`. `uninstall` no adivina: recorre el
manifiesto y restaura backups `<ruta>.bak-raptor-opencode-<timestamp>`.

### La política se extrae, no se reimplementa

Los hooks de RAPTOR son scripts Python con la allowlist en la línea de
comandos. `gen_policy.py` parsea esa línea (frontmatter → `shlex.split`) y
escribe un JSON estable. `raptor-guard.ts` solo traduce payload y exit codes. Si
un hook nuevo o un flag desconocido aparece, se anota en `warnings`; el plugin
no inventa reglas.

### `mode: subagent` y permisos

Los agentes se generan como subagentes: se invocan con `task`, no como agente
principal. `permission` reproduce la restricción de `tools` del original
(`bash: deny`, `edit: deny`) para que OpenCode no ofrezca una tool que Claude
Code no daba.

### Comandos envoltorio, no copias

Copiar el cuerpo de los comandos los desincroniza del checkout. El wrapper
obliga a leer `$RAPTOR_HOME/.claude/commands/<name>.md` y a respetar las reglas
de dispatch de `AGENTS.md`; `$ARGUMENTS` viaja sin filtrar. Es también lo que
permite que `git checkout <otro-tag>` cambie el comportamiento sin regenerar el
comando.

### Config: fragmento + merge conservador

OpenCode no fusiona dos ficheros de configuración. El adaptador emite un
fragmento JSONC y ofrece `install --merge-config`, que inserta claves sin
perder comentarios ni claves ajenas y **aborta** si no puede verificar el
resultado. Nunca reescribe tu configuración sin backup.

### Skills bajo `~/.agents/skills`

OpenCode descubre skills como `<dir>/<name>/SKILL.md`. Se enlaza el directorio
completo (no solo `SKILL.md`) para conservar scripts y rutas relativas. Si el
nombre colisiona con una skill existente, se prefija `raptor-` y el mapa queda
en `skills-map.json`.

## Límites conocidos

- Los subagentes lanzados con `task` heredan la política de la sesión
  (ver [`hooks.md`](hooks.md#limitations)).
- El guard solo intercepta `bash` y `webfetch`: son los dos hooks que RAPTOR
  publica.
- La instalación necesita `git` y `python3`; `doctor` devuelve 2 si faltan.
- `AGENTS.md` se regenera desde `CLAUDE.md`; las ediciones locales se pierden.
