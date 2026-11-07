# Instalación de raptor-opencode

`raptor-opencode` traduce un checkout de [RAPTOR](https://github.com/gadievron/raptor)
a la configuración nativa de OpenCode: agentes, comandos, skills, política del
guard, plugin de hooks y un `AGENTS.md` derivado del `CLAUDE.md` upstream.

El instalador **no parchea RAPTOR**: clona (o reutiliza) el checkout tal cual
está publicado, genera una copia adaptada y enlaza esa copia en tu
configuración de OpenCode. RAPTOR sigue siendo la fuente de verdad.

## Requisitos

| Dependencia | Versión mínima | Uso |
|---|---|---|
| `git` | cualquiera reciente | clonar y actualizar el checkout de RAPTOR |
| Python | 3.11+ | ejecutar `raptor-opencode` y los hooks de RAPTOR |
| OpenCode | v2.2+ | cargar agentes, comandos, skills y el plugin `raptor-guard` |
| `node` / `bun` | runtime del plugin | ejecutar los hooks Python desde OpenCode |

`pip install raptor-opencode` deja el comando `raptor-opencode` en el `PATH`.

## Instalación

Opción A, el wrapper incluido en el repo (no necesita `pipx`): comprueba
Python 3.11+, instala el adaptador con `pipx` si está disponible (si no, crea
`.venv/` en el checkout) y reenvía **todos** los flags a `raptor-opencode install`.

```bash
./install.sh --merge-config      # o: ./install.sh --help
```

Opción B, `pipx` directamente:

```bash
pipx install . && raptor-opencode install
```

Por defecto:

| Ruta | Valor |
|---|---|
| Checkout de RAPTOR | `~/.local/share/raptor` |
| Ref upstream | `v3.1.0` |
| Artefactos generados | `$XDG_DATA_HOME/raptor-opencode/generated` |
| Config de OpenCode | `~/.config/opencode` |
| Skills globales | `~/.agents/skills` |
| Manifiesto | `$XDG_DATA_HOME/raptor-opencode/install-manifest.json` |

Pasos que ejecuta:

1. **Checkout** — si el directorio no existe, `git clone --depth 1 --branch <ref>`.
   Si existe, comprueba `git status --porcelain`: con cambios locales **aborta
   sin tocar nada** (nunca hace `reset`). Si está limpio, `git fetch --tags` y
   `git checkout --detach <ref>`.
2. **Dependencias** — crea `$XDG_DATA_HOME/raptor-opencode/venv` e instala
   `<RAPTOR_HOME>/requirements.txt`. Guarda la ruta del intérprete como
   `RAPTOR_PYTHON` en el fragmento de configuración. Con `--skip-deps` se omite
   (y se avisa de que `RAPTOR_PYTHON` no quedará definido).
3. **Generación** — ejecuta el pipeline completo dentro de `--generated-dir`
   (agentes, comandos, skills, `raptor-policy.json`, plugin, `AGENTS.md` y
   `opencode.fragment.jsonc`).
4. **Enlaces** — crea symlinks en `--opencode-dir` y `--skills-dir`. Antes de
   tocar un destino ocupado por algo que no es nuestro, lo renombra a
   `<ruta>.bak-raptor-opencode-<timestamp>`.
5. **Manifiesto** — registra enlaces, backups, ref y timestamps.
6. **Config** — escribe `opencode.fragment.jsonc` y muestra el snippet. Con
   `--merge-config` fusiona el fragmento en `opencode.jsonc`.

Al terminar, reinicia OpenCode para que recargue la configuración.

### Flags

```bash
raptor-opencode install \
  --raptor-home ~/.local/share/raptor \
  --ref v3.1.0 \
  --generated-dir ~/.local/share/raptor-opencode/generated \
  --opencode-dir ~/.config/opencode \
  --skills-dir ~/.agents/skills \
  [--copy] [--dry-run] [--yes] [--merge-config] [--skip-deps]
```

- `--copy` copia en vez de enlazar. El `uninstall` borra solo las copias que
  siguen siendo idénticas byte a byte a lo generado; si las editaste, las
  conserva.
- `--dry-run` no escribe nada: solo valida y muestra el plan.
- `--yes` evita el prompt interactivo (obligatorio en CI o sin TTY).
- `--merge-config` fusiona el fragmento preservando comentarios y claves
  ajenas. Si el `opencode.jsonc` no se puede parsear, **aborta sin escribir**.
- `--skip-deps` no crea venv ni instala `requirements.txt`.

### Invariante de escritura

`generate` (y el paso de generación del instalador) escribe **solo** dentro de
`--out-dir` / `--generated-dir`. `install` es el único que escribe en
`--opencode-dir` y `--skills-dir`, y siempre a través del manifiesto. Nada
toca rutas globales fuera de esos tres destinos declarados.

## Actualización

```bash
raptor-opencode install --ref v3.2.0
```

Si el checkout tiene cambios locales, la orden se aborta. Haz commit o stash
antes de actualizar. La segunda ejecución es idempotente: no crea backups
nuevos ni duplica enlaces, y reemplaza los que ya son nuestros.

## Desinstalación

```bash
raptor-opencode uninstall
```

Lee `install-manifest.json` y:

- borra solo los symlinks que apuntan a nuestros destinos;
- en modo `--copy`, borra solo copias intactas;
- restaura los backups `<ruta>.bak-raptor-opencode-<timestamp>`;
- restaura (o elimina, si lo creó el instalador) `opencode.jsonc`;
- elimina los directorios que creó el instalador si quedaron vacíos;
- **nunca** borra directorios de usuario con contenido.

`--dry-run` informa de lo que haría sin tocar el disco.

## Qué NO hace

- No parchea, mueve ni modifica ficheros dentro del checkout de RAPTOR.
- No escribe en `~/.claude` ni usa hooks de Claude Code.
- No instala RAPTOR globalmente por su cuenta ni añade binarios a tu `PATH`
  del shell (el `PATH` de RAPTOR se inyecta vía `env` de OpenCode).
- No sobrescribe tu configuración sin backup: cualquier colisión se renombra
  antes y queda registrada en el manifiesto.

## Solución de problemas

| Síntoma | Causa / solución |
|---|---|
| `has local changes` | El checkout está sucio; haz commit o stash. |
| `exists but is not a git checkout` | `--raptor-home` apunta a un directorio ajeno. |
| `refusing a non-interactive install without --yes` | Añade `--yes` (CI, cron). |
| `no install manifest` | No hay instalación previa en `--generated-dir`. |
| El guard bloquea todo | Revisa `raptor-policy.json`; es *fail-closed* por diseño (ver `docs/hooks.md`). |
