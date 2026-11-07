"""Tests for agent generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from raptor_opencode.errors import GenerationError, UnknownToolError
from raptor_opencode.gen_agents import agent_from_file, load_agents
from raptor_opencode.parse_claude import parse_frontmatter


def _agent(raptor_fixture: Path, filename: str):
    return agent_from_file(raptor_fixture / ".claude" / "agents" / filename)


def test_agent_with_bash_is_generated_as_subagent(raptor_fixture: Path) -> None:
    agent = _agent(raptor_fixture, "audit-reviewer.md")
    assert agent.slug == "audit-reviewer"

    frontmatter, body = parse_frontmatter(agent.content)
    assert frontmatter["mode"] == "subagent"
    assert frontmatter["description"].startswith("Hypothesis-driven code review")
    assert frontmatter["tools"] == {"read": True, "grep": True, "glob": True, "bash": True}
    # Bash is allowed but edit/write are not, so only edit is denied.
    assert frontmatter["permission"] == {"edit": "deny"}
    assert "tool-grounded analysis" in body


def test_agent_without_bash_gets_explicit_deny(raptor_fixture: Path) -> None:
    agent = _agent(raptor_fixture, "crash-report-fetcher.md")
    frontmatter, _ = parse_frontmatter(agent.content)
    assert frontmatter["tools"] == {"read": True, "write": True, "webfetch": True}
    assert frontmatter["permission"] == {"bash": "deny"}
    # Claude-only hook syntax is not copied into the OpenCode agent.
    assert "hooks" not in frontmatter


def test_agent_with_write_allowed_does_not_deny_edit(raptor_fixture: Path) -> None:
    agent = _agent(raptor_fixture, "oss-report-generator-agent.md")
    frontmatter, _ = parse_frontmatter(agent.content)
    assert frontmatter["tools"] == {"read": True, "write": True}
    assert frontmatter["permission"] == {"bash": "deny"}


def test_slug_comes_from_name_not_filename(raptor_fixture: Path) -> None:
    agent = _agent(raptor_fixture, "coverage-analysis-generator-agent.md")
    assert agent.slug == "coverage-analyzer"


def test_agent_without_tools_gets_no_tools_or_permission(raptor_fixture: Path) -> None:
    agent = _agent(raptor_fixture, "offsec-specialist.md")
    frontmatter, body = parse_frontmatter(agent.content)
    assert "tools" not in frontmatter
    assert "permission" not in frontmatter
    assert frontmatter["mode"] == "subagent"
    assert "offensive security specialist" in body


def test_generation_is_deterministic(raptor_fixture: Path) -> None:
    first = _agent(raptor_fixture, "audit-reviewer.md").content
    second = _agent(raptor_fixture, "audit-reviewer.md").content
    assert first == second
    assert first.endswith("\n")


def test_unknown_tool_aborts_generation(tmp_path: Path) -> None:
    source = tmp_path / "bogus.md"
    source.write_text(
        "---\nname: bogus\ndescription: x\ntools: Bash, Telepathy\n---\nbody\n",
        encoding="utf-8",
    )
    with pytest.raises(UnknownToolError, match="Telepathy"):
        agent_from_file(source)


def test_missing_description_aborts_generation(tmp_path: Path) -> None:
    source = tmp_path / "nodesc.md"
    source.write_text("---\nname: nodesc\n---\nbody\n", encoding="utf-8")
    with pytest.raises(GenerationError, match="description"):
        agent_from_file(source)


def test_duplicate_slugs_abort_generation(tmp_path: Path) -> None:
    for filename in ("one.md", "two.md"):
        (tmp_path / filename).write_text(
            "---\nname: same-name\ndescription: x\n---\nbody\n", encoding="utf-8"
        )
    with pytest.raises(GenerationError, match="duplicate agent slug"):
        load_agents(tmp_path)


def test_load_agents_is_sorted_by_filename(raptor_fixture: Path) -> None:
    agents = load_agents(raptor_fixture / ".claude" / "agents")
    assert [agent.source.name for agent in agents] == sorted(
        path.name for path in (raptor_fixture / ".claude" / "agents").glob("*.md")
    )


def test_missing_agents_dir_returns_empty_list(tmp_path: Path) -> None:
    assert load_agents(tmp_path / "does-not-exist") == []
