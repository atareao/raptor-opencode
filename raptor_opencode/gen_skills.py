"""Mirror RAPTOR skills into the OpenCode skills directory.

OpenCode discovers skills as ``<skills-dir>/<name>/SKILL.md``. RAPTOR's
``.claude/skills`` tree mostly follows that layout, but it also contains loose
``.md`` files and nested skill directories. This module:

* symlinks (default) or copies every top-level skill directory, preserving
  relative paths and helper scripts;
* wraps loose ``.md`` files in ``<name>/SKILL.md`` because a file cannot be a
  skill;
* guarantees ``name`` and ``description`` in every materialised ``SKILL.md``
  without touching the body;
* renames colliding skills with a ``raptor-`` prefix and records the mapping in
  ``skills-map.json``.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .errors import GenerationError
from .gen_agents import slugify
from .parse_claude import format_scalar, parse_frontmatter

SKILLS_MAP_VERSION = 1
DEFAULT_MODE: Literal["symlink", "copy"] = "symlink"
_COLLISION_PREFIX = "raptor-"
_DESCRIPTION_LIMIT = 240


@dataclass(frozen=True, slots=True)
class SkillSource:
    """A top-level entry of RAPTOR's skills directory."""

    name: str
    path: Path
    kind: Literal["directory", "file"]


@dataclass(frozen=True, slots=True)
class SkillPlan:
    """What the generator intends to create for one source skill."""

    source: SkillSource
    target: str
    renamed: bool
    strategy: Literal["symlink", "copy", "materialise", "file"]


def discover_skills(source_dir: str | Path) -> list[SkillSource]:
    """Return every top-level directory and ``*.md`` file in *source_dir*."""
    root = Path(source_dir)
    if not root.is_dir():
        return []
    sources: list[SkillSource] = []
    seen: dict[str, Path] = {}
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            kind: Literal["directory", "file"] = "directory"
        elif entry.suffix == ".md" and entry.is_file():
            kind = "file"
        else:
            continue
        skill = SkillSource(
            name=entry.stem if kind == "file" else entry.name, path=entry, kind=kind
        )
        if skill.name in seen:
            raise GenerationError(
                f"duplicate skill name {skill.name!r}: {seen[skill.name]} and {entry}"
            )
        seen[skill.name] = entry
        sources.append(skill)
    return sources


def plan_skills(
    source_dir: str | Path,
    skills_dir: str | Path,
    *,
    mode: Literal["symlink", "copy"] = DEFAULT_MODE,
    collision_dir: str | Path | None = None,
    previous_map: Mapping[str, Any] | None = None,
) -> list[SkillPlan]:
    """Decide target names and strategies for every source skill.

    *skills_dir* is the (owned) output directory; nothing outside it is
    written. Collisions are only checked against *collision_dir*, an optional
    read-only directory such as the user's global skills folder. Without it,
    the generated tree never renames anything.
    """
    _validate_mode(mode)
    collision_root = Path(collision_dir) if collision_dir is not None else None
    previous = _previous_entries(previous_map)
    owned = {entry.get("target") for entry in previous.values() if entry.get("target")}
    existing = _existing_names(collision_root) if collision_root is not None else set()
    plans: list[SkillPlan] = []
    claimed: dict[str, Path] = {}

    for skill in discover_skills(source_dir):
        target = skill.name
        renamed = False
        if (
            collision_root is not None
            and target in existing
            and not _is_ours(collision_root / target, skill.path, owned)
        ):
            target = f"{_COLLISION_PREFIX}{target}"
            renamed = True
        if target in claimed:
            raise GenerationError(
                f"skills {claimed[target]} and {skill.path} both map to {target!r}"
            )
        if (
            collision_root is not None
            and target in existing
            and not _is_ours(collision_root / target, skill.path, owned)
        ):
            raise GenerationError(
                f"cannot place skill {skill.name!r}: {collision_root / target} is "
                "already owned by another package; remove it or pass another "
                "--collision-dir"
            )
        claimed[target] = skill.path
        plans.append(
            SkillPlan(
                source=skill,
                target=target,
                renamed=renamed,
                strategy=_strategy(skill, mode),
            )
        )
    return plans


