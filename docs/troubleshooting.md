# Solución de problemas

Empieza siempre por:

```bash
raptor-opencode doctor            # o --json para procesarlo
```

Cada línea `[fail]`/`[warn]` dice qué revisar. Esta página cubre los fallos más
frecuentes, con causa y arreglo.

## 1. Las skills de RAPTOR no aparecen (bloqueadas globalmente)

**Síntoma.** `doctor` marca `[fail] config-skills`; en OpenCode no se ve ninguna
skill de RAPTOR aunque `skills-map.json` las lista.

**Causa.** Tu `opencode.jsonc` deniega todas las skills:

```jsonc
"permissions": [
  { "action": "skill", "resource": "*", "effect": "deny" }
]
```

OpenCode evalúa la lista **en orden** y la última regla que coincide manda. La
excepción que genera el adaptador (`raptor-*` y cada skill por nombre) tiene que
ir **después** del `deny`, no antes.

**Arreglo.**

```jsonc
"permissions": [
  { "action": "skill", "resource": "*", "effect": "deny" },
  { "action": "skill", "resource": "raptor-*", "effect": "allow" }
]
```

O vuelve a fusionar el fragmento generado:

```bash
raptor-opencode install --merge-config --yes
```

## 2. `RAPTOR_HOME` no es absoluto

**Síntoma.** `[fail] raptor-home ... which is relative` o el guard bloquea todo
con `RAPTOR_HOME is not an absolute path`.

**Causa.** `RAPTOR_HOME` se exportó como `~/.local/share/raptor` o `./raptor`.
Las `~` no se expanden dentro de la configuración de OpenCode y el plugin
resuelve las rutas con `path.isAbsolute()`.

**Arreglo.**

```bash
export RAPTOR_HOME="$HOME/.local/share/raptor"
# y reexportado con ruta literal en el JSON
```

Vuelve a generar (`raptor-opencode install`) para que la política hornee la ruta
absoluta.

## 3. Hook no ejecutable

**Síntoma.** `[warn] hooks ... is not executable` o `Permission denied` al
invocar el hook.

**Causa.** El checkout se copió sin bits de ejecución (zip, `cp -r` sin `-p`,
volumen montado con `noexec`).

**Arreglo.**

```bash
chmod +x "$RAPTOR_HOME"/.claude/hooks/*-allowlist.py
```

El plugin invoca los hooks con `python3 <script>`, así que el bit no es
estrictamente necesario; aun así, el aviso indica que el checkout no está en el
estado que publica upstream.

## 4. Política corrupta: el guard bloquea *todo*

**Síntoma.** Cualquier `bash`/`webfetch` falla con
`raptor-policy.json is missing or invalid` o `cannot parse`.

**Causa.** `raptor-policy.json` se editó a mano, se truncó una escritura o se
borró el directorio generado. El guard es fail-closed por diseño
(ver [`hooks.md`](hooks.md)).

**Arreglo.**

```bash
raptor-opencode doctor            # muestra el error exacto
raptor-opencode install           # regenera la política
```

Si estás depurando y necesitas trabajar sin policía, edita el JSON y pon
`"default": {"unrestricted": true}` de forma consciente; no lo dejes así.

## 5. El plugin no se carga

**Síntoma.** `[fail] config-plugin`: el `opencode.jsonc` no lista
`raptor-guard.ts`. Ningún hook se aplica y no hay errores.

**Causa.** Instalaste con `generate` (que no toca tu configuración) o el merge
quedó pendiente.

**Arreglo.**

```bash
raptor-opencode install --merge-config --yes
```

Comprueba que `plugin` contiene una ruta al `raptor-guard.ts` generado (absoluta
si instalaste con `--generated-dir` fuera de `~/.config/opencode`) y reinicia
OpenCode. Si el plugin anterior se cargó desde una ruta que ya no existe,
OpenCode lo ignora en silencio.

## 6. JSONC inválido

**Síntoma.** `[fail] config cannot parse ...` o `install --merge-config` aborta
con `unexpected data at offset N`.

**Causa.** Coma final fuera de un objeto/array, comentario sin cerrar, o texto
pegado después del objeto raíz. El parser acepta `//`, `/* */` y comas finales,
pero nada más.

**Arreglo.** Restaura el backup creado por el instalador:

```bash
ls ~/.config/opencode/opencode.jsonc.bak-raptor-opencode-*
mv ~/.config/opencode/opencode.jsonc.bak-raptor-opencode-<ts> ~/.config/opencode/opencode.jsonc
```

y vuelve a intentarlo, o corrige la sintaxis a mano. `doctor` valida el archivo
sin escribirlo.

## 7. Fallos de git al instalar o actualizar

**Síntoma.** `has local changes`, `exists but is not a git checkout`,
`ref v3.1.0 not found`, o un `git pull --ff-only` que no avanza.

**Causa.** El adaptador nunca descarta trabajo: exige un checkout limpio, del
ref pedido y con el tag disponible. `--ff-only` falla si el upstream reescribió
historia o si tu rama local divergió.

**Arreglo.**

```bash
git -C "$RAPTOR_HOME" status --short      # ¿sucio? commit o stash
git -C "$RAPTOR_HOME" fetch --tags        # ¿falta el tag?
raptor-opencode install --ref v3.1.0      # checkout --detach del ref
```

Si el checkout está roto o es de otro proyecto, clona de nuevo en otro
directorio y usa `--raptor-home`. No fuerces el fast-forward: es la señal de que
los historiales no son comparables.

## 8. `webfetch` bloqueado para todo (anchor file)

**Síntoma.** Un agente con `--anchor-file` recibe
`anchor file ... is missing or unreadable; blocking the WebFetch call` en cada
fetch, incluso a dominios que deberían permitirse.

**Causa.** El modo anchor deriva la allowlist de un fichero que el orquestador
debe escribir **antes** de lanzar el agente. Si no existe, está vacío o su
primera línea no es https, el hook bloquea (fail-closed). Además, el plugin
resuelve `anchor_file` contra el directorio del proyecto y luego contra
`RAPTOR_HOME`, así que una ruta relativa puede apuntar a otro sitio del que
esperas.

**Arreglo.** Escribe el ancla antes de despachar:

```bash
mkdir -p .claude/run
printf 'https://github.com/advisories\n' > .claude/run/web.anchor
```

Cada decisión se registra en `<anchor>.log` (fecha, veredicto, host, URL).
Revisa también `raptor-policy.json`: `webfetch.anchor_file` dice la ruta exacta
que el guard va a usar.
