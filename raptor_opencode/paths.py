"""Resolve RAPTOR and OpenCode filesystem locations.

The adapter never guesses where things live inside the user's home: every path
is derived here, honouring ``XDG_CONFIG_HOME`` / ``XDG_DATA_HOME`` and the
``RAPTOR_HOME`` environment variable, with explicit CLI arguments taking
precedence over the environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RAPTOR_DIRNAME = "raptor"
OPENCODE_DIRNAME = "opencode"


def _home(env: Mapping[str, str]) -> Path:
    """Return the home directory, preferring the value in *env*."""
    value = env.get("HOME")
    if value:
        return Path(value).expanduser()
    return Path.home()


def _expand(raw: str | Path, env: Mapping[str, str]) -> Path:
    """Expand ``~`` using *env* instead of the process environment."""
    text = str(raw)
    if text == "~":
        return _home(env)
    if text.startswith("~/"):
        return _home(env) / text[2:]
    return Path(text).expanduser()


def _env_path(env: Mapping[str, str], key: str) -> Path | None:
    """Return *key* from *env* as a non-empty expanded path, else ``None``."""
    raw = env.get(key)
    if raw is None or not raw.strip():
        return None
    return _expand(raw.strip(), env)


def raptor_home(explicit: str | Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    """Return the RAPTOR checkout directory.

    Precedence: *explicit* (CLI flag) > ``RAPTOR_HOME`` env var >
    ``$XDG_DATA_HOME/raptor`` > ``~/.local/share/raptor``.
    """
    environ = os.environ if env is None else env
    if explicit is not None:
        return _expand(explicit, environ)
    from_env = _env_path(environ, "RAPTOR_HOME")
    if from_env is not None:
        return from_env
    data_home = _env_path(environ, "XDG_DATA_HOME")
    if data_home is not None:
        return data_home / DEFAULT_RAPTOR_DIRNAME
    return _home(environ) / ".local" / "share" / DEFAULT_RAPTOR_DIRNAME


def opencode_config_dir(
    explicit: str | Path | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """Return the OpenCode configuration directory.

    Precedence: *explicit* > ``$XDG_CONFIG_HOME/opencode`` > ``~/.config/opencode``.
    """
    environ = os.environ if env is None else env
    if explicit is not None:
        return _expand(explicit, environ)
    config_home = _env_path(environ, "XDG_CONFIG_HOME")
    if config_home is not None:
        return config_home / OPENCODE_DIRNAME
    return _home(environ) / ".config" / OPENCODE_DIRNAME


def skills_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the global OpenCode skills directory (``~/.agents/skills``)."""
    environ = os.environ if env is None else env
    return _home(environ) / ".agents" / "skills"


@dataclass(frozen=True, slots=True)
class Paths:
    """Every directory the adapter reads from or writes to."""

    raptor_home: Path
    opencode_config: Path
    agents: Path
    commands: Path
    plugins: Path
    skills: Path

    @property
    def claude_agents(self) -> Path:
        """Source directory with Claude subagent definitions."""
        return self.raptor_home / ".claude" / "agents"

    @property
    def claude_commands(self) -> Path:
        """Source directory with Claude slash-command definitions."""
        return self.raptor_home / ".claude" / "commands"

    @property
    def claude_hooks(self) -> Path:
        """Source directory with RAPTOR's Python hook scripts."""
        return self.raptor_home / ".claude" / "hooks"

    @property
    def claude_skills(self) -> Path:
        """Source directory with RAPTOR's skills tree."""
        return self.raptor_home / ".claude" / "skills"

    @property
    def policy_file(self) -> Path:
        """Generated per-agent policy document."""
        return self.opencode_config / "raptor-policy.json"

    @property
    def fragment_file(self) -> Path:
        """Generated OpenCode configuration fragment (JSONC)."""
        return self.opencode_config / "opencode.fragment.jsonc"

    @property
    def merge_notes_file(self) -> Path:
        """Human instructions for merging the fragment."""
        return self.opencode_config / "merge_notes.md"

    @property
    def skills_map_file(self) -> Path:
        """Generated mapping between RAPTOR skills and installed names."""
        return self.opencode_config / "skills-map.json"


def resolve_paths(
    raptor_root: str | Path | None = None,
    out_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Paths:
    """Build every path used by the generator.

    Args:
        raptor_root: Explicit RAPTOR checkout (``--raptor-home``). Falls back to
            the ``RAPTOR_HOME`` env var and then the XDG data directory.
        out_dir: Explicit OpenCode config root (``--out-dir``). When given,
            ``agents``, ``commands`` and ``plugins`` are placed below it.
        env: Environment mapping, for tests. Defaults to ``os.environ``.
    """
    root = raptor_home(raptor_root, env=env)
    config = opencode_config_dir(out_dir, env=env)
    return Paths(
        raptor_home=root,
        opencode_config=config,
        agents=config / "agents",
        commands=config / "commands",
        plugins=config / "plugins",
        skills=skills_dir(env=env),
    )
