"""Tests for the skills tree generator (``gen_skills``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor_opencode.errors import GenerationError
from raptor_opencode.gen_skills import (
    discover_skills,
    generate_skills,
    normalize_skill_text,
    write_skills_map,
)
from raptor_opencode.parse_claude import parse_frontmatter


def _valid_skill(root: Path, name: str, body: str = "Body\n") -> Path:
    skill = root / name
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Skill {name}\n---\n\n{body}", encoding="utf-8"
    )
    (skill / "scripts" / "run.sh").write_text("#!/bin/sh\necho run\n", encoding="utf-8")
    return skill


def _body_after_frontmatter(text: str) -> str:
    _, body = parse_frontmatter(text)
    return body


def test_symlinks_valid_directories_and_preserves_scripts(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    _valid_skill(source, "skill-a")
    out = tmp_path / "skills"

    document = generate_skills(source, out, raptor_home=tmp_path / "raptor")

    target = out / "skill-a"
    assert target.is_symlink()
    assert target.resolve() == (source / "skill-a").resolve()
    assert (target / "scripts" / "run.sh").read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert document["skills"]["skill-a"] == {
        "target": "skill-a",
        "source": ".claude/skills/skill-a",
        "kind": "directory",
        "renamed": False,
    }
    assert document["renames"] == {}


def test_loose_markdown_file_becomes_wrapped_skill(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    source.mkdir(parents=True)
    original = "# Loose Skill\n\nFirst line description.\n\nMore body.\n"
    (source / "loose.md").write_text(original, encoding="utf-8")
    out = tmp_path / "skills"

    generate_skills(source, out, raptor_home=tmp_path / "raptor")

    skill_md = out / "loose" / "SKILL.md"
    assert skill_md.is_file()
    frontmatter, body = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
    assert frontmatter["name"] == "loose-skill"
    assert frontmatter["description"] == "First line description."
    assert body == original


def test_loose_file_with_existing_frontmatter_keeps_body_byte_for_byte(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    source.mkdir(parents=True)
    raw = (
        "---\n"
        "name: coverage\n"
        "description: Coverage tracking\n"
        "user-invocable: false\n"
        "---\n"
        "\n"
        "# Coverage\n"
        "\n"
        "Body with  double  spaces.\n"
    )
    (source / "coverage.md").write_text(raw, encoding="utf-8")
    out = tmp_path / "skills"

    generate_skills(source, out, raptor_home=tmp_path / "raptor")

    output = (out / "coverage" / "SKILL.md").read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(output)
    assert frontmatter["name"] == "coverage"
    assert frontmatter["description"] == "Coverage tracking"
    assert frontmatter["user-invocable"] is False
    assert body == _body_after_frontmatter(raw)
    assert "# Coverage\n\nBody with  double  spaces.\n" in output


def test_nested_skill_directories_are_reachable_through_symlink(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    nested = source / "crash" / "sub"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text(
        "---\nname: crash-sub\ndescription: Nested\n---\n\nNested body\n", encoding="utf-8"
    )
    out = tmp_path / "skills"

    generate_skills(source, out, raptor_home=tmp_path / "raptor")

    assert (out / "crash").is_symlink()
    assert (out / "crash" / "sub" / "SKILL.md").is_file()


def test_missing_description_is_added_without_rewriting_body(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    skill = source / "needs"
    skill.mkdir(parents=True)
    raw = "---\nname: needs\nuser-invocable: false\n---\n\n# Needs\n\nKeep this body.\n"
    (skill / "SKILL.md").write_text(raw, encoding="utf-8")
    (skill / "helper.py").write_text("print('hi')\n", encoding="utf-8")
    out = tmp_path / "skills"

    generate_skills(source, out, raptor_home=tmp_path / "raptor")

    target = out / "needs"
    assert not target.is_symlink()
    output = (target / "SKILL.md").read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(output)
    assert frontmatter["name"] == "needs"
    assert frontmatter["description"] == "Keep this body."
    assert frontmatter["user-invocable"] is False
    assert body == _body_after_frontmatter(raw)
    assert (target / "helper.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_copy_mode_copies_and_normalises_recursively(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    _valid_skill(source, "skill-a")
    nested = source / "skill-a" / "nested"
    nested.mkdir()
    (nested / "SKILL.md").write_text("---\nname: nested\n---\n\nNested body\n", encoding="utf-8")
    out = tmp_path / "skills"

    generate_skills(source, out, raptor_home=tmp_path / "raptor", mode="copy")

    target = out / "skill-a"
    assert not target.is_symlink()
    assert (target / "scripts" / "run.sh").is_file()
    frontmatter, body = parse_frontmatter((target / "nested" / "SKILL.md").read_text())
    assert frontmatter["description"]
    assert body == "Nested body\n"


def test_collision_gets_raptor_prefix_and_is_recorded(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    _valid_skill(source, "audit")
    out = tmp_path / "skills"
    foreign = out / "audit"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("foreign\n", encoding="utf-8")

    document = generate_skills(source, out, raptor_home=tmp_path / "raptor")

    assert (out / "raptor-audit").is_symlink()
    assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "foreign\n"
    assert document["skills"]["audit"]["target"] == "raptor-audit"
    assert document["skills"]["audit"]["renamed"] is True
    assert document["renames"] == {"audit": "raptor-audit"}


def test_previous_map_marks_generated_entries_as_ours(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    _valid_skill(source, "audit")
    out = tmp_path / "skills"
    first = generate_skills(source, out, raptor_home=tmp_path / "raptor")
    second = generate_skills(source, out, raptor_home=tmp_path / "raptor", previous_map=first)

    assert second["skills"]["audit"]["target"] == "audit"
    assert (out / "audit").is_symlink()


def test_duplicate_skill_names_fail_loudly(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    _valid_skill(source, "dup")
    (source / "dup.md").write_text("# Dup\n", encoding="utf-8")

    with pytest.raises(GenerationError, match="duplicate skill name"):
        discover_skills(source)


def test_unknown_mode_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(GenerationError, match="unsupported skills mode"):
        generate_skills(tmp_path / "src", tmp_path / "out", raptor_home=tmp_path, mode="hard")


def test_normalize_keeps_complete_frontmatter_untouched() -> None:
    raw = "---\nname: x\ndescription: y\n---\n\nBody\n"
    assert normalize_skill_text(raw, "fallback") == raw


def test_write_skills_map_is_valid_json(tmp_path: Path) -> None:
    document = {"version": 1, "skills": {}, "renames": {}}
    path = write_skills_map(document, tmp_path / "nested" / "skills-map.json")

    assert json.loads(path.read_text(encoding="utf-8")) == document


def test_dry_run_does_not_touch_filesystem(tmp_path: Path) -> None:
    source = tmp_path / "raptor" / ".claude" / "skills"
    _valid_skill(source, "skill-a")
    out = tmp_path / "skills"

    document = generate_skills(source, out, raptor_home=tmp_path / "raptor", dry_run=True)

    assert "skill-a" in document["skills"]
    assert not out.exists()
