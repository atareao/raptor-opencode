"""Emit the OpenCode guard plugin that enforces RAPTOR's per-agent policy.

The plugin is a thin I/O layer over ``raptor-guard-core.mjs``: it loads the
generated ``raptor-policy.json``, runs RAPTOR's original Python hooks with a
JSON payload on stdin and translates exit codes into allow/block. Nothing here
touches the filesystem outside ``<out-dir>/plugins``.
"""

from __future__ import annotations

from pathlib import Path

from .errors import GenerationError

PLUGIN_NAME = "raptor-guard.ts"
CORE_NAME = "raptor-guard-core.mjs"
ASSETS = Path(__file__).with_name("assets")


def generate_plugin(out_dir: str | Path, *, dry_run: bool = False) -> tuple[Path, Path]:
    """Write ``plugins/raptor-guard.ts`` and its core under *out_dir*.

    Returns the plugin and core paths. Only ``<out_dir>/plugins`` is written.
    """
    plugins = Path(out_dir) / "plugins"
    plugin_path = plugins / PLUGIN_NAME
    core_path = plugins / CORE_NAME
    plugin_text = _read_asset(PLUGIN_NAME)
    core_text = _read_asset(CORE_NAME)
    if dry_run:
        return plugin_path, core_path

    plugins.mkdir(parents=True, exist_ok=True)
    plugin_path.write_text(plugin_text, encoding="utf-8")
    core_path.write_text(core_text, encoding="utf-8")
    return plugin_path, core_path


def _read_asset(name: str) -> str:
    path = ASSETS / name
    if not path.is_file():
        raise GenerationError(f"missing plugin asset {path}")
    return path.read_text(encoding="utf-8")


__all__ = ["CORE_NAME", "PLUGIN_NAME", "generate_plugin"]
