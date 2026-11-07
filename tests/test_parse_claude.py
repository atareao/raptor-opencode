"""Tests for the tolerant frontmatter parser."""

from __future__ import annotations

import pytest

from raptor_opencode.errors import FrontmatterError
from raptor_opencode.parse_claude import dump_frontmatter, parse_frontmatter


def test_simple_frontmatter() -> None:
    text = "---\nname: audit-reviewer\nmodel: inherit\n---\n\nBody line.\n"
    frontmatter, body = parse_frontmatter(text)
    assert frontmatter == {"name": "audit-reviewer", "model": "inherit"}
    assert body == "Body line.\n"


def test_inline_list() -> None:
    text = "---\ntools: Read, Grep, Glob, Bash\n---\nbody\n"
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["tools"] == ["Read", "Grep", "Glob", "Bash"]


def test_inline_list_with_single_value() -> None:
    frontmatter, _ = parse_frontmatter("---\ntools: Bash\n---\n")
    assert frontmatter["tools"] == ["Bash"]


def test_block_list_indented() -> None:
    text = "---\ntools:\n  - Read\n  - Grep\n---\n"
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["tools"] == ["Read", "Grep"]


def test_block_list_at_key_indent() -> None:
    text = "---\ntools:\n- Read\n- Grep\nmodel: inherit\n---\n"
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["tools"] == ["Read", "Grep"]
    assert frontmatter["model"] == "inherit"


def test_flow_sequence() -> None:
    frontmatter, _ = parse_frontmatter('---\ntools: [Read, "Grep"]\n---\n')
    assert frontmatter["tools"] == ["Read", "Grep"]


def test_quoted_values_and_escapes() -> None:
    text = "---\ndescription: \"Line one\\nLine two: with colon\"\nother: 'It''s fine'\n---\n"
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["description"] == "Line one\nLine two: with colon"
    assert frontmatter["other"] == "It's fine"


def test_plain_scalar_keeps_literal_escapes() -> None:
    # RAPTOR's offsec-specialist stores literal backslash-n in an unquoted value.
    text = "---\ndescription: includes:\\n\\n<example> text\n---\n"
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["description"] == "includes:\\n\\n<example> text"


def test_nested_hooks_mapping() -> None:
    text = (
        "---\n"
        "name: fetcher\n"
        "hooks:\n"
        "  PreToolUse:\n"
        "    - matcher: WebFetch\n"
        "      hooks:\n"
        "        - type: command\n"
        '          command: "run --flag"\n'
        "---\n"
    )
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["hooks"] == {
        "PreToolUse": [
            {"matcher": "WebFetch", "hooks": [{"type": "command", "command": "run --flag"}]}
        ]
    }


def test_no_frontmatter_returns_empty_dict_and_text() -> None:
    text = "# Title\n\nNo frontmatter here.\n"
    frontmatter, body = parse_frontmatter(text)
    assert frontmatter == {}
    assert body == text


def test_body_may_contain_separator_lines() -> None:
    text = "---\ndescription: x\n---\n\nSection\n\n---\n\nMore\n"
    frontmatter, body = parse_frontmatter(text)
    assert frontmatter == {"description": "x"}
    assert body == "Section\n\n---\n\nMore\n"


def test_unterminated_frontmatter_raises() -> None:
    with pytest.raises(FrontmatterError, match="unterminated"):
        parse_frontmatter("---\nname: broken\n\nbody without closing marker\n")


def test_crlf_is_normalised() -> None:
    frontmatter, body = parse_frontmatter("---\r\nname: x\r\n---\r\n\r\nbody\r\n")
    assert frontmatter == {"name": "x"}
    assert body == "body\n"


def test_continuation_line_of_plain_scalar() -> None:
    text = "---\ndescription: first part\n  second part\nname: x\n---\n"
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["description"] == "first part second part"
    assert frontmatter["name"] == "x"


def test_empty_frontmatter() -> None:
    frontmatter, body = parse_frontmatter("---\n---\nbody\n")
    assert frontmatter == {}
    assert body == "body\n"


def test_dump_and_parse_roundtrip() -> None:
    data = {
        "description": 'Has: colon and "quotes"',
        "mode": "subagent",
        "tools": {"read": True, "grep": True},
        "permission": {"bash": "deny"},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command"}]}]},
    }
    parsed, _ = parse_frontmatter(dump_frontmatter(data) + "\n\nbody\n")
    assert parsed == data


def test_dump_handles_nested_first_key_in_sequence() -> None:
    data = {"hooks": [{"run": {"command": "x"}, "name": "y"}]}
    parsed, _ = parse_frontmatter(dump_frontmatter(data) + "\n")
    assert parsed == data


def test_literal_block_scalar() -> None:
    text = "---\ndescription: |\n  line one\n  line two\nname: x\n---\n"
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["description"] == "line one\nline two"
    assert frontmatter["name"] == "x"


def test_folded_block_scalar() -> None:
    text = "---\ndescription: >\n  line one\n  line two\n---\n"
    frontmatter, _ = parse_frontmatter(text)
    assert frontmatter["description"] == "line one line two"
