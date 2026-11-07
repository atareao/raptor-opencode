"""Tests for per-agent policy extraction (``gen_policy``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor_opencode.errors import GenerationError
from raptor_opencode.gen_policy import build_policy, write_policy


def _write_agent(root: Path, name: str, body: str) -> Path:
    agents = root / ".claude" / "agents"
    agents.mkdir(parents=True, exist_ok=True)
    path = agents / f"{name}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _agent(name: str, hooks: str | None) -> str:
    lines = ["---", f"name: {name}", f"description: {name} agent"]
    if hooks is not None:
        lines.append("hooks:")
        lines.extend(f"  {line}" if line else line for line in hooks.splitlines())
    lines.extend(["---", "", f"# {name}", ""])
    return "\n".join(lines)


def test_extracts_bash_prefixes_and_web_fetch_hosts(tmp_path: Path) -> None:
    root = tmp_path / "raptor"
    _write_agent(
        root,
        "fetcher",
        _agent(
            "fetcher",
            """PreToolUse:
  - matcher: WebFetch
    hooks:
      - type: command
        command: >
          "$CLAUDE_PROJECT_DIR"/.claude/hooks/webfetch-domain-allowlist.py
          --anchor-file "$CLAUDE_PROJECT_DIR"/run/a
  - matcher: Bash
    hooks:
      - type: command
        command: >
          "$CLAUDE_PROJECT_DIR"/.claude/hooks/bash-command-allowlist.py
          libexec/raptor-clone-repo 'python3 x.py'
""",
        ),
    )

    policy = build_policy(root)

    assert policy["version"] == 1
    assert policy["raptor_home"] == "$RAPTOR_HOME"
    assert policy["hooks"] == {
        "bash": ".claude/hooks/bash-command-allowlist.py",
        "webfetch": ".claude/hooks/webfetch-domain-allowlist.py",
    }
    entry = policy["agents"]["fetcher"]
    assert entry["bash"] == ["libexec/raptor-clone-repo", "python3 x.py"]
    assert entry["webfetch"] == {
        "domains": [],
        "https_any": False,
        "anchor_file": "run/a",
    }
    assert policy["warnings"] == []


def test_https_any_and_host_list(tmp_path: Path) -> None:
    root = tmp_path / "raptor"
    _write_agent(
        root,
        "any",
        _agent(
            "any",
            """PreToolUse:
  - matcher: WebFetch
    hooks:
      - type: command
        command: "\\"$CLAUDE_PROJECT_DIR\\"/.claude/hooks/webfetch-domain-allowlist.py --https-any"
""",
        ),
    )
    _write_agent(
        root,
        "hosts",
        _agent(
            "hosts",
            """PreToolUse:
  - matcher: WebFetch
    hooks:
      - type: command
        command: "\\"$CLAUDE_PROJECT_DIR\\"/.claude/hooks/webfetch-domain-allowlist.py GitHub.com https://api.github.com/items"
""",
        ),
    )

    policy = build_policy(root)

    assert policy["agents"]["any"]["webfetch"]["https_any"] is True
    assert policy["agents"]["any"]["webfetch"]["domains"] == []
    assert policy["agents"]["hosts"]["webfetch"]["domains"] == ["github.com", "api.github.com"]


def test_agent_without_hooks_gets_empty_policy(tmp_path: Path) -> None:
    root = tmp_path / "raptor"
    _write_agent(root, "plain", _agent("plain", None))

    policy = build_policy(root)

    assert policy["agents"]["plain"] == {
        "bash": [],
        "webfetch": {"domains": [], "https_any": False},
    }


def test_unknown_hook_and_matcher_are_warnings(tmp_path: Path) -> None:
    root = tmp_path / "raptor"
    _write_agent(
        root,
        "odd",
        _agent(
            "odd",
            """PreToolUse:
  - matcher: Edit
    hooks:
      - type: command
        command: "\\"$CLAUDE_PROJECT_DIR\\"/.claude/hooks/write-path-allowlist.py src"
  - matcher: WebFetch
    hooks:
      - type: command
        command: "\\"$CLAUDE_PROJECT_DIR\\"/.claude/hooks/mystery-hook.py example.com"
""",
        ),
    )

    policy = build_policy(root)

    assert policy["agents"]["odd"]["webfetch"]["domains"] == []
    warnings = "\n".join(policy["warnings"])
    assert "unsupported matcher 'Edit'" in warnings
    assert "unknown hook script" in warnings


def test_malformed_command_warns_instead_of_crashing(tmp_path: Path) -> None:
    root = tmp_path / "raptor"
    _write_agent(
        root,
        "broken",
        _agent(
            "broken",
            """PreToolUse:
  - matcher: Bash
    hooks:
      - type: command
        command: >
          "$CLAUDE_PROJECT_DIR"/.claude/hooks/bash-command-allowlist.py 'unterminated
""",
        ),
    )

    policy = build_policy(root)

    assert policy["agents"]["broken"]["bash"] == []
    assert any("cannot parse" in warning for warning in policy["warnings"])


def test_multiple_hooks_for_same_matcher_merge(tmp_path: Path) -> None:
    root = tmp_path / "raptor"
    _write_agent(
        root,
        "multi",
        _agent(
            "multi",
            """PreToolUse:
  - matcher: WebFetch
    hooks:
      - type: command
        command: "\\"$CLAUDE_PROJECT_DIR\\"/.claude/hooks/webfetch-domain-allowlist.py a.example"
  - matcher: WebFetch
    hooks:
      - type: command
        command: "\\"$CLAUDE_PROJECT_DIR\\"/.claude/hooks/webfetch-domain-allowlist.py b.example"
""",
        ),
    )

    policy = build_policy(root)

    assert policy["agents"]["multi"]["webfetch"]["domains"] == ["a.example", "b.example"]


def test_duplicate_agent_names_fail_loudly(tmp_path: Path) -> None:
    root = tmp_path / "raptor"
    _write_agent(root, "one", _agent("same", None))
    _write_agent(root, "two", _agent("same", None))

    with pytest.raises(GenerationError, match="duplicate agent name"):
        build_policy(root)


def test_write_policy_emits_valid_json(tmp_path: Path) -> None:
    root = tmp_path / "raptor"
    _write_agent(root, "plain", _agent("plain", None))
    destination = tmp_path / "out" / "raptor-policy.json"

    policy = write_policy(root, destination)

    assert json.loads(destination.read_text(encoding="utf-8")) == policy
    assert destination.read_text(encoding="utf-8").endswith("\n")


def test_missing_dot_claude_returns_empty_document(tmp_path: Path) -> None:
    policy = build_policy(tmp_path / "nothing")

    assert policy["agents"] == {}
    assert policy["hooks"] == {}
