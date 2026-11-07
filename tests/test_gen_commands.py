"""Tests for command wrapper generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from raptor_opencode.errors import GenerationError
from raptor_opencode.gen_commands import command_from_file, load_commands, render_command
from raptor_opencode.parse_claude import parse_frontmatter


def _command(raptor_fixture: Path, filename: str):
    return command_from_file(raptor_fixture / ".claude" / "commands" / filename)


def test_command_with_direct_dispatch(raptor_fixture: Path) -> None:
    command = _command(raptor_fixture, "scan.md")
    assert command.slug == "scan"

    frontmatter, body = parse_frontmatter(command.content)
    assert frontmatter == {"description": "Scan a repository with Semgrep and CodeQL"}
    assert "$RAPTOR_HOME/.claude/commands/scan.md" in body
    assert "$ARGUMENTS" in body
    assert "verbatim" in body
    assert "Declared dispatch: `python3 raptor.py scan`" in body
    # The original instructions are not copied; the wrapper points at them.
    assert "Run the scan and summarize the findings." not in body


def test_command_with_skill_dispatch(raptor_fixture: Path) -> None:
    command = _command(raptor_fixture, "audit.md")
    frontmatter, body = parse_frontmatter(command.content)
    assert frontmatter["description"] == (
        "Systematic hypothesis-driven security review of coverage gaps"
    )
    assert "Declared dispatch: `skill`" in body
    assert "multi-step workflow" in body
    # Claude-only metadata is not carried over.
    assert "user-invocable" not in frontmatter


def test_argument_hint_is_preserved(raptor_fixture: Path) -> None:
    command = _command(raptor_fixture, "annotate.md")
    frontmatter, _ = parse_frontmatter(command.content)
    assert frontmatter["argument-hint"] == "<subcommand> [args]"


def test_arguments_are_never_rewritten(raptor_fixture: Path) -> None:
    command = _command(raptor_fixture, "ask.md")
    _, body = parse_frontmatter(command.content)
    assert "no filtering" in body
    assert "no added flags" in body
    assert "$ARGUMENTS" in body
    assert "libexec/raptor-llm-ask $ARGUMENTS" in body


def test_slug_comes_from_filename(tmp_path: Path) -> None:
    source = tmp_path / "MyWeird_Name.md"
    source.write_text(
        "---\nname: ignored\ndescription: x\ndispatch: skill\n---\nbody\n",
        encoding="utf-8",
    )
    assert command_from_file(source).slug == "myweird-name"


def test_missing_description_raises(tmp_path: Path) -> None:
    source = tmp_path / "nodesc.md"
    source.write_text("---\ndispatch: skill\n---\nbody\n", encoding="utf-8")
    with pytest.raises(GenerationError, match="description"):
        command_from_file(source)


def test_missing_dispatch_is_reported_in_body(tmp_path: Path) -> None:
    rendered = render_command({"description": "x"}, "noop")
    assert "Declared dispatch: `missing" in rendered


def test_duplicate_command_slugs_abort(tmp_path: Path) -> None:
    for filename in ("A.md", "a.md"):
        (tmp_path / filename).write_text(
            "---\ndescription: x\ndispatch: skill\n---\nbody\n", encoding="utf-8"
        )
    with pytest.raises(GenerationError, match="duplicate command slug"):
        load_commands(tmp_path)


def test_load_commands_sorted_and_complete(raptor_fixture: Path) -> None:
    commands = load_commands(raptor_fixture / ".claude" / "commands")
    assert [command.slug for command in commands] == [
        "annotate",
        "ask",
        "audit",
        "scan",
        "version",
    ]


def test_output_is_deterministic_and_ends_with_newline(raptor_fixture: Path) -> None:
    first = _command(raptor_fixture, "scan.md").content
    second = _command(raptor_fixture, "scan.md").content
    assert first == second
    assert first.endswith("\n")


def test_missing_commands_dir_returns_empty_list(tmp_path: Path) -> None:
    assert load_commands(tmp_path / "missing") == []
