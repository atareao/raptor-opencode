"""Tests for the OpenCode configuration fragment (``gen_config``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor_opencode.errors import GenerationError
from raptor_opencode.gen_config import (
    FRAGMENT_NAME,
    NOTES_NAME,
    build_fragment,
    load_mcp_servers,
    render_fragment,
    render_notes,
    write_config,
)


def _parse_jsonc(text: str) -> dict[str, object]:
    stripped = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    return json.loads(stripped)


def test_fragment_has_expected_shape(tmp_path: Path) -> None:
    raptor = tmp_path / "raptor"

    fragment = build_fragment(raptor, ["audit"], install_home="/opt/raptor")

    assert fragment["instructions"] == ["{env:RAPTOR_HOME}/AGENTS.md"]
    assert fragment["env"] == {
        "RAPTOR_HOME": "/opt/raptor",
        "CLAUDE_ENV_FILE": "{env:RAPTOR_HOME}/.claude/raptor.env",
        "CLAUDECODE": "1",
        "CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1",
        "BASH_DEFAULT_TIMEOUT_MS": "300000",
        "PATH": "{env:RAPTOR_HOME}/libexec:{env:PATH}",
    }
    assert fragment["plugin"] == ["plugins/raptor-guard.ts"]
    assert fragment["permissions"] == [
        {"action": "skill", "resource": "raptor-*", "effect": "allow"},
        {"action": "skill", "resource": "audit", "effect": "allow"},
    ]
    assert "mcp" not in fragment


def test_permissions_include_every_generated_skill_without_duplicates(tmp_path: Path) -> None:
    fragment = build_fragment(tmp_path / "raptor", ["b", "a", "a"])

    resources = [item["resource"] for item in fragment["permissions"]]
    assert resources == ["raptor-*", "b", "a"]


def test_no_mcp_file_returns_none(tmp_path: Path) -> None:
    assert load_mcp_servers(tmp_path) is None


def test_mcp_file_is_translated(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "local-tool": {
                        "command": "npx",
                        "args": ["-y", "pkg"],
                        "env": {"TOKEN": "x"},
                    },
                    "remote-tool": {
                        "url": "https://mcp.example/sse",
                        "headers": {"Authorization": "Bearer t"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    fragment = build_fragment(tmp_path, [])

    servers = fragment["mcp"]["servers"]
    assert servers["local-tool"] == {
        "type": "local",
        "command": ["npx", "-y", "pkg"],
        "environment": {"TOKEN": "x"},
    }
    assert servers["remote-tool"] == {
        "type": "remote",
        "url": "https://mcp.example/sse",
        "headers": {"Authorization": "Bearer t"},
    }


def test_invalid_mcp_file_fails_loudly(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(GenerationError, match="invalid JSON"):
        load_mcp_servers(tmp_path)


def test_mcp_server_without_url_or_command_fails(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"broken": {}}}), encoding="utf-8"
    )

    with pytest.raises(GenerationError, match="neither 'url' nor 'command'"):
        load_mcp_servers(tmp_path)


def test_rendered_fragment_is_valid_jsonc(tmp_path: Path) -> None:
    fragment = build_fragment(tmp_path / "raptor", ["audit"], install_home="/opt/raptor")
    text = render_fragment(fragment)

    assert text.startswith("//")
    assert _parse_jsonc(text) == fragment


def test_fragment_never_embeds_the_generation_checkout(tmp_path: Path) -> None:
    raptor = tmp_path / "test-checkout"
    text = render_fragment(build_fragment(raptor, ["audit"], install_home="/opt/raptor"))

    assert str(raptor) not in text
    assert "/tmp/" not in text


def test_merge_notes_explain_every_key(tmp_path: Path) -> None:
    fragment = build_fragment(tmp_path / "raptor", ["audit"], install_home="/opt/raptor")
    notes = render_notes(fragment)

    for key in ("instructions", "env", "permissions", "plugin", "mcp"):
        assert f"`{key}`" in notes
    assert "libexec" in notes
    assert "skill * deny" in notes
    assert "{env:RAPTOR_HOME}/AGENTS.md" in notes
    assert "--raptor-home" in notes
    assert str(tmp_path) not in notes


def test_write_config_writes_fragment_and_notes(tmp_path: Path) -> None:
    out = tmp_path / "config"

    fragment_path, notes_path = write_config(
        tmp_path / "raptor", out, ["audit"], install_home="/opt/raptor"
    )

    assert fragment_path == out / FRAGMENT_NAME
    assert notes_path == out / NOTES_NAME
    assert _parse_jsonc(fragment_path.read_text(encoding="utf-8"))["permissions"]
    assert notes_path.read_text(encoding="utf-8").startswith("# Merging")


def test_write_config_dry_run_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "config"

    fragment_path, notes_path = write_config(
        tmp_path / "raptor", out, ["audit"], install_home="/opt/raptor", dry_run=True
    )

    assert fragment_path.parent == out
    assert notes_path.parent == out
    assert not out.exists()
