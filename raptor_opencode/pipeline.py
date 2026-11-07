"""Run the whole generation pipeline into a single output directory.

The pipeline never writes outside *out_dir*. Installers may then link or copy
generated artifacts into the user's OpenCode configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import GenerationError
from .gen_agents import load_agents
from .gen_agents_md import DEFAULT_TAG, generate_agents_md
from .gen_commands import load_commands
from .gen_config import write_config
from .gen_plugin import generate_plugin
from .gen_policy import build_policy, write_policy
from .gen_skills import generate_skills, write_skills_map
from .paths import Paths, resolve_paths


@dataclass(frozen=True, slots=True)
class GeneratedTree:
    """Counts and paths produced by :func:`generate_tree`."""

    paths: Paths
    agents: int
    commands: int
    skills: int
    agents_md: Path
    fragment: Path


def generate_tree(
    raptor_home: str | Path,
    out_dir: str | Path,
    *,
    tag: str = DEFAULT_TAG,
    skills_mode: str = "symlink",
    install_home: str | Path | None = None,
    raptor_python: str | Path | None = None,
    plugin_path: str | Path | None = None,
    dry_run: bool = False,
) -> GeneratedTree:
    """Generate agents, commands, skills, policy, plugin, AGENTS.md and config."""
    paths = resolve_paths(raptor_root=raptor_home, out_dir=out_dir)
    agents = load_agents(paths.claude_agents)
    commands = load_commands(paths.claude_commands)
    if not agents and not commands:
        raise GenerationError(
            f"nothing to generate: no .md files under {paths.claude_agents} or "
            f"{paths.claude_commands}"
        )

    skills_map = generate_skills(
        paths.claude_skills,
        paths.generated_skills,
        raptor_home=paths.raptor_home,
        mode=skills_mode,
        dry_run=dry_run,
    )
    if not dry_run:
        write_skills_map(skills_map, paths.skills_map_file)

    for agent in agents:
        _write(paths.agents / f"{agent.slug}.md", agent.content, dry_run)
    for command in commands:
        _write(paths.commands / f"{command.slug}.md", command.content, dry_run)

    if dry_run:
        build_policy(paths.raptor_home)
    else:
        write_policy(paths.raptor_home, paths.policy_file)

    skill_names = [entry["target"] for entry in skills_map["skills"].values()]
    fragment, _notes = write_config(
        paths.raptor_home,
        paths.opencode_config,
        skill_names,
        install_home=install_home,
        raptor_python=raptor_python,
        plugin_path=plugin_path,
        dry_run=dry_run,
    )
    generate_plugin(paths.opencode_config, dry_run=dry_run)
    agents_md = generate_agents_md(
        paths.raptor_home, paths.opencode_config, tag=tag, dry_run=dry_run
    )

    return GeneratedTree(
        paths=paths,
        agents=len(agents),
        commands=len(commands),
        skills=len(skill_names),
        agents_md=agents_md,
        fragment=fragment,
    )


def _write(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
