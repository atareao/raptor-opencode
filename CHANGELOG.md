# Changelog

All notable changes to this project are documented in this file.

## [0.1.0] - 2026-11-07

### Bug fixes

- 🐛 fix(generate): no escribe fuera de out-dir
- 🐛 fix(policy): hornea raptor_home absoluto y documenta listas vacías
- 🐛 fix(plugin): resuelve el agente por sesión y falla cerrado

### CI

- 👷 ci: añade pipeline con python, js, shellcheck y e2e sobre RAPTOR v3.1.0
- 👷 ci(shellcheck): lint explícito de install.sh

### Chores

- 🔧 chore: añade pyproject, extras de desarrollo y estructura del paquete
- 🔧 chore(lint): fija ruff 0.17.4 y corrige RUF043/RUF059

### Documentation

- 📝 docs: añade PLAN-001 del port de RAPTOR a OpenCode
- 📝 docs(install): documenta instalación, actualización y desinstalación
- 📝 docs: documenta hooks, mapeo, actualización y resolución de problemas
- 📝 docs: reescribe README y cierra el estado del PLAN-001
- 📝 docs(install): install.sh como opción A y pipx como opción B

### Features

- ✨ feat(paths): resuelve RAPTOR_HOME y destinos OpenCode con soporte XDG
- ✨ feat(parser): implementa parser tolerante de frontmatter sin dependencias
- ✨ feat(toolmap): mapea tools de Claude a OpenCode y falla ante desconocidas
- ✨ feat(agents): genera subagentes OpenCode desde .claude/agents
- ✨ feat(commands): genera wrappers que pasan $ARGUMENTS verbatim
- ✨ feat(cli): añade subcomandos generate y version con --dry-run
- ✨ feat(policy): extrae política Bash/WebFetch de los hooks de cada agente
- ✨ feat(skills): replica skills con symlink/copy, envuelve ficheros sueltos y resuelve colisiones
- ✨ feat(config): genera opencode.fragment.jsonc y merge_notes.md
- ✨ feat(cli): integra skills, política y config en generate y añade policy
- ✨ feat(plugin): guard de OpenCode que reutiliza los hooks de RAPTOR y falla cerrado
- ✨ feat(agents-md): porta CLAUDE.md a AGENTS.md con rutas reescritas
- ✨ feat(install): añade install/uninstall con manifiesto y merge JSONC idempotente
- ✨ feat(doctor): valida entorno, artefactos, enlaces y config con salida --json
- ✨ feat(install): añade wrapper install.sh sin pipx

### Tests

- ✅ test(adapter): cubre parser, toolmap, agentes, comandos y paths
- ✅ test(guard): hace autocontenidos los tests JS del guard
- ✅ test(install): cubre ramas pipx/venv y propagación de flags


