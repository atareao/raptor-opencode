# PLAN-003 - Instalación por proyecto (`--scope project`)

Estado: en curso (implementación y verificación completas; pendiente push/PR).

## Objetivo

Permitir que `raptor-opencode` instale agentes, comandos, skills, plugin y
configuración **dentro de un proyecto OpenCode** en lugar de ensuciar el árbol
global del usuario, y documentar el modelo "one workspace, many targets".

## Tareas

- [x] Resolver el layout `global` / `project` en `raptor_opencode/scope.py`.
- [x] `--scope project` en `install`, `uninstall` y `doctor` (`cli.py`).
- [x] Instalador scoped: enlaces en `.opencode/` y `.agents/skills/`, manifest
      en `.opencode/raptor-manifest.json` y config en `./opencode.json`.
- [x] Rechazar config de proyecto ambigua (varios candidatos de `opencode.json`).
- [x] `uninstall --scope project`: enlaces, backups, config y venv compartido.
- [x] `doctor --scope project`: valida rutas y conteos del layout del proyecto.
- [x] `install.sh` propaga `--scope` / `--project-dir` y valida el directorio.
- [x] Tests de layout, ambigüedad de config, install/uninstall y doctor scoped.
- [x] Docs: `docs/project-workspace.md` y sección en `README.md`.
- [x] Scaffold de `/data/security` y validación real con 18 agentes.
- [ ] Push de `feature/project-scope`, PR a `development`, CI verde y merge.

## Verificación

- `python3 -m pytest -q`: 220 passed.
- `node --test tests_js/*.test.mjs`: 23 passed.
- `npm run typecheck`, `ruff check .`, `ruff format --check .`,
  `shellcheck install.sh`: limpios.
- Instalación real en `/data/security` con `--scope project`: 18 agentes
  visibles desde el workspace, `doctor --scope project --json` con
  `RAPTOR_HOME=/tmp/opencode/raptor-src` → 20 ok, 0 warn, 0 fail, exit 0.

## Notas

- `RAPTOR_OUT_DIR` no admite rutas fuera de `$HOME`; por eso los findings del
  workspace `/data/security` se leen por un symlink a
  `~/.local/share/raptor/out/projects`, no por un directorio real en `/data`.
- El `--raptor-home` usado en la validación es un checkout de prueba en
  `/tmp/opencode/raptor-src`; la instalación estable usaría
  `~/.local/share/raptor`.

Archivado: pendiente.
