# PLAN-002 - `install.sh` wrapper

Estado: completo.

## Objetivo

Dar una vía de instalación sin `pipx`: un wrapper bash mínimo que instale el
adaptador y delegue en `raptor-opencode install`.

## Tareas

- [x] `install.sh` con `set -euo pipefail`, comprobación de Python 3.11+,
      `--help` propio y reenvío literal de argumentos.
- [x] Rama `pipx` (`pipx install --force .`) y rama venv local
      (`python3 -m venv .venv` + `pip install -e .`).
- [x] `--dry-run` no instala nada: ejecuta `python3 -m raptor_opencode install`.
- [x] Mensajes accionables e idempotencia (reutiliza `.venv`, `pipx --force`).
- [x] `tests/test_install_sh.py` (9 tests: `bash -n`, `--help`, propagación de
      flags, rama venv, Python antiguo, dry-run, código de salida, sin
      `pyproject.toml`).
- [x] `shellcheck install.sh` explícito en CI.
- [x] README y `docs/install.md`: `./install.sh` como opción A, pipx como B.

## Verificación

Suites verdes: `pytest -q` (199), `node --test tests_js/*.test.mjs` (23),
`npm run typecheck`, `ruff check .`, `ruff format --check .`,
`shellcheck install.sh`.

Prueba funcional en `/tmp/opencode/verify` con `HOME` y `XDG_*` sandboxeados:
`./install.sh --raptor-home /tmp/opencode/raptor-src --skip-deps --yes
--merge-config` → exit 0; `doctor --json` → 20 ok, 0 warn, 0 fail, exit 0;
`uninstall` → 68 enlaces eliminados, 1 backup restaurado, `opencode.jsonc`
idéntico al original y `.agents/` eliminado.

## Notas

- Con `--skip-deps` el CLI avisa `RAPTOR_PYTHON will not be set` y no crea el
  venv de RAPTOR: sus `requirements.txt` no se instalan. El lanzador principal
  (`python3 raptor.py --help|doctor|describe`) funciona igualmente, pero los
  paquetes opcionales no: p. ej. `packages/cve_diff` falla con
  `ModuleNotFoundError: No module named 'typer'`.

Archivado: 2026-11-07
