"""Tests for the ``global`` / ``project`` scope split.

Every path lives under ``tmp_path``: ``HOME`` and the ``XDG_*`` variables are
monkeypatched and ``Path.home`` is booby-trapped, so a regression that reaches
the real user home fails loudly. Git is faked through the injectable runner.
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

from raptor_opencode import cli, installer
from raptor_opencode.doctor import ExpectedCounts, run_checks
from raptor_opencode.errors import RaptorOpencodeError
from raptor_opencode.installer import install, uninstall
from raptor_opencode.scope import resolve_layout, resolve_project_config

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 2, 3, 4, 5, 6, tzinfo=UTC)
PROJECT_AGENTS = 5
PROJECT_COMMANDS = 5
PROJECT_SKILLS = 1


class FakeRunner:
    """Stand-in for ``subprocess.run``: answers git and opencode probes."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_kwargs: object) -> SimpleNamespace:
        argv = [str(item) for item in args]
        self.calls.append(argv)
        if Path(argv[0]).name == "opencode":
            return SimpleNamespace(returncode=0, stdout="opencode v2.2.6\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def fake_which(name: str) -> str | None:
    return {
        "python3": "/usr/bin/python3",
        "git": "/usr/bin/git",
        "opencode": "/usr/bin/opencode",
    }.get(name)


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


def _checkout(tmp_path: Path) -> Path:
    root = tmp_path / "raptor"
    shutil.copytree(FIXTURES / "raptor", root)
    (root / ".git").mkdir()
    skill_dir = root / ".claude" / "skills" / "raptor-demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: raptor-demo\ndescription: Demo\n---\n\nbody\n",
        encoding="utf-8",
    )
    return root


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "targets").mkdir()
    return project


def _tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _install_project(
    root: Path,
    project: Path,
    *,
    runner: Callable[..., object] | None = None,
    **overrides: object,
) -> installer.InstallResult:
    kwargs: dict[str, object] = {
        "raptor_home": root,
        "scope": "project",
        "cwd": project,
        "merge_config": True,
        "yes": True,
        "skip_deps": True,
        "run": runner or FakeRunner(),
        "now": NOW,
    }
    kwargs.update(overrides)
    return install(**kwargs)  # type: ignore[arg-type]


# --- layouts ----------------------------------------------------------------


def test_global_layout_keeps_the_historical_xdg_tree(sandbox: Path) -> None:
    layout = resolve_layout("global", env=os.environ)

    assert layout.project_root is None
    assert layout.opencode == sandbox / ".config" / "opencode"
    assert layout.generated == sandbox / ".local" / "share" / "raptor-opencode" / "generated"
    assert layout.skills == sandbox / ".agents" / "skills"
    assert (
        layout.manifest
        == sandbox / ".local" / "share" / "raptor-opencode" / "install-manifest.json"
    )
    assert layout.config == sandbox / ".config" / "opencode" / "opencode.jsonc"
    assert layout.venv == sandbox / ".local" / "share" / "raptor-opencode" / "venv"


def test_project_layout_stays_inside_the_project(sandbox: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)

    layout = resolve_layout("project", cwd=project, env=os.environ)

    assert layout.project_root == project.resolve()
    assert layout.opencode == project / ".opencode"
    assert layout.generated == project / ".opencode" / "generated"
    assert layout.skills == project / ".agents" / "skills"
    assert layout.manifest == project / ".opencode" / "raptor-manifest.json"
    assert layout.config == project / "opencode.json"
    # The virtualenv is shared and never pollutes the workspace.
    assert not layout.venv.is_relative_to(project)
    assert layout.venv.is_relative_to(sandbox)


def test_unknown_scope_is_rejected(sandbox: Path) -> None:
    with pytest.raises(RaptorOpencodeError, match="unknown scope"):
        resolve_layout("workspace", env=os.environ)  # type: ignore[arg-type]


