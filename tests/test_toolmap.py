"""Tests for the Claude -> OpenCode tool name mapping."""

from __future__ import annotations

import pytest

from raptor_opencode.errors import UnknownToolError
from raptor_opencode.toolmap import CLAUDE_TO_OPENCODE, map_tool, map_tools


@pytest.mark.parametrize(
    ("claude", "opencode"),
    [
        ("Bash", "bash"),
        ("Read", "read"),
        ("Write", "write"),
        ("Edit", "edit"),
        ("Grep", "grep"),
        ("Glob", "glob"),
        ("WebFetch", "webfetch"),
        ("Task", "task"),
        ("NotebookEdit", "edit"),
    ],
)
def test_single_tool_mapping(claude: str, opencode: str) -> None:
    assert map_tool(claude) == opencode


def test_every_claude_tool_in_table_is_covered() -> None:
    for claude, opencode in CLAUDE_TO_OPENCODE.items():
        assert map_tool(claude) == opencode


@pytest.mark.parametrize("unknown", ["Telepathy", "bash", "WebSearch", "Bash(git:*)"])
def test_unknown_tool_raises_with_actionable_message(unknown: str) -> None:
    with pytest.raises(UnknownToolError) as excinfo:
        map_tool(unknown)
    assert unknown in str(excinfo.value)
    assert "known tools" in str(excinfo.value)


def test_map_tools_preserves_order_and_deduplicates() -> None:
    assert map_tools(["Bash", "Read", "Edit", "NotebookEdit", "Grep"]) == [
        "bash",
        "read",
        "edit",
        "grep",
    ]


def test_map_tools_ignores_blank_entries() -> None:
    assert map_tools(["Read", " ", ""]) == ["read"]


def test_map_tool_strips_whitespace() -> None:
    assert map_tool("  Bash  ") == "bash"


def test_unknown_tool_error_is_a_raptor_error() -> None:
    from raptor_opencode.errors import RaptorOpencodeError

    assert issubclass(UnknownToolError, RaptorOpencodeError)
