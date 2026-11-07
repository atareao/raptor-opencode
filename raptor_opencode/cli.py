"""Command line interface for the RAPTOR to OpenCode adapter."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .errors import RaptorOpencodeError
from .gen_agents import load_agents
from .gen_agents_md import detect_tag, generate_agents_md
from .gen_commands import load_commands
from .gen_config import write_config
from .gen_plugin import generate_plugin
from .gen_policy import build_policy, write_policy
from .gen_skills import DEFAULT_MODE, generate_skills, write_skills_map
from .installer import DEFAULT_REF, install, uninstall
from .paths import Paths, resolve_paths


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the ``raptor-opencode`` entry point."""
    parser = argparse.ArgumentParser(
        prog="raptor-opencode",
        description="Generate native OpenCode configuration from a RAPTOR checkout.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "generate",
        help="generate agents, commands, skills, policy and config",
        description=(
            "Generate OpenCode agents, commands, skills, the per-agent policy and "
            "the configuration fragment from RAPTOR's .claude tree."
        ),
    )
    _add_source_args(generate)
    generate.add_argument(
        "--skills-mode",
        choices=("symlink", "copy"),
        default=DEFAULT_MODE,
        help="how to install skills under <out-dir>/skills (default: symlink)",
    )
    generate.add_argument(
        "--collision-dir",
        type=Path,
        metavar="PATH",
        help=(
            "read-only directory to check for skill name collisions "
            "(default: none; never points at the global skills directory)"
        ),
    )
    generate.add_argument(
        "--no-config",
        action="store_true",
        help="skip opencode.fragment.jsonc and merge_notes.md",
    )
    generate.set_defaults(func=cmd_generate)

    policy = subparsers.add_parser(
        "policy",
        help="emit only raptor-policy.json",
        description="Extract the per-agent Bash/WebFetch policy without generating files.",
    )
    _add_source_args(policy)
    policy.set_defaults(func=cmd_policy)

    install_parser = subparsers.add_parser(
        "install",
        help="install RAPTOR into the user's OpenCode configuration",
        description="Clone RAPTOR, generate its OpenCode port and link the artifacts.",
    )
    install_parser.add_argument(
        "--raptor-home",
        type=Path,
        metavar="PATH",
        help="RAPTOR checkout (default: $RAPTOR_HOME or ~/.local/share/raptor)",
    )
    install_parser.add_argument("--ref", default=DEFAULT_REF, help="upstream git ref to install")
    install_parser.add_argument(
        "--generated-dir",
        type=Path,
        metavar="PATH",
        help="generated artifacts (default: $XDG_DATA_HOME/raptor-opencode/generated)",
    )
    install_parser.add_argument(
        "--opencode-dir",
        type=Path,
        metavar="PATH",
        help="OpenCode config dir (default: $XDG_CONFIG_HOME/opencode)",
    )
    install_parser.add_argument(
        "--skills-dir",
        type=Path,
        metavar="PATH",
        help="global skills dir (default: ~/.agents/skills)",
    )
    install_parser.add_argument("--copy", action="store_true", help="copy instead of symlinking")
    install_parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    install_parser.add_argument("--yes", action="store_true", help="do not prompt")
    install_parser.add_argument(
        "--merge-config",
        action="store_true",
        help="merge the fragment into <opencode-dir>/opencode.jsonc",
    )
    install_parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="skip venv creation and pip install",
    )
    install_parser.set_defaults(func=cmd_install)

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="remove artifacts recorded in install-manifest.json",
    )
    uninstall_parser.add_argument("--generated-dir", type=Path, metavar="PATH")
    uninstall_parser.add_argument("--opencode-dir", type=Path, metavar="PATH")
    uninstall_parser.add_argument("--skills-dir", type=Path, metavar="PATH")
    uninstall_parser.add_argument("--dry-run", action="store_true")
    uninstall_parser.set_defaults(func=cmd_uninstall)

    version = subparsers.add_parser("version", help="print the adapter version")
    version.set_defaults(func=cmd_version)

    return parser


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--raptor-home",
        type=Path,
        metavar="PATH",
        help="RAPTOR checkout (default: $RAPTOR_HOME or ~/.local/share/raptor)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        metavar="PATH",
        help="OpenCode config root (default: $XDG_CONFIG_HOME/opencode or ~/.config/opencode)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the files that would be written without touching the filesystem",
    )


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"raptor-opencode {__version__}")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    paths = _resolve_and_validate(args, require_content=True)
    skills_dir = paths.generated_skills

    agents = load_agents(paths.claude_agents)
    commands = load_commands(paths.claude_commands)
    if not agents and not commands:
        raise RaptorOpencodeError(
            f"nothing to generate: no .md files under {paths.claude_agents} or "
            f"{paths.claude_commands}"
        )

    previous_map = _load_previous_map(paths.skills_map_file)
    skills_map = generate_skills(
        paths.claude_skills,
        skills_dir,
        raptor_home=paths.raptor_home,
        mode=args.skills_mode,
        collision_dir=args.collision_dir,
        previous_map=previous_map,
        dry_run=args.dry_run,
    )

    for agent in agents:
        _emit(paths.agents / f"{agent.slug}.md", agent.content, args.dry_run)
    for command in commands:
        _emit(paths.commands / f"{command.slug}.md", command.content, args.dry_run)

    for entry in skills_map["skills"].values():
        target = skills_dir / entry["target"]
        if args.dry_run:
            print(f"[dry-run] {target}")
        else:
            print(target)
    if not args.dry_run:
        write_skills_map(skills_map, paths.skills_map_file)

    if args.dry_run:
        policy = build_policy(paths.raptor_home)
        print(f"[dry-run] {paths.policy_file}")
    else:
        policy = write_policy(paths.raptor_home, paths.policy_file)
        print(paths.policy_file)

    if not args.no_config:
        skill_names = [entry["target"] for entry in skills_map["skills"].values()]
        fragment_path, notes_path = write_config(
            paths.raptor_home,
            paths.opencode_config,
            skill_names,
            dry_run=args.dry_run,
        )
        for path in (fragment_path, notes_path):
            print(f"[dry-run] {path}" if args.dry_run else path)

    plugin_path, core_path = generate_plugin(paths.opencode_config, dry_run=args.dry_run)
    for path in (plugin_path, core_path):
        print(f"[dry-run] {path}" if args.dry_run else path)

    agents_md = generate_agents_md(
        paths.raptor_home, paths.opencode_config, tag=detect_tag(paths.raptor_home),
        dry_run=args.dry_run,
    )
    print(f"[dry-run] {agents_md}" if args.dry_run else agents_md)

    for warning in policy.get("warnings", []):
        print(f"warning: {warning}", file=sys.stderr)

    prefix = "[dry-run] would generate" if args.dry_run else "generated"
    skill_count = len(skills_map["skills"])
    print(
        f"{prefix} {len(agents)} agents, {len(commands)} commands, "
        f"{skill_count} skills and 2 plugin files"
    )
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    result = install(
        raptor_home=args.raptor_home,
        ref=args.ref,
        generated=args.generated_dir,
        opencode=args.opencode_dir,
        skills=args.skills_dir,
        copy=args.copy,
        dry_run=args.dry_run,
        yes=args.yes,
        merge_config=args.merge_config,
        skip_deps=args.skip_deps,
    )
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    prefix = "[dry-run] would install" if args.dry_run else "installed"
    print(
        f"{prefix} {result.agents} agents, {result.commands} commands, "
        f"{result.skills} skills"
    )
    print(f"generated: {result.generated_dir}")
    print(f"opencode:  {result.opencode_dir}")
    print(f"skills:    {result.skills_dir}")
    print(f"manifest:  {result.manifest_path}")
    if not args.dry_run:
        fragment = result.generated_dir / "opencode.fragment.jsonc"
        if fragment.is_file():
            print(f"fragment:  {fragment}")
            print(fragment.read_text(encoding="utf-8"))
    print("Restart OpenCode to pick up the new configuration.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    result = uninstall(
        generated=args.generated_dir,
        opencode=args.opencode_dir,
        skills=args.skills_dir,
        dry_run=args.dry_run,
    )
    action = "would remove" if args.dry_run else "removed"
    print(
        f"{action} {result.removed_links} links and restored "
        f"{result.restored_backups} backups"
    )
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    paths = _resolve_and_validate(args, require_content=False)
    if args.dry_run:
        print(f"[dry-run] {paths.policy_file}")
        return 0
    policy = write_policy(paths.raptor_home, paths.policy_file)
    print(paths.policy_file)
    for warning in policy.get("warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _resolve_and_validate(args: argparse.Namespace, *, require_content: bool) -> Paths:
    paths = resolve_paths(raptor_root=args.raptor_home, out_dir=args.out_dir)
    if not (paths.raptor_home / ".claude").is_dir():
        raise RaptorOpencodeError(
            f"no RAPTOR checkout at {paths.raptor_home} (missing .claude/); "
            "pass --raptor-home or set RAPTOR_HOME"
        )
    if require_content and not paths.claude_agents.is_dir() and not paths.claude_commands.is_dir():
        raise RaptorOpencodeError(
            f"nothing to generate: no .claude/agents or .claude/commands under {paths.raptor_home}"
        )
    return paths


def _load_previous_map(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _emit(path: Path, content: str, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(path)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except RaptorOpencodeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
