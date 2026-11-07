# Subir de versión de RAPTOR

`raptor-opencode` es un adaptador: no modifica RAPTOR, lo traduce. Actualizar
significa mover el checkout al nuevo tag, regenerar y verificar.

## Antes de empezar

```bash
raptor-opencode doctor                     # línea base: todo en ok
git -C ~/.local/share/raptor status --short # debe estar vacío
```

Si el checkout tiene cambios locales, `install` **aborta**. Haz commit o stash:

```bash
git -C ~/.local/share/raptor stash push -m "antes de subir a vX.Y.Z"
```

## Subir de tag

```bash
# 1. Instala el nuevo ref; clona/actualiza y regenera todo
raptor-opencode install --ref v3.2.0

# 2. Verifica con el mismo ref (doctor lo compara por defecto con v3.1.0)
raptor-opencode doctor --ref v3.2.0

# 3. Reinicia OpenCode para recargar plugin, agentes y skills
```

`install` hace, en orden:

1. `git fetch --tags` y `git checkout --detach <ref>` en el checkout;
2. regenera agentes, comandos, skills, `raptor-policy.json`, el plugin,
   `AGENTS.md` y `opencode.fragment.jsonc`;
3. reemplaza sus propios symlinks (no duplica ni crea backups de lo suyo);
4. escribe el manifiesto con el `ref` nuevo.

Los conteos pueden cambiar entre tags. `doctor` los compara con los de
`v3.1.0` (18 agentes, 37 comandos, 11 skills); si el tag nuevo trae otros,
actualiza las constantes `EXPECTED_AGENTS` / `EXPECTED_COMMANDS` /
`EXPECTED_SKILLS` en `raptor_opencode/doctor.py` en el mismo commit que sube el
`DEFAULT_REF`.

## Qué revisar tras actualizar

| Comprobación | Comando |
|---|---|
| Ref y conteos | `raptor-opencode doctor --ref v3.2.0` |
| Hooks nuevos o cambiados | `git -C ~/.local/share/raptor diff v3.1.0..v3.2.0 -- .claude/hooks .claude/agents` |
| Avisos de política | `python -c 'import json;print(json.load(open("'"$XDG_DATA_HOME"'/raptor-opencode/generated/raptor-policy.json"))["warnings"])'` |
| Skills renombradas | `cat "${XDG_DATA_HOME:-$HOME/.local/share}/raptor-opencode/generated/skills-map.json"` |
| Guard de verdad | un `bash` permitido y otro bloqueado en el agente afectado |

Si un agente declara un hook que el adaptador no conoce, `generate` no falla:
lo anota en `warnings` y sigue. Revísalos: significan que esa restricción no se
está aplicando.

## Rollback

```bash
# Volver al tag anterior
raptor-opencode install --ref v3.1.0
raptor-opencode doctor

# O deshacer la instalación por completo y reinstalar
raptor-opencode uninstall
raptor-opencode install --ref v3.1.0
```

`uninstall` restaura los backups `<ruta>.bak-raptor-opencode-<timestamp>` que
`install` creó al encontrar ficheros ajenos, y elimina solo los enlaces y
copias que siguen siendo suyos.

## Si `install` no puede actualizar

- `has local changes` → commit o stash en el checkout.
- `exists but is not a git checkout` → `--raptor-home` apunta a otro sitio.
- `ref v3.2.0 not found` → `git -C ~/.local/share/raptor fetch --tags`.
- `git checkout --detach` rechazado por ficheros sin seguimiento → muévelos
  fuera del checkout; el adaptador nunca hace `reset --hard`.
- `git pull --ff-only` falla (si actualizas a mano) → el upstream reescribió
  historia o tu rama divergió: no fuerces, clona de nuevo en otro directorio y
  apunta `--raptor-home` ahí.

Ver también [`troubleshooting.md`](troubleshooting.md).