def test_single_existing_project_config_is_reused(sandbox: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    existing = project / ".opencode" / "opencode.jsonc"
    existing.parent.mkdir()
    existing.write_text("{}\n", encoding="utf-8")

    layout = resolve_layout("project", cwd=project, env=os.environ)

    assert layout.config == existing


def test_two_project_configs_are_an_error(sandbox: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "opencode.json").write_text("{}\n", encoding="utf-8")
    nested = project / ".opencode" / "opencode.json"
    nested.parent.mkdir()
    nested.write_text("{}\n", encoding="utf-8")

    with pytest.raises(RaptorOpencodeError, match="ambiguous"):
        resolve_project_config(project)
    with pytest.raises(RaptorOpencodeError, match="ambiguous"):
        resolve_layout("project", cwd=project, env=os.environ)


def test_ambiguous_config_aborts_a_project_install(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    project = _project(tmp_path)
    (project / "opencode.json").write_text("{}\n", encoding="utf-8")
    nested = project / ".opencode" / "opencode.json"
    nested.parent.mkdir()
    nested.write_text("{}\n", encoding="utf-8")
    before = _tree(project)

    with pytest.raises(RaptorOpencodeError, match="ambiguous"):
        _install_project(root, project)

    assert _tree(project) == before


# --- install / uninstall ----------------------------------------------------


def test_project_install_writes_inside_the_project(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    project = _project(tmp_path)

    result = _install_project(root, project)

    assert result.scope == "project"
    assert result.project_root == project.resolve()
    assert result.config_path == project / "opencode.json"
    assert result.manifest_path == project / ".opencode" / "raptor-manifest.json"
    assert (result.agents, result.commands, result.skills) == (
        PROJECT_AGENTS,
        PROJECT_COMMANDS,
        PROJECT_SKILLS,
    )
    assert (project / ".opencode" / "agents" / "audit-reviewer.md").is_symlink()
    assert (project / ".opencode" / "commands").is_dir()
    assert (project / ".opencode" / "plugins" / "raptor-guard.ts").is_symlink()
    assert (project / ".agents" / "skills" / "raptor-demo").is_symlink()

    # Nothing leaked into the user configuration or the global skills tree.
    assert not (sandbox / ".config").exists()
    assert not (sandbox / ".agents").exists()

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["links"]) == result.links
    assert manifest["raptor_home"] == str(root)


def test_project_config_is_strict_json_and_complete(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    project = _project(tmp_path)

    _install_project(root, project)

    text = (project / "opencode.json").read_text(encoding="utf-8")
    assert "//" not in text, "opencode.json must stay strict JSON"
    config = json.loads(text)
    assert config["instructions"] == ["{env:RAPTOR_HOME}/AGENTS.md"]
    assert config["env"]["RAPTOR_HOME"] == str(root)
    assert config["plugin"] == [
        str(project / ".opencode" / "generated" / "plugins" / "raptor-guard.ts")
    ]
    assert {"action": "skill", "resource": "raptor-*", "effect": "allow"} in config["permissions"]


def test_project_install_without_merge_config_warns(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    project = _project(tmp_path)

    result = _install_project(root, project, merge_config=False)

    assert not (project / "opencode.json").exists()
    assert any("--merge-config" in warning for warning in result.warnings)
    assert (project / ".opencode" / "agents" / "audit-reviewer.md").is_symlink()


def test_project_uninstall_restores_a_fresh_project(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    project = _project(tmp_path)
    before = _tree(project)

    _install_project(root, project)
    result = uninstall(scope="project", cwd=project)

    assert result.removed_links == PROJECT_AGENTS + PROJECT_COMMANDS + PROJECT_SKILLS + 2
    assert _tree(project) == before


def test_project_uninstall_restores_an_existing_config(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    project = _project(tmp_path)
    original = '{\n  "theme": "dark"\n}\n'
    (project / "opencode.json").write_text(original, encoding="utf-8")
    before = _tree(project)

    _install_project(root, project)
    assert (project / "opencode.json").read_text(encoding="utf-8") != original

    result = uninstall(scope="project", cwd=project)

    assert result.restored_backups == 1
    assert (project / "opencode.json").read_text(encoding="utf-8") == original
    assert _tree(project) == before


def test_project_uninstall_without_manifest_fails(sandbox: Path, tmp_path: Path) -> None:
    project = _project(tmp_path)

    with pytest.raises(RaptorOpencodeError, match="no install manifest"):
        uninstall(scope="project", cwd=project)


def test_global_install_is_not_affected_by_the_project_scope(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    generated = sandbox / ".local" / "share" / "raptor-opencode" / "generated"

    result = install(
        raptor_home=root,
        generated=generated,
        opencode=sandbox / ".config" / "opencode",
        skills=sandbox / ".agents" / "skills",
        merge_config=True,
        yes=True,
        skip_deps=True,
        run=FakeRunner(),
        now=NOW,
    )

    assert result.scope == "global"
    assert result.project_root is None
    assert result.config_path == sandbox / ".config" / "opencode" / "opencode.jsonc"
    assert result.config_path.is_file()
    assert (sandbox / ".config" / "opencode" / "agents" / "audit-reviewer.md").is_symlink()

    uninstall(
        generated=generated,
        opencode=sandbox / ".config" / "opencode",
        skills=sandbox / ".agents" / "skills",
    )
    assert not result.config_path.exists()


# --- doctor -----------------------------------------------------------------

PROJECT_EXPECTED = ExpectedCounts(
    agents=PROJECT_AGENTS,
    commands=PROJECT_COMMANDS,
    skills=PROJECT_SKILLS,
    plugin_files=2,
)


def _doctor(project: Path, root: Path, **overrides: object):
    kwargs: dict[str, object] = {
        "raptor_home": root,
        "scope": "project",
        "cwd": project,
        "expected": PROJECT_EXPECTED,
        "run": FakeRunner(),
        "which": fake_which,
    }
    kwargs.update(overrides)
    return run_checks(**kwargs)  # type: ignore[arg-type]


def test_doctor_reports_the_project_scope(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    project = _project(tmp_path)
    _install_project(root, project)

    report = _doctor(project, root)

    payload = report.to_dict()
    assert payload["scope"] == "project"
    assert payload["paths"]["project_root"] == str(project.resolve())
    assert payload["paths"]["config"] == str(project / "opencode.json")
    assert payload["paths"]["manifest"] == str(project / ".opencode" / "raptor-manifest.json")
    statuses = {check.name: check.status for check in report.checks}
    for name in ("config-instructions", "config-env", "config-plugin", "config-skills"):
        assert statuses[name] == "ok", report.render()


def test_doctor_flags_an_ambiguous_project_config(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    project = _project(tmp_path)
    (project / "opencode.json").write_text("{}\n", encoding="utf-8")
    nested = project / ".opencode" / "opencode.json"
    nested.parent.mkdir()
    nested.write_text("{}\n", encoding="utf-8")

    report = _doctor(project, root)

    checks = [check for check in report.checks if check.name == "project-config"]
    assert [check.status for check in checks] == ["fail"]
    assert "ambig" in checks[0].message or "configs" in checks[0].message
    assert report.exit_code == 1
    assert "project scope" in report.render()


def test_doctor_global_scope_reports_no_project_paths(sandbox: Path, tmp_path: Path) -> None:
    root = _checkout(tmp_path)

    report = run_checks(
        raptor_home=root,
        expected=PROJECT_EXPECTED,
        run=FakeRunner(),
        which=fake_which,
    )

    assert report.to_dict()["scope"] == "global"
    assert report.paths["project_root"] == ""
    assert report.paths["config"] == str(sandbox / ".config" / "opencode" / "opencode.jsonc")


# --- CLI --------------------------------------------------------------------


def test_cli_passes_scope_and_project_dir(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_install(**kwargs: object) -> installer.InstallResult:
        captured.update(kwargs)
        return installer.InstallResult(
            agents=0,
            commands=0,
            skills=0,
            generated_dir=tmp_path,
            opencode_dir=tmp_path,
            skills_dir=tmp_path,
            manifest_path=tmp_path / "manifest.json",
            links=0,
            backups=0,
        )

    monkeypatch.setattr(cli, "install", fake_install)
    project = _project(tmp_path)

    assert (
        cli.main(
            [
                "install",
                "--scope",
                "project",
                "--project-dir",
                str(project),
                "--merge-config",
                "--yes",
            ]
        )
        == 0
    )
    assert captured["scope"] == "project"
    assert captured["cwd"] == project
    assert captured["merge_config"] is True


def test_cli_rejects_an_unknown_scope(sandbox: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["install", "--scope", "workspace"])

    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
