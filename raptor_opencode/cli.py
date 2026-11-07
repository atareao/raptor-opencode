"""Command line interface for the RAPTOR to OpenCode adapter."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__
from .errors import RaptorOpencodeError
from .gen_agents import load_agents
from .gen_commands import load_commands
from .paths import resolve_paths


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
        help="generate OpenCode agents and commands",
        description="Generate OpenCode agents and commands from RAPTOR's .claude tree.",
    )
    generate.add_argument(
        "--raptor-home",
        type=Path,
        metavar="PATH",
        help="RAPTOR checkout (default: $RAPTOR_HOME or ~/.local/share/raptor)",
    )
    generate.add_argument(
        "--out-dir",
        type=Path,
        metavar="PATH",
        help="OpenCode config root (default: $XDG_CONFIG_HOME/opencode or ~/.config/opencode)",
    )
    generate.add_argument(
        "--dry-run",
        action="store_true",
        help="print the files that would be written without touching the filesystem",
    )
    generate.add_argument(
        "--with-skills",
        action="store_true",
        help="also generate skills (not implemented yet)",
    )
    generate.add_argument(
        "--with-config",
        action="store_true",
        help="also generate the opencode.jsonc fragment (not implemented yet)",
    )
    generate.add_argument(
        "--with-plugin",
        action="store_true",
        help="also generate the raptor-guard plugin (not implemented yet)",
    )
    generate.set_defaults(func=cmd_generate)

    version = subparsers.add_parser("version", help="print the adapter version")
    version.set_defaults(func=cmd_version)

    return parser


def cmd_version(_args: argparse.Namespace) -> int:
    print(f"raptor-opencode {__version__}")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    if args.with_skills:
        generate_skills()
    if args.with_config:
        generate_config()
    if args.with_plugin:
        generate_plugin()

    paths = resolve_paths(raptor_root=args.raptor_home, out_dir=args.out_dir)
    if not (paths.raptor_home / ".claude").is_dir():
        raise RaptorOpencodeError(
            f"no RAPTOR checkout at {paths.raptor_home} (missing .claude/); "
            "pass --raptor-home or set RAPTOR_HOME"
        )

    agents = load_agents(paths.claude_agents)
    commands = load_commands(paths.claude_commands)
    if not agents and not commands:
        raise RaptorOpencodeError(
            f"nothing to generate: no .md files under {paths.claude_agents} or "
            f"{paths.claude_commands}"
        )

    outputs: list[tuple[Path, str]] = [
        (paths.agents / f"{agent.slug}.md", agent.content) for agent in agents
    ]
    outputs.extend((paths.commands / f"{command.slug}.md", command.content) for command in commands)

    for path, content in outputs:
        if args.dry_run:
            print(f"[dry-run] {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(path)

    prefix = "[dry-run] would generate" if args.dry_run else "generated"
    print(f"{prefix} {len(agents)} agents and {len(commands)} commands")
    return 0


def generate_skills(*_args: object, **_kwargs: object) -> None:
    """Placeholder for block 2 of PLAN-001."""
    raise NotImplementedError(
        "skills generation is not implemented yet (PLAN-001 block 2); "
        "run 'generate' without --with-skills"
    )


def generate_config(*_args: object, **_kwargs: object) -> None:
    """Placeholder for block 3 of PLAN-001."""
    raise NotImplementedError(
        "opencode.jsonc generation is not implemented yet (PLAN-001 block 3); "
        "run 'generate' without --with-config"
    )


def generate_plugin(*_args: object, **_kwargs: object) -> None:
    """Placeholder for block 3 of PLAN-001."""
    raise NotImplementedError(
        "the raptor-guard plugin is not implemented yet (PLAN-001 block 3); "
        "run 'generate' without --with-plugin"
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except NotImplementedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except RaptorOpencodeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
