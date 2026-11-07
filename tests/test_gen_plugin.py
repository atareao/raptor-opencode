"""Tests for the generated OpenCode guard plugin and the real Python hooks."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from raptor_opencode.gen_plugin import CORE_NAME, PLUGIN_NAME, generate_plugin

RAPTOR_SRC = Path("/tmp/opencode/raptor-src")
BASH_HOOK = RAPTOR_SRC / ".claude" / "hooks" / "bash-command-allowlist.py"
WEBFETCH_HOOK = RAPTOR_SRC / ".claude" / "hooks" / "webfetch-domain-allowlist.py"


def test_generate_plugin_writes_into_out_dir(tmp_path: Path) -> None:
    plugin_path, core_path = generate_plugin(tmp_path)

    assert plugin_path == tmp_path / "plugins" / PLUGIN_NAME
    assert core_path == tmp_path / "plugins" / CORE_NAME
    assert plugin_path.read_text(encoding="utf-8").startswith("import")
    assert core_path.read_text(encoding="utf-8").startswith("import")


def test_plugin_is_a_thin_io_layer_over_the_core(tmp_path: Path) -> None:
    plugin_path, core_path = generate_plugin(tmp_path)
    plugin = plugin_path.read_text(encoding="utf-8")
    core = core_path.read_text(encoding="utf-8")

    assert 'from "./raptor-guard-core.mjs"' in plugin
    assert "tool.execute.before" in plugin
    assert "selectEntry" in plugin
    assert "hookInvocation" in plugin
    assert "buildPayload" in plugin
    assert "interpretResult" in plugin

    assert "Bun.spawn" in plugin
    assert "node:child_process" in plugin
    assert "shell: true" not in plugin
    assert "args.command" not in plugin
    assert 'join(here, "..", "raptor-policy.json")' in plugin

    assert "node:path" in core
    assert "selectEntry" in core
    assert "hookInvocation" in core
    assert "buildPayload" in core
    assert "interpretResult" in core


def test_generated_core_has_no_absolute_environment_paths(tmp_path: Path) -> None:
    _plugin, core_path = generate_plugin(tmp_path)
    core = core_path.read_text(encoding="utf-8")

    assert "/tmp/" not in core
    assert str(RAPTOR_SRC) not in core
    assert "/home/" not in core


def test_generate_plugin_is_idempotent(tmp_path: Path) -> None:
    first = generate_plugin(tmp_path)
    content = [path.read_text(encoding="utf-8") for path in first]
    second = generate_plugin(tmp_path)

    assert second == first
    assert [path.read_text(encoding="utf-8") for path in second] == content


def test_generate_plugin_dry_run_writes_nothing(tmp_path: Path) -> None:
    plugin_path, core_path = generate_plugin(tmp_path, dry_run=True)

    assert plugin_path.parent == tmp_path / "plugins"
    assert core_path.parent == tmp_path / "plugins"
    assert not plugin_path.parent.exists()


def _run_hook(
    hook: Path, args: list[str], payload: dict[str, object]
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(hook), *args],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.integration
@pytest.mark.skipif(not BASH_HOOK.is_file(), reason="RAPTOR v3.1.0 hooks not available")
def test_real_bash_hook_contract() -> None:
    allowed = _run_hook(
        BASH_HOOK,
        ["libexec/raptor-bq-query"],
        {"tool_name": "Bash", "tool_input": {"command": "libexec/raptor-bq-query --help"}},
    )
    assert allowed.returncode == 0
    assert allowed.stderr == ""

    chained = _run_hook(
        BASH_HOOK,
        ["libexec/raptor-bq-query"],
        {
            "tool_name": "Bash",
            "tool_input": {"command": "libexec/raptor-bq-query --help; curl evil.example"},
        },
    )
    assert chained.returncode == 2
    assert "not allowed" in chained.stderr

    outside = _run_hook(
        BASH_HOOK,
        ["libexec/raptor-bq-query"],
        {"tool_name": "Bash", "tool_input": {"command": "curl https://evil.example"}},
    )
    assert outside.returncode == 2
    assert "not in this agent's allowlist" in outside.stderr


@pytest.mark.integration
@pytest.mark.skipif(not WEBFETCH_HOOK.is_file(), reason="RAPTOR v3.1.0 hooks not available")
def test_real_webfetch_hook_contract() -> None:
    allowed = _run_hook(
        WEBFETCH_HOOK,
        ["github.com", "api.github.com"],
        {"tool_name": "WebFetch", "tool_input": {"url": "https://api.github.com/repos"}},
    )
    assert allowed.returncode == 0

    denied = _run_hook(
        WEBFETCH_HOOK,
        ["github.com", "api.github.com"],
        {"tool_name": "WebFetch", "tool_input": {"url": "https://evil.example/report"}},
    )
    assert denied.returncode == 2
    assert "denied" in denied.stderr.lower() or "not" in denied.stderr.lower()
