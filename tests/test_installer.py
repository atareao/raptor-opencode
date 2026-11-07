"""Tests for ``install`` / ``uninstall``.

Every path is rooted in ``tmp_path``; ``HOME`` and the ``XDG_*`` variables are
monkeypatched and ``Path.home`` is booby-trapped so a test fails loudly if the
installer ever falls back to the real user home.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from raptor_opencode import installer
from raptor_opencode.errors import GenerationError, RaptorOpencodeError
from raptor_opencode.installer import (
    BACKUP_SUFFIX,
    MANIFEST_NAME,
    JsoncError,
    default_paths,
    install,
    merge_jsonc,
    uninstall,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
STAMP = NOW.strftime("%Y%m%dT%H%M%SZ")


class FakeRunner:
    """Stand-in for ``subprocess.run`` that records git invocations."""

    def __init__(self, *, dirty: bool = False, failing: str | None = None) -> None:
        self.dirty = dirty
        self.failing = failing
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_kwargs: object) -> SimpleNamespace:
        self.calls.append(list(args))
        if self.failing and self.failing in args:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        stdout = " M CLAUDE.md\n" if self.dirty and "status" in args else ""
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate HOME/XDG and forbid the real ``Path.home`` fallback."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    monkeypatch.delenv("RAPTOR_HOME", raising=False)

    def _forbidden(cls: type[Path]) -> Path:
        pytest.fail("Path.home() was used; pass an explicit path or env mapping")

    monkeypatch.setattr(Path, "home", classmethod(_forbidden))
    return home


def _checkout(tmp_path: Path, *, skill: bool = True) -> Path:
    root = tmp_path / "raptor"
    shutil.copytree(FIXTURES / "raptor", root)
    (root / ".git").mkdir()
    if skill:
        skill_dir = root / ".claude" / "skills" / "raptor-demo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: raptor-demo\ndescription: Demo\n---\n\nbody\n",
            encoding="utf-8",
        )
    return root


def _tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _run_install(
    root: Path,
    home: Path,
    *,
    runner: Callable[..., object] | None = None,
    **overrides: object,
) -> installer.InstallResult:
    generated = home / ".local" / "share" / "raptor-opencode" / "generated"
    kwargs: dict[str, object] = {
        "raptor_home": root,
        "generated": generated,
        "opencode": home / ".config" / "opencode",
        "skills": home / ".agents" / "skills",
        "yes": True,
        "skip_deps": True,
        "run": runner or FakeRunner(),
        "now": NOW,
    }
    kwargs.update(overrides)
    return install(**kwargs)  # type: ignore[arg-type]


def _run_uninstall(home: Path, **overrides: object) -> installer.UninstallResult:
    kwargs: dict[str, object] = {
        "generated": home / ".local" / "share" / "raptor-opencode" / "generated",
        "opencode": home / ".config" / "opencode",
        "skills": home / ".agents" / "skills",
    }
    kwargs.update(overrides)
    return uninstall(**kwargs)  # type: ignore[arg-type]


def _manifest(home: Path) -> dict[str, object]:
    path = home / ".local" / "share" / "raptor-opencode" / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def test_clean_install_creates_links_and_manifest(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    runner = FakeRunner()

    result = _run_install(root, sandbox, runner=runner)

    assert (result.agents, result.commands, result.skills) == (5, 5, 1)
    assert result.links == 13
    assert result.backups == 0
    assert (sandbox / ".config" / "opencode" / "agents" / "audit-reviewer.md").is_symlink()
    assert (sandbox / ".agents" / "skills" / "raptor-demo").is_symlink()
    assert (sandbox / ".config" / "opencode" / "plugins" / "raptor-guard.ts").is_symlink()

    manifest = _manifest(sandbox)
    assert manifest["ref"] == "v3.1.0"
    assert manifest["raptor_home"] == str(root)
    assert len(manifest["links"]) == 13  # type: ignore[arg-type]
    assert manifest["backups"] == []
    assert runner.calls and "status" in runner.calls[0]


def test_dry_run_writes_nothing(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    before = _tree(tmp_path)

    result = _run_install(root, sandbox, dry_run=True)

    assert (result.agents, result.commands, result.skills, result.links) == (0, 0, 0, 0)
    assert _tree(tmp_path) == before


def test_second_install_is_idempotent(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    first = _run_install(root, sandbox)
    after_first = _tree(sandbox)
    second = _run_install(root, sandbox, now=datetime(2026, 6, 6, tzinfo=UTC))

    assert (second.agents, second.commands, second.skills) == (
        first.agents,
        first.commands,
        first.skills,
    )
    assert second.backups == 0
    assert not list(sandbox.rglob(f"*{BACKUP_SUFFIX}*"))
    assert _tree(sandbox) == after_first


def test_collision_backs_up_and_uninstall_restores(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    agent = sandbox / ".config" / "opencode" / "agents" / "audit-reviewer.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("user content\n", encoding="utf-8")
    before = _tree(sandbox)

    result = _run_install(root, sandbox)

    backup = agent.with_name(f"{agent.name}.{BACKUP_SUFFIX}-{STAMP}")
    assert result.backups == 1
    assert backup.read_text(encoding="utf-8") == "user content\n"
    assert agent.is_symlink()
    assert _manifest(sandbox)["backups"] == [{"path": str(agent), "backup": str(backup)}]

    uninstall_result = _run_uninstall(sandbox)

    assert uninstall_result.restored_backups == 1
    assert not agent.is_symlink()
    assert agent.read_text(encoding="utf-8") == "user content\n"
    assert not backup.exists()
    assert _tree(sandbox) == before


def test_dirty_checkout_aborts_without_touching_anything(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    before = _tree(tmp_path)

    with pytest.raises(RaptorOpencodeError, match="local changes"):
        _run_install(root, sandbox, runner=FakeRunner(dirty=True))

    assert _tree(tmp_path) == before


def test_merge_config_preserves_comments_and_foreign_keys(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    config_dir = sandbox / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    target = config_dir / "opencode.jsonc"
    original = (
        "{\n"
        "  // keep this comment\n"
        "  /* and this block */\n"
        '  "theme": "dark",\n'
        '  "instructions": ["/existing.md"],\n'
        '  "env": {"FOO": "bar"},\n'
        '  "permissions": [{"action": "other"}],\n'
        "}\n"
    )
    target.write_text(original, encoding="utf-8")

    result = _run_install(root, sandbox, merge_config=True)

    merged_text = target.read_text(encoding="utf-8")
    assert "// keep this comment" in merged_text
    assert "/* and this block */" in merged_text
    merged = installer._loads_jsonc(merged_text)
    assert merged["theme"] == "dark"
    assert merged["env"]["FOO"] == "bar"
    assert merged["instructions"] == ["/existing.md", "{env:RAPTOR_HOME}/AGENTS.md"]
    assert merged["env"]["RAPTOR_HOME"] == str(root)
    assert merged["plugin"] == [str(result.generated_dir / "plugins" / "raptor-guard.ts")]
    assert merged["permissions"][0] == {"action": "other"}

    backup = target.with_name(f"opencode.jsonc.{BACKUP_SUFFIX}-{STAMP}")
    assert backup.read_text(encoding="utf-8") == original

    _run_uninstall(sandbox)
    assert target.read_text(encoding="utf-8") == original


def test_merge_config_is_idempotent(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    config_dir = sandbox / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    target = config_dir / "opencode.jsonc"
    target.write_text('{\n  // note\n  "theme": "dark"\n}\n', encoding="utf-8")

    _run_install(root, sandbox, merge_config=True)
    after_first = target.read_text(encoding="utf-8")
    _run_install(root, sandbox, merge_config=True, now=datetime(2026, 7, 7, tzinfo=UTC))

    assert target.read_text(encoding="utf-8") == after_first
    backups = list(config_dir.glob(f"opencode.jsonc.{BACKUP_SUFFIX}-*"))
    assert len(backups) == 1


def test_invalid_config_aborts_without_writing(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    config_dir = sandbox / ".config" / "opencode"
    config_dir.mkdir(parents=True)
    target = config_dir / "opencode.jsonc"
    target.write_text("{not json", encoding="utf-8")

    with pytest.raises(JsoncError):
        _run_install(root, sandbox, merge_config=True)

    assert target.read_text(encoding="utf-8") == "{not json"
    assert not list(config_dir.glob(f"*{BACKUP_SUFFIX}*"))


def test_uninstall_restores_home_tree(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    before = _tree(sandbox)

    _run_install(root, sandbox)
    _run_uninstall(sandbox)

    assert _tree(sandbox) == before


def test_uninstall_without_manifest_fails(sandbox: Path) -> None:
    with pytest.raises(RaptorOpencodeError, match="no install manifest"):
        _run_uninstall(sandbox)


def test_defaults_never_escape_the_sandbox(sandbox: Path) -> None:
    raptor, generated, opencode, skills = default_paths(env=os.environ)

    for path in (raptor, generated, opencode, skills):
        assert path.is_relative_to(sandbox), path


def test_merge_jsonc_rejects_invalid_input() -> None:
    with pytest.raises(JsoncError):
        merge_jsonc("{not json", {"plugin": []})


def test_copy_mode_places_real_files(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)

    _run_install(root, sandbox, copy=True)

    agent = sandbox / ".config" / "opencode" / "agents" / "audit-reviewer.md"
    skill = sandbox / ".agents" / "skills" / "raptor-demo"
    assert agent.is_file() and not agent.is_symlink()
    assert skill.is_dir() and not skill.is_symlink()
    _run_uninstall(sandbox)
    assert not agent.exists()
    assert not skill.exists()


def test_cli_dry_run_and_missing_manifest(
    sandbox: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from raptor_opencode.cli import main

    root = _checkout(tmp_path)
    before = _tree(tmp_path)

    assert main(["install", "--raptor-home", str(root), "--dry-run", "--skip-deps"]) == 0
    assert "would install 0 agents" in capsys.readouterr().out
    assert _tree(tmp_path) == before

    assert main(["uninstall"]) == 1
    assert "no install manifest" in capsys.readouterr().err


def test_generation_failure_leaves_no_manual_cleanup(sandbox: Path, tmp_path: Path) -> None:
    root = tmp_path / "raptor"
    root.mkdir()
    (root / ".git").mkdir()

    with pytest.raises(GenerationError, match="nothing to generate"):
        _run_install(root, sandbox)

    assert not (sandbox / ".config" / "opencode").exists()
    assert not (sandbox / ".local" / "share" / "raptor-opencode" / MANIFEST_NAME).exists()
