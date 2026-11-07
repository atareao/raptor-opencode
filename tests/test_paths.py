"""Tests for path resolution (RAPTOR_HOME, XDG and CLI overrides)."""

from __future__ import annotations

from pathlib import Path

from raptor_opencode.paths import (
    opencode_config_dir,
    raptor_home,
    resolve_paths,
    skills_dir,
)


def _env(home: str = "/home/tester", **extra: str) -> dict[str, str]:
    env = {"HOME": home, **extra}
    return env


def test_defaults_use_home_directory() -> None:
    env = _env()
    assert raptor_home(env=env) == Path("/home/tester/.local/share/raptor")
    assert opencode_config_dir(env=env) == Path("/home/tester/.config/opencode")
    assert skills_dir(env=env) == Path("/home/tester/.agents/skills")


def test_raptor_home_env_override() -> None:
    env = _env(RAPTOR_HOME="/opt/raptor")
    assert raptor_home(env=env) == Path("/opt/raptor")


def test_xdg_data_home_is_respected() -> None:
    env = _env(XDG_DATA_HOME="/data")
    assert raptor_home(env=env) == Path("/data/raptor")


def test_raptor_home_beats_xdg_data_home() -> None:
    env = _env(RAPTOR_HOME="/opt/raptor", XDG_DATA_HOME="/data")
    assert raptor_home(env=env) == Path("/opt/raptor")


def test_xdg_config_home_is_respected() -> None:
    env = _env(XDG_CONFIG_HOME="/cfg")
    assert opencode_config_dir(env=env) == Path("/cfg/opencode")


def test_explicit_paths_beat_environment() -> None:
    env = _env(RAPTOR_HOME="/opt/raptor", XDG_CONFIG_HOME="/cfg")
    assert raptor_home("/srv/raptor", env=env) == Path("/srv/raptor")
    assert opencode_config_dir("/out", env=env) == Path("/out")


def test_empty_environment_values_are_ignored() -> None:
    env = _env(RAPTOR_HOME="  ", XDG_CONFIG_HOME="", XDG_DATA_HOME="")
    assert raptor_home(env=env) == Path("/home/tester/.local/share/raptor")
    assert opencode_config_dir(env=env) == Path("/home/tester/.config/opencode")


def test_tilde_is_expanded() -> None:
    env = _env()
    assert raptor_home("~/raptor", env=env) == Path("/home/tester/raptor")


def test_resolve_paths_builds_every_destination() -> None:
    paths = resolve_paths("/opt/raptor", "/out", env=_env())
    assert paths.raptor_home == Path("/opt/raptor")
    assert paths.opencode_config == Path("/out")
    assert paths.agents == Path("/out/agents")
    assert paths.commands == Path("/out/commands")
    assert paths.plugins == Path("/out/plugins")
    assert paths.skills == Path("/home/tester/.agents/skills")
    assert paths.claude_agents == Path("/opt/raptor/.claude/agents")
    assert paths.claude_commands == Path("/opt/raptor/.claude/commands")
    assert paths.claude_hooks == Path("/opt/raptor/.claude/hooks")


def test_resolve_paths_without_overrides() -> None:
    paths = resolve_paths(env=_env())
    assert paths.raptor_home == Path("/home/tester/.local/share/raptor")
    assert paths.agents == Path("/home/tester/.config/opencode/agents")
