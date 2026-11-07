"""End-to-end tests for the CLI (no subprocesses, no writes outside tmp_path)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor_opencode.cli import main
from raptor_opencode.parse_claude import parse_frontmatter

RAPTOR_SRC = Path("/tmp/opencode/raptor-src")
HOOKED_AGENTS = (
    "crash-report-fetcher",
    "oss-investigator-gh-archive-agent",
    "oss-investigator-github-agent",
    "oss-investigator-ioc-extractor-agent",
    "oss-investigator-wayback-agent",
)


def _generate_args(raptor: Path, out: Path, skills: Path) -> list[str]:
    return [
        "generate",
        "--raptor-home",
        str(raptor),
        "--out-dir",
        str(out),
        "--skills-dir",
        str(skills),
    ]


def _parse_jsonc(text: str) -> dict[str, object]:
    stripped = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("//"))
    return json.loads(stripped)


def test_generate_writes_every_artifact(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "opencode"
    skills = tmp_path / "skills"

    assert main(_generate_args(raptor_fixture, out, skills)) == 0

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
    assert (out / "raptor-policy.json").is_file()
    assert (out / "skills-map.json").is_file()
    assert (out / "opencode.fragment.jsonc").is_file()
    assert (out / "merge_notes.md").is_file()

    captured = capsys.readouterr()
    assert "generated 5 agents, 5 commands and 0 skills" in captured.out

    for path in [*(out / "agents").glob("*.md"), *(out / "commands").glob("*.md")]:
        content = path.read_text(encoding="utf-8")
        assert content.endswith("\n")
        assert parse_frontmatter(content)[0]

    policy = json.loads((out / "raptor-policy.json").read_text(encoding="utf-8"))
    assert policy["version"] == 1
    assert policy["agents"]["crash-report-fetcher"]["webfetch"]["domains"] == []
    fragment = _parse_jsonc((out / "opencode.fragment.jsonc").read_text(encoding="utf-8"))
    assert fragment["instructions"] == [str(raptor_fixture / "AGENTS.md")]


def test_generate_is_idempotent(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "opencode"
    skills = tmp_path / "skills"
    args = _generate_args(raptor_fixture, out, skills)

    assert main(args) == 0
    first = {
        path.relative_to(out): path.read_text(encoding="utf-8")
        for path in out.rglob("*")
        if path.is_file()
    }
    assert main(args) == 0
    second = {
        path.relative_to(out): path.read_text(encoding="utf-8")
        for path in out.rglob("*")
        if path.is_file()
    }

    assert first == second
    capsys.readouterr()


def test_no_config_skips_fragment(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "opencode"
    args = [*_generate_args(raptor_fixture, out, tmp_path / "skills"), "--no-config"]

    assert main(args) == 0

    assert not (out / "opencode.fragment.jsonc").exists()
    assert not (out / "merge_notes.md").exists()
    assert (out / "raptor-policy.json").is_file()
    capsys.readouterr()


def test_skills_are_generated_and_mapped(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import shutil

    source_root = tmp_path / "raptor"
    shutil.copytree(raptor_fixture, source_root)
    source = source_root / ".claude" / "skills" / "audit"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: audit\ndescription: Audit skill\n---\n\nAudit body\n", encoding="utf-8"
    )
    out = tmp_path / "opencode"
    skills = tmp_path / "skills"

    assert main(_generate_args(source_root, out, skills)) == 0

    assert (skills / "audit").is_symlink()
    mapping = json.loads((out / "skills-map.json").read_text(encoding="utf-8"))
    assert mapping["skills"]["audit"]["target"] == "audit"
    assert "1 skills" in capsys.readouterr().out


def test_dry_run_writes_nothing(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "opencode"
    skills = tmp_path / "skills"
    code = main([*_generate_args(raptor_fixture, out, skills), "--dry-run"])

    assert code == 0
    assert not out.exists()
    assert not skills.exists()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "would generate 5 agents, 5 commands" in captured.out


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


def test_policy_subcommand_emits_only_policy(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "opencode"

    assert main(["policy", "--raptor-home", str(raptor_fixture), "--out-dir", str(out)]) == 0

    assert sorted(path.name for path in out.iterdir()) == ["raptor-policy.json"]
    policy = json.loads((out / "raptor-policy.json").read_text(encoding="utf-8"))
    assert policy["agents"]["crash-report-fetcher"]["webfetch"][
        "anchor_file"
    ] is None or isinstance(
        policy["agents"]["crash-report-fetcher"]["webfetch"].get("anchor_file"), str
    )
    capsys.readouterr()


def test_policy_dry_run_writes_nothing(
    raptor_fixture: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "opencode"

    assert (
        main(
            [
                "policy",
                "--raptor-home",
                str(raptor_fixture),
                "--out-dir",
                str(out),
                "--dry-run",
            ]
        )
        == 0
    )

    assert not out.exists()
    assert "[dry-run]" in capsys.readouterr().out


def test_version_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.startswith("raptor-opencode ")


@pytest.mark.integration
@pytest.mark.skipif(
    not (RAPTOR_SRC / ".claude").is_dir(), reason="RAPTOR v3.1.0 checkout not available"
)
def test_full_pipeline_on_real_raptor(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "opencode"
    skills = tmp_path / "skills"

    assert main(_generate_args(RAPTOR_SRC, out, skills)) == 0

    assert len(list((out / "agents").glob("*.md"))) == 18
    assert len(list((out / "commands").glob("*.md"))) == 37

    policy = json.loads((out / "raptor-policy.json").read_text(encoding="utf-8"))
    assert policy["hooks"] == {
        "bash": ".claude/hooks/bash-command-allowlist.py",
        "webfetch": ".claude/hooks/webfetch-domain-allowlist.py",
    }
    for agent in HOOKED_AGENTS:
        assert agent in policy["agents"]
    assert policy["agents"]["oss-investigator-github-agent"]["webfetch"]["domains"] == [
        "github.com",
        "api.github.com",
        "raw.githubusercontent.com",
    ]
    assert policy["agents"]["oss-investigator-ioc-extractor-agent"]["webfetch"]["https_any"] is True
    assert policy["agents"]["crash-report-fetcher"]["webfetch"]["anchor_file"] == (
        ".claude/run/crash-report-fetcher.anchor"
    )
    assert policy["agents"]["oss-investigator-gh-archive-agent"]["bash"] == [
        "libexec/raptor-bq-query",
        "python3 .claude/skills/oss-forensics/github-evidence-kit/scripts/ingest_bq_events.py",
    ]
    assert policy["warnings"] == []

    fragment = _parse_jsonc((out / "opencode.fragment.jsonc").read_text(encoding="utf-8"))
    assert fragment["instructions"] == [str(RAPTOR_SRC / "AGENTS.md")]
    assert any(item["resource"] == "raptor-*" for item in fragment["permissions"])
    assert (out / "skills-map.json").is_file()
    mapping = json.loads((out / "skills-map.json").read_text(encoding="utf-8"))
    assert "coverage" in mapping["skills"]
    assert "tune" in mapping["skills"]
    assert (skills / "coverage" / "SKILL.md").is_file()
    capsys.readouterr()