def generate_skills(
    source_dir: str | Path,
    skills_dir: str | Path,
    *,
    raptor_home: str | Path,
    mode: Literal["symlink", "copy"] = DEFAULT_MODE,
    collision_dir: str | Path | None = None,
    previous_map: Mapping[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Materialise every skill under *skills_dir* and return the skills map.

    All writes stay below *skills_dir*. *collision_dir* is only inspected, never
    modified. The caller owns ``skills-map.json`` and any install-time copy to
    the global skills directory.
    """
    root = Path(raptor_home)
    target_root = Path(skills_dir)
    plans = plan_skills(
        source_dir,
        target_root,
        mode=mode,
        collision_dir=collision_dir,
        previous_map=previous_map,
    )
    previous = _previous_entries(previous_map)

    for plan in plans:
        target = target_root / plan.target
        if dry_run:
            continue
        target_root.mkdir(parents=True, exist_ok=True)
        _remove_obsolete(target, plan, previous)
        if target.exists() or target.is_symlink():
            _remove_path(target)
        _materialise(plan, target, mode)

    entries: dict[str, Any] = {}
    renames: dict[str, str] = {}
    for plan in plans:
        entries[plan.source.name] = {
            "target": plan.target,
            "source": _relative(plan.source.path, root),
            "kind": plan.source.kind,
            "renamed": plan.renamed,
        }
        if plan.renamed:
            renames[plan.source.name] = plan.target

    return {
        "version": SKILLS_MAP_VERSION,
        "skills": {name: entries[name] for name in sorted(entries)},
        "renames": {name: renames[name] for name in sorted(renames)},
    }


def write_skills_map(document: Mapping[str, Any], destination: str | Path) -> Path:
    """Write *document* as JSON and return the path written."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def normalize_skill_text(text: str, fallback_name: str) -> str:
    """Ensure ``name`` and ``description`` exist, preserving the body verbatim.

    Existing keys and every other frontmatter line are kept as-is. When both
    fields are already present the original text is returned unchanged.
    """
    frontmatter, _ = parse_frontmatter(text)
    if _has_text(frontmatter.get("name")) and _has_text(frontmatter.get("description")):
        return text

    frontmatter_raw, body = _split_raw_frontmatter(text)
    name = frontmatter.get("name")
    if not _has_text(name):
        name = _derive_name(body, fallback_name)
    description = frontmatter.get("description")
    if not _has_text(description):
        description = _derive_description(body, fallback_name)

    lines = frontmatter_raw.rstrip("\n").split("\n") if frontmatter_raw.strip() else []
    lines = _ensure_key(lines, "name", str(name))
    lines = _ensure_key(lines, "description", str(description))
    rendered = "\n".join(line for line in lines if line != "")

    prefix = "---\n"
    if rendered:
        prefix += rendered + "\n"
    prefix += "---\n"
    if not body.startswith("\n"):
        prefix += "\n"
    return prefix + body


def _materialise(plan: SkillPlan, target: Path, mode: str) -> None:
    source = plan.source
    if plan.strategy == "file":
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text(
            normalize_skill_text(source.path.read_text(encoding="utf-8"), source.name),
            encoding="utf-8",
        )
        return
    if plan.strategy == "copy":
        shutil.copytree(source.path, target)
        _normalize_tree(target)
        return
    if plan.strategy == "materialise":
        _materialise_normalized_dir(source.path, target)
        return
    try:
        target.symlink_to(source.path.resolve(), target_is_directory=True)
    except OSError:
        if mode != "symlink":
            raise
        shutil.copytree(source.path, target)
        _normalize_tree(target)


def _materialise_normalized_dir(source: Path, target: Path) -> None:
    """Create a real directory with a normalised SKILL.md and linked contents."""
    target.mkdir(parents=True)
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        destination = target / child.name
        if child.name == "SKILL.md":
            destination.write_text(
                normalize_skill_text(child.read_text(encoding="utf-8"), source.name),
                encoding="utf-8",
            )
            continue
        try:
            destination.symlink_to(child.resolve(), target_is_directory=child.is_dir())
        except OSError:
            if child.is_dir():
                shutil.copytree(child, destination)
            else:
                shutil.copy2(child, destination)


def _strategy(skill: SkillSource, mode: str) -> Literal["symlink", "copy", "materialise", "file"]:
    if skill.kind == "file":
        return "file"
    if mode == "copy":
        return "copy"
    skill_md = skill.path / "SKILL.md"
    if skill_md.is_file() and _needs_normalisation(skill_md):
        return "materialise"
    return "symlink"


def _needs_normalisation(path: Path) -> bool:
    frontmatter, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return not _has_text(frontmatter.get("name")) or not _has_text(frontmatter.get("description"))


def _normalize_tree(root: Path) -> None:
    for path in sorted(root.rglob("SKILL.md")):
        original = path.read_text(encoding="utf-8")
        normalized = normalize_skill_text(original, path.parent.name)
        if normalized != original:
            path.write_text(normalized, encoding="utf-8")


def _split_raw_frontmatter(text: str) -> tuple[str, str]:
    """Return ``(frontmatter_text, body)`` preserving the body byte-for-byte."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "".join(lines[1:index]), "".join(lines[index + 1 :])
    raise GenerationError("unterminated frontmatter while normalising a skill")


def _ensure_key(lines: list[str], key: str, value: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    formatted = f"{key}: {format_scalar(value)}"
    for index, line in enumerate(lines):
        if pattern.match(line):
            lines[index] = formatted
            return lines
    lines.append(formatted)
    return lines


def _derive_name(body: str, fallback: str) -> str:
    heading = _first_heading(body)
    if heading:
        try:
            return slugify(heading)
        except GenerationError:
            pass
    try:
        return slugify(fallback)
    except GenerationError as exc:
        raise GenerationError(f"cannot derive a skill name from {fallback!r}") from exc


def _derive_description(body: str, fallback: str) -> str:
    heading: str | None = None
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if heading is None:
                heading = stripped.lstrip("#").strip()
            continue
        text = re.sub(r"^[-*+]\s+", "", stripped)
        text = re.sub(r"[`*_>#]", "", text).strip()
        if text:
            return _truncate(text)
    if heading:
        return _truncate(heading)
    return _truncate(fallback.replace("-", " "))


def _first_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            text = stripped.lstrip("#").strip()
            if text:
                return text
    return None


def _truncate(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _DESCRIPTION_LIMIT:
        return collapsed
    return collapsed[: _DESCRIPTION_LIMIT - 1].rstrip() + "…"


def _validate_mode(mode: str) -> None:
    if mode not in {"symlink", "copy"}:
        raise GenerationError(f"unsupported skills mode {mode!r} (use 'symlink' or 'copy')")


def _previous_entries(previous_map: Mapping[str, Any] | None) -> dict[str, Any]:
    if not previous_map:
        return {}
    skills = previous_map.get("skills")
    if not isinstance(skills, Mapping):
        return {}
    return {str(key): value for key, value in skills.items() if isinstance(value, Mapping)}


def _existing_names(target_root: Path) -> set[str]:
    if not target_root.is_dir():
        return set()
    return {entry.name for entry in target_root.iterdir()}


def _is_ours(target: Path, source: Path, owned: set[str]) -> bool:
    if target.name in owned:
        return True
    if not target.is_symlink():
        return False
    try:
        resolved = target.resolve()
    except OSError:
        return False
    source_root = source.parent.resolve()
    return resolved == source_root or source_root in resolved.parents


def _remove_obsolete(target: Path, plan: SkillPlan, previous: Mapping[str, Any]) -> None:
    entry = previous.get(plan.source.name)
    if not isinstance(entry, Mapping):
        return
    previous_target = entry.get("target")
    if not isinstance(previous_target, str) or previous_target == plan.target:
        return
    obsolete = target.parent / previous_target
    if obsolete.is_symlink() or obsolete.exists():
        _remove_path(obsolete)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


__all__ = [
    "SkillPlan",
    "SkillSource",
    "discover_skills",
    "generate_skills",
    "normalize_skill_text",
    "plan_skills",
    "write_skills_map",
]
