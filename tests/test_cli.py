"""End-to-end tests for the CLI (no subprocesses, no writes outside tmp_path)."""

from __future__ import annotations

from pathlib import Path

import pytest

from raptor_opencode.cli import main
from raptor_opencode.parse_claude import parse_frontmatter


def test_generate_writes_agents_and_commands(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "opencode"
    assert main(["generate", "--raptor-home", str(raptor_fixture), "--out-dir", str(out)]) == 0

    agent_files = sorted(path.name for path in (out / "agents").glob("*.md"))
    command_files = sorted(path.name for path in (out / "commands").glob("*.md"))
    assert agent_files == [
        "audit-reviewer.md",
        "coverage-analyzer.md",
        "crash-report-fetcher.md",
        "offsec-specialist.md",
        "oss-report-generator-agent.md",
    ]
    assert command_files == ["annotate.md", "ask.md", "audit.md", "scan.md", "version.md"]

    captured = capsys.readouterr()
    assert "generated 5 agents and 5 commands" in captured.out

    for path in [*(out / "agents").glob("*.md"), *(out / "commands").glob("*.md")]:
        content = path.read_text(encoding="utf-8")
        assert content.endswith("\n")
        assert parse_frontmatter(content)[0]


def test_generate_is_idempotent(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "opencode"
    args = ["generate", "--raptor-home", str(raptor_fixture), "--out-dir", str(out)]

    assert main(args) == 0
    first = {path.relative_to(out): path.read_text(encoding="utf-8") for path in out.rglob("*.md")}
    assert main(args) == 0
    second = {path.relative_to(out): path.read_text(encoding="utf-8") for path in out.rglob("*.md")}

    assert first == second
    capsys.readouterr()


def test_dry_run_writes_nothing(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "opencode"
    code = main(
        ["generate", "--raptor-home", str(raptor_fixture), "--out-dir", str(out), "--dry-run"]
    )
    assert code == 0
    assert not out.exists()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "would generate 5 agents and 5 commands" in captured.out


def test_missing_raptor_home_returns_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["generate", "--raptor-home", str(tmp_path / "nope"), "--dry-run"])
    assert code == 1
    assert "no RAPTOR checkout" in capsys.readouterr().err


def test_empty_checkout_returns_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".claude").mkdir()
    code = main(["generate", "--raptor-home", str(tmp_path), "--dry-run"])
    assert code == 1
    assert "nothing to generate" in capsys.readouterr().err


def test_unimplemented_generators_return_clear_error(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "generate",
            "--raptor-home",
            str(raptor_fixture),
            "--out-dir",
            str(tmp_path),
            "--with-skills",
        ]
    )
    assert code == 2
    assert "not implemented yet" in capsys.readouterr().err


def test_version_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.startswith("raptor-opencode ")
