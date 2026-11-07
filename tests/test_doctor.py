"""Tests for ``doctor``.

Every path lives under ``tmp_path``: HOME and the XDG variables are
monkeypatched and ``Path.home`` is booby-trapped, so a regression that reads (or
writes) the real user home fails loudly. ``git`` and ``opencode`` are faked
through the injectable runner, so no subprocess is spawned.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from raptor_opencode import cli
from raptor_opencode.doctor import (
    DEFAULT_EXPECTED,
    DoctorReport,
    ExpectedCounts,
    run_checks,
)

REF = "v3.1.0"
HEAD = "a" * 40
WANTED = "b" * 40


# --- fixtures and helpers ---------------------------------------------------


class FakeRunner:
    """Stand-in for ``subprocess.run`` that answers git and opencode probes."""

    def __init__(
        self,
        *,
        dirty: bool = False,
        head: str = WANTED,
        wanted: str | None = WANTED,
        tag: str = "v3.1.0",
        version: str = "opencode v2.2.6",
        version_error: bool = False,
    ) -> None:
        self.dirty = dirty
        self.head = head
        self.wanted = wanted
        self.tag = tag
        self.version = version
        self.version_error = version_error
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **_kwargs: object) -> SimpleNamespace:
        argv = [str(item) for item in args]
        self.calls.append(argv)
        name = Path(argv[0]).name
        if name == "git":
            return self._git(argv[3:])
        if name == "opencode":
            if self.version_error:
                raise OSError("cannot execute")
            return _proc(0, f"{self.version}\n")
        return _proc(0)

    def _git(self, sub: list[str]) -> SimpleNamespace:
        if not sub:
            return _proc(0)
        command = sub[0]
        if command == "rev-parse":
            target = sub[-1]
            if target.endswith("^{commit}"):
                if self.wanted is None:
                    return _proc(128, "", "fatal: bad revision")
                return _proc(0, f"{self.wanted}\n")
            return _proc(0, f"{self.head}\n")
        if command == "status":
            return _proc(0, " M CLAUDE.md\n" if self.dirty else "")
        if command == "describe":
            return _proc(0, f"{self.tag}\n")
        return _proc(0)


def _proc(code: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def fake_which(**missing: bool) -> Callable[[str], str | None]:
    """Return a ``shutil.which`` replacement; ``missing={'python3': True}`` hides it."""
    table = {
        "python3": "/usr/bin/python3",
        "git": "/usr/bin/git",
        "opencode": "/usr/bin/opencode",
    }
    table.update({name: None for name, absent in missing.items() if absent})
    return lambda name: table.get(name)


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(source)


@dataclass
class Layout:
    """A synthetic install tree rooted in ``tmp_path``."""

    raptor: Path
    generated: Path
    opencode: Path
    skills: Path
    manifest: Path

    @property
    def kwargs(self) -> dict[str, object]:
        return {
            "raptor_home": self.raptor,
            "ref": REF,
            "opencode_dir": self.opencode,
            "generated_dir": self.generated,
            "skills": self.skills,
        }


GOOD_AGENTS_MD = (
    "# RAPTOR\n\n"
    "Hooks live in $RAPTOR_HOME/.claude/hooks and the CLIs in "
    "$RAPTOR_HOME/libexec/raptor-scan.\n"
)


def build_layout(
    root: Path,
    *,
    agents: int = 18,
    commands: int = 37,
    skills: int = 11,
    hooks: int = 2,
    hook_mode: int = 0o755,
    policy: str | None = None,
    agents_md: str = GOOD_AGENTS_MD,
    config: str | None = None,
    manifest: bool = True,
) -> Layout:
    """Materialise an installed tree whose shape matches ``install``."""
    raptor = root / "raptor"
    (raptor / ".git").mkdir(parents=True)
    _write(raptor / "CLAUDE.md", "upstream\n")
    for name in ("bash-command-allowlist.py", "webfetch-domain-allowlist.py")[:hooks]:
        hook = _write(raptor / ".claude" / "hooks" / name, "#!/usr/bin/env python3\n")
        hook.chmod(hook_mode)

    generated = root / "data" / "raptor-opencode" / "generated"
    opencode = root / "config" / "opencode"
    skills_root = root / "home" / ".agents" / "skills"

    for index in range(agents):
        _write(generated / "agents" / f"agent-{index:02d}.md", "agent\n")
    for index in range(commands):
        _write(generated / "commands" / f"command-{index:02d}.md", "command\n")
    for index in range(skills):
        _write(generated / "skills" / f"skill-{index:02d}" / "SKILL.md", "skill\n")
    _write(generated / "plugins" / "raptor-guard.ts", "export const x = 1;\n")
    _write(generated / "plugins" / "raptor-guard-core.mjs", "export const y = 1;\n")
    if policy is None:
        policy = json.dumps(
            {
                "version": 1,
                "raptor_home": str(raptor.resolve()),
                "default": {"unrestricted": False},
                "hooks": {
                    "bash": ".claude/hooks/bash-command-allowlist.py",
                    "webfetch": ".claude/hooks/webfetch-domain-allowlist.py",
                },
                "agents": {
                    "auditor": {"bash": ["libexec/raptor-scan"], "webfetch": {"domains": []}},
                    "web": {"bash": [], "webfetch": {"domains": ["github.com"]}},
                },
            },
            indent=2,
        )
    _write(generated / "raptor-policy.json", policy)
    _write(generated / "AGENTS.md", agents_md)

    if config is None:
        config = json.dumps(
            {
                "instructions": ["{env:RAPTOR_HOME}/AGENTS.md"],
                "env": {"RAPTOR_HOME": str(raptor.resolve())},
                "plugin": [str(generated / "plugins" / "raptor-guard.ts")],
                "permissions": [
                    {"action": "skill", "resource": "raptor-*", "effect": "allow"},
                ],
            },
            indent=2,
        )
    _write(opencode / "opencode.jsonc", f"// local config\n{config}\n")

    links: list[dict[str, str]] = []
    for source in sorted((generated / "agents").glob("*.md")):
        destination = opencode / "agents" / source.name
        _link(source, destination)
        links.append({"source": str(source), "destination": str(destination), "kind": "agent"})
    for source in sorted((generated / "commands").glob("*.md")):
        destination = opencode / "commands" / source.name
        _link(source, destination)
        links.append({"source": str(source), "destination": str(destination), "kind": "command"})
    for name in ("raptor-guard.ts", "raptor-guard-core.mjs"):
        source = generated / "plugins" / name
        destination = opencode / "plugins" / name
        _link(source, destination)
        links.append({"source": str(source), "destination": str(destination), "kind": "plugin"})
    for source in sorted((generated / "skills").iterdir()):
        destination = skills_root / source.name
        _link(source, destination)
        links.append({"source": str(source), "destination": str(destination), "kind": "skill"})

    manifest_path = generated.parent / "install-manifest.json"
    if manifest:
        _write(
            manifest_path,
            json.dumps(
                {
                    "version": 1,
                    "ref": REF,
                    "raptor_home": str(raptor),
                    "generated_dir": str(generated),
                    "mode": "symlink",
                    "links": links,
                    "backups": [],
                    "created_dirs": [],
                },
                indent=2,
            ),
        )
    return Layout(
        raptor=raptor,
        generated=generated,
        opencode=opencode,
        skills=skills_root,
        manifest=manifest_path,
    )


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


def _report(
    layout: Layout,
    *,
    run: FakeRunner | None = None,
    which: Callable[[str], str | None] | None = None,
    expected: ExpectedCounts = DEFAULT_EXPECTED,
) -> DoctorReport:
    return run_checks(
        **layout.kwargs,  # type: ignore[arg-type]
        expected=expected,
        run=run or FakeRunner(),
        which=which or fake_which(),
    )


def _by_name(report: DoctorReport, name: str) -> list[str]:
    return [check.status for check in report.checks if check.name == name]


def _message(report: DoctorReport, name: str) -> str:
    messages = [check.message for check in report.checks if check.name == name]
    assert messages, f"no check named {name}"
    return " | ".join(messages)


# --- green path -------------------------------------------------------------


def test_a_green_install_exits_zero(sandbox: Path) -> None:
    report = _report(build_layout(sandbox))

    assert report.exit_code == 0, report.render()
    assert report.status == "ok"
    assert all(check.status == "ok" for check in report.checks), report.render()
    assert report.counts()["fail"] == 0


def test_report_to_dict_is_machine_readable(sandbox: Path) -> None:
    payload = _report(build_layout(sandbox)).to_dict()

    assert payload["version"] == 1
    assert payload["status"] == "ok"
    assert payload["exit_code"] == 0
    assert payload["ref"] == REF
    assert set(payload["paths"]) == {"raptor_home", "opencode_dir", "generated_dir", "skills_dir"}
    assert payload["counts"] == {"ok": len(payload["checks"]), "warn": 0, "fail": 0}
    assert all(set(check) >= {"name", "status", "message"} for check in payload["checks"])


def test_expected_counts_are_overridable(sandbox: Path) -> None:
    layout = build_layout(sandbox, agents=2, commands=3, skills=1)

    report = _report(layout, expected=ExpectedCounts(agents=2, commands=3, skills=1))

    assert report.exit_code == 0, report.render()
    assert _by_name(report, "agents") == ["ok"]


def test_doctor_is_read_only(sandbox: Path) -> None:
    layout = build_layout(sandbox)
    before = sorted(str(path) for path in sandbox.rglob("*"))

    _report(layout, run=FakeRunner(), which=fake_which(opencode=True))

    assert sorted(str(path) for path in sandbox.rglob("*")) == before
    missing = sandbox / "does-not-exist"
    report = run_checks(
        raptor_home=layout.raptor,
        ref=REF,
        opencode_dir=missing / "opencode",
        generated_dir=missing / "generated",
        run=FakeRunner(),
        which=fake_which(),
    )
    assert report.exit_code == 1
    assert not missing.exists(), "doctor must not create the paths it checks"


# --- prerequisites ----------------------------------------------------------


def test_missing_python3_is_fatal(sandbox: Path) -> None:
    report = _report(build_layout(sandbox), which=fake_which(python3=True))

    assert report.exit_code == 2
    assert _by_name(report, "prerequisites") == ["fail"]
    assert "python3" in _message(report, "prerequisites")


def test_missing_git_is_fatal(sandbox: Path) -> None:
    report = _report(build_layout(sandbox), which=fake_which(git=True))

    assert report.exit_code == 2
    assert _by_name(report, "git") == ["fail"]


# --- RAPTOR_HOME ------------------------------------------------------------


def test_missing_raptor_home_fails(sandbox: Path) -> None:
    layout = build_layout(sandbox)
    layout.raptor.rename(sandbox / "elsewhere")

    report = _report(layout)

    assert report.exit_code == 1
    assert _by_name(report, "raptor-home") == ["fail"]
    assert "install" in _message(report, "raptor-home")


def test_relative_raptor_home_fails(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = build_layout(sandbox)
    monkeypatch.chdir(sandbox)

    report = run_checks(
        raptor_home=Path("raptor"),
        ref=REF,
        opencode_dir=layout.opencode,
        generated_dir=layout.generated,
        run=FakeRunner(),
        which=fake_which(),
    )

    assert report.exit_code == 1
    assert _by_name(report, "raptor-home") == ["fail"]
    assert "absolute" in _message(report, "raptor-home")


def test_non_git_checkout_fails(sandbox: Path) -> None:
    layout = build_layout(sandbox)
    shutil.rmtree(layout.raptor / ".git")

    report = _report(layout)

    assert _by_name(report, "raptor-git") == ["fail"]
    assert "not a git checkout" in _message(report, "raptor-git")


def test_dirty_checkout_warns_but_exits_zero(sandbox: Path) -> None:
    report = _report(build_layout(sandbox), run=FakeRunner(dirty=True))

    assert _by_name(report, "raptor-clean") == ["warn"]
    assert report.status == "warn"
    assert report.exit_code == 0
    assert "commit or stash" in _message(report, "raptor-clean")


def test_wrong_ref_fails(sandbox: Path) -> None:
    report = _report(build_layout(sandbox), run=FakeRunner(head="c" * 40, tag="v3.0.0"))

    assert _by_name(report, "raptor-ref") == ["fail"]
    assert "expected v3.1.0" in _message(report, "raptor-ref")


def test_unknown_ref_fails(sandbox: Path) -> None:
    report = _report(build_layout(sandbox), run=FakeRunner(wanted=None))

    assert _by_name(report, "raptor-ref") == ["fail"]
    assert "fetch --tags" in _message(report, "raptor-ref")


# --- hooks ------------------------------------------------------------------


def test_missing_hook_fails(sandbox: Path) -> None:
    report = _report(build_layout(sandbox, hooks=1))

    assert report.exit_code == 1
    assert "fail" in _by_name(report, "hooks")
    assert "webfetch-domain-allowlist.py" in _message(report, "hooks")


def test_non_executable_hook_warns(sandbox: Path) -> None:
    report = _report(build_layout(sandbox, hook_mode=0o644))

    assert set(_by_name(report, "hooks")) == {"warn"}
    assert "chmod +x" in _message(report, "hooks")
    assert report.exit_code == 0


# --- generated artifacts ----------------------------------------------------


def test_agent_count_mismatch_fails(sandbox: Path) -> None:
    layout = build_layout(sandbox)
    (layout.generated / "agents" / "agent-00.md").unlink()

    report = _report(layout)

    assert _by_name(report, "agents") == ["fail"]
    assert "expected 18" in _message(report, "agents")


def test_missing_artifacts_fail(sandbox: Path) -> None:
    layout = build_layout(sandbox)
    for name in ("agents", "commands", "skills", "plugins", "raptor-policy.json", "AGENTS.md"):
        target = layout.generated / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    report = _report(layout)

    assert report.exit_code == 1
    for name in ("agents", "commands", "skills", "plugins", "policy"):
        assert _by_name(report, name) == ["fail"]


def test_corrupt_policy_fails(sandbox: Path) -> None:
    report = _report(build_layout(sandbox, policy="{not json"))

    assert _by_name(report, "policy") == ["fail"]
    assert "cannot parse" in _message(report, "policy")


def test_policy_without_absolute_raptor_home_fails(sandbox: Path) -> None:
    policy = json.dumps({"version": 1, "raptor_home": "~/.local/share/raptor", "agents": {"a": {}}})
    report = _report(build_layout(sandbox, policy=policy))

    assert _by_name(report, "policy") == ["fail"]
    assert "absolute" in _message(report, "policy")


def test_empty_policy_agents_warns(sandbox: Path) -> None:
    policy = json.dumps(
        {"version": 1, "raptor_home": str((sandbox / "raptor").resolve()), "agents": {}}
    )
    report = _report(build_layout(sandbox, policy=policy))

    assert _by_name(report, "policy") == ["warn"]
    assert report.exit_code == 0


# --- manifest ---------------------------------------------------------------


def test_missing_manifest_warns(sandbox: Path) -> None:
    report = _report(build_layout(sandbox, manifest=False))

    assert _by_name(report, "manifest") == ["warn"]
    assert "install" in _message(report, "manifest")
    assert report.exit_code == 0


def test_broken_manifest_link_fails(sandbox: Path) -> None:
    layout = build_layout(sandbox)
    (layout.opencode / "agents" / "agent-00.md").unlink()

    report = _report(layout)

    assert _by_name(report, "manifest") == ["fail"]
    assert "missing" in _message(report, "manifest")


def test_manifest_link_outside_generated_fails(sandbox: Path) -> None:
    layout = build_layout(sandbox)
    destination = layout.opencode / "agents" / "agent-00.md"
    destination.unlink()
    destination.symlink_to(layout.raptor / "CLAUDE.md")

    report = _report(layout)

    assert _by_name(report, "manifest") == ["fail"]
    assert "outside" in _message(report, "manifest") or "expected" in _message(report, "manifest")


def test_orphan_link_warns(sandbox: Path) -> None:
    layout = build_layout(sandbox)
    stray = layout.skills / "stray"
    stray.symlink_to(layout.generated / "skills" / "skill-00")

    report = _report(layout)

    assert _by_name(report, "manifest-orphans") == ["warn"]
    assert "stray" in _message(report, "manifest-orphans")
    assert report.exit_code == 0


# --- AGENTS.md --------------------------------------------------------------


def test_relative_claude_path_fails(sandbox: Path) -> None:
    report = _report(
        build_layout(sandbox, agents_md="run .claude/hooks/bash-command-allowlist.py\n")
    )

    assert _by_name(report, "agents-md") == ["fail"]
    assert "relative .claude/" in _message(report, "agents-md")


def test_bare_libexec_path_fails(sandbox: Path) -> None:
    report = _report(build_layout(sandbox, agents_md="run libexec/raptor-scan\n"))

    assert _by_name(report, "agents-md") == ["fail"]
    assert "libexec/" in _message(report, "agents-md")


def test_prefixed_paths_are_accepted(sandbox: Path) -> None:
    report = _report(build_layout(sandbox))

    assert _by_name(report, "agents-md") == ["ok"]


# --- configuration ----------------------------------------------------------


def test_missing_config_fails(sandbox: Path) -> None:
    layout = build_layout(sandbox)
    (layout.opencode / "opencode.jsonc").unlink()

    report = _report(layout)

    assert _by_name(report, "config") == ["fail"]
    assert "merge" in _message(report, "config")


def test_invalid_jsonc_fails(sandbox: Path) -> None:
    report = _report(build_layout(sandbox, config='{"instructions": ["x",]} trailing'))

    assert _by_name(report, "config") == ["fail"]
    assert "cannot parse" in _message(report, "config")


def test_missing_instructions_fails(sandbox: Path) -> None:
    config = json.dumps(
        {"env": {"RAPTOR_HOME": "/opt/raptor"}, "plugin": ["plugins/raptor-guard.ts"]}
    )
    report = _report(build_layout(sandbox, config=config))

    assert _by_name(report, "config-instructions") == ["fail"]
    assert "AGENTS.md" in _message(report, "config-instructions")


def test_missing_env_warns(sandbox: Path) -> None:
    config = json.dumps(
        {
            "instructions": ["{env:RAPTOR_HOME}/AGENTS.md"],
            "plugin": ["plugins/raptor-guard.ts"],
        }
    )
    report = _report(build_layout(sandbox, config=config))

    assert _by_name(report, "config-env") == ["warn"]
    assert "RAPTOR_HOME" in _message(report, "config-env")
    assert report.exit_code == 0


def test_missing_guard_plugin_fails(sandbox: Path) -> None:
    config = json.dumps(
        {
            "instructions": ["{env:RAPTOR_HOME}/AGENTS.md"],
            "env": {"RAPTOR_HOME": "/opt/raptor"},
            "plugin": ["plugins/other.ts"],
        }
    )
    report = _report(build_layout(sandbox, config=config))

    assert _by_name(report, "config-plugin") == ["fail"]
    assert "raptor-guard.ts" in _message(report, "config-plugin")


def test_global_skill_deny_without_allow_fails(sandbox: Path) -> None:
    config = json.dumps(
        {
            "instructions": ["{env:RAPTOR_HOME}/AGENTS.md"],
            "env": {"RAPTOR_HOME": "/opt/raptor"},
            "plugin": ["plugins/raptor-guard.ts"],
            "permissions": [{"action": "skill", "resource": "*", "effect": "deny"}],
        }
    )
    report = _report(build_layout(sandbox, config=config))

    assert _by_name(report, "config-skills") == ["fail"]
    message = _message(report, "config-skills")
    assert "order" in message
    assert "raptor-*" in message
    assert report.exit_code == 1


def test_allow_before_the_deny_still_fails(sandbox: Path) -> None:
    config = json.dumps(
        {
            "instructions": ["{env:RAPTOR_HOME}/AGENTS.md"],
            "env": {"RAPTOR_HOME": "/opt/raptor"},
            "plugin": ["plugins/raptor-guard.ts"],
            "permissions": [
                {"action": "skill", "resource": "raptor-*", "effect": "allow"},
                {"action": "skill", "resource": "*", "effect": "deny"},
            ],
        }
    )
    report = _report(build_layout(sandbox, config=config))

    assert _by_name(report, "config-skills") == ["fail"]


def test_allow_after_the_deny_passes(sandbox: Path) -> None:
    config = json.dumps(
        {
            "instructions": ["{env:RAPTOR_HOME}/AGENTS.md"],
            "env": {"RAPTOR_HOME": "/opt/raptor"},
            "plugin": ["plugins/raptor-guard.ts"],
            "permissions": [
                {"action": "skill", "resource": "*", "effect": "deny"},
                {"action": "skill", "resource": "raptor-*", "effect": "allow"},
            ],
        }
    )
    report = _report(build_layout(sandbox, config=config))

    assert _by_name(report, "config-skills") == ["ok"]
    assert report.exit_code == 0


# --- opencode version -------------------------------------------------------


def test_old_opencode_version_fails(sandbox: Path) -> None:
    report = _report(build_layout(sandbox), run=FakeRunner(version="opencode v2.1.9"))

    assert _by_name(report, "opencode-version") == ["fail"]
    assert "2.2" in _message(report, "opencode-version")


def test_unparseable_version_warns(sandbox: Path) -> None:
    report = _report(build_layout(sandbox), run=FakeRunner(version="dev build"))

    assert _by_name(report, "opencode-version") == ["warn"]
    assert report.exit_code == 0


def test_missing_opencode_warns(sandbox: Path) -> None:
    report = _report(build_layout(sandbox), which=fake_which(opencode=True))

    assert _by_name(report, "opencode-version") == ["warn"]
    assert "not found" in _message(report, "opencode-version")
    assert report.exit_code == 0


def test_opencode_spawn_error_warns(sandbox: Path) -> None:
    report = _report(build_layout(sandbox), run=FakeRunner(version_error=True))

    assert _by_name(report, "opencode-version") == ["warn"]


# --- CLI wiring -------------------------------------------------------------


def test_cli_doctor_prints_json_and_propagates_the_exit_code(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = _report(build_layout(sandbox))
    monkeypatch.setattr(cli, "doctor", lambda **_kwargs: report)

    assert cli.main(["doctor", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["exit_code"] == 0


def test_cli_doctor_returns_one_when_a_check_fails(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = build_layout(sandbox)
    (layout.generated / "agents" / "agent-00.md").unlink()
    report = _report(layout)
    monkeypatch.setattr(cli, "doctor", lambda **_kwargs: report)

    assert cli.main(["doctor"]) == 1
    assert "[fail]" in capsys.readouterr().out


def test_cli_doctor_passes_the_flags_through(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake(**kwargs: object) -> DoctorReport:
        captured.update(kwargs)
        return DoctorReport(checks=(), ref=str(kwargs["ref"]), paths={})

    monkeypatch.setattr(cli, "doctor", fake)

    assert (
        cli.main(
            [
                "doctor",
                "--raptor-home",
                str(sandbox / "raptor"),
                "--ref",
                "v9.9.9",
                "--opencode-dir",
                str(sandbox / "config"),
                "--generated-dir",
                str(sandbox / "gen"),
                "--skills-dir",
                str(sandbox / "skills"),
            ]
        )
        == 0
    )
    assert captured == {
        "raptor_home": sandbox / "raptor",
        "ref": "v9.9.9",
        "opencode_dir": sandbox / "config",
        "generated_dir": sandbox / "gen",
        "skills": sandbox / "skills",
    }
