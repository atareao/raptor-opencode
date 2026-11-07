"""Install generated RAPTOR artifacts into an OpenCode configuration.

Every write is bounded by the explicit ``--generated-dir``, ``--opencode-dir``
and ``--skills-dir`` targets (or their XDG defaults). The installer records
what it created in a manifest so ``uninstall`` can reverse it without deleting
user-owned directories.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import GenerationError, RaptorOpencodeError
from .gen_plugin import CORE_NAME, PLUGIN_NAME
from .paths import generated_dir as default_generated_dir
from .paths import opencode_config_dir
from .paths import raptor_home as resolve_raptor_home
from .paths import skills_dir as default_skills_dir
from .pipeline import generate_tree

DEFAULT_REF = "v3.1.0"
UPSTREAM_URL = "https://github.com/gadievron/raptor.git"
MANIFEST_NAME = "install-manifest.json"
MANIFEST_VERSION = 1
BACKUP_SUFFIX = "bak-raptor-opencode"
PLUGIN_FILES = (PLUGIN_NAME, CORE_NAME)

Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class InstallResult:
    """Outcome of an ``install`` run."""

    agents: int
    commands: int
    skills: int
    generated_dir: Path
    opencode_dir: Path
    skills_dir: Path
    manifest_path: Path
    links: int
    backups: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UninstallResult:
    """Outcome of an ``uninstall`` run."""

    removed_links: int
    restored_backups: int
    manifest_path: Path


@dataclass
class _Manifest:
    version: int = MANIFEST_VERSION
    ref: str = ""
    raptor_home: str = ""
    generated_dir: str = ""
    created_at: str = ""
    updated_at: str = ""
    mode: str = "symlink"
    links: list[dict[str, str]] = field(default_factory=list)
    backups: list[dict[str, str]] = field(default_factory=list)
    created_dirs: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "ref": self.ref,
            "raptor_home": self.raptor_home,
            "generated_dir": self.generated_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "mode": self.mode,
            "links": self.links,
            "backups": self.backups,
            "created_dirs": self.created_dirs,
            "config": self.config,
        }


def default_paths(
    *,
    raptor_home: str | Path | None = None,
    generated: str | Path | None = None,
    opencode: str | Path | None = None,
    skills: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, Path, Path, Path]:
    """Resolve the four install roots honouring ``HOME``/``XDG_*``."""
    return (
        resolve_raptor_home(raptor_home, env=env),
        Path(generated) if generated is not None else default_generated_dir(env=env),
        Path(opencode) if opencode is not None else opencode_config_dir(env=env),
        Path(skills) if skills is not None else default_skills_dir(env=env),
    )


def ensure_checkout(
    raptor_home: Path,
    ref: str,
    *,
    run: Runner = subprocess.run,
    dry_run: bool = False,
) -> None:
    """Clone or update the RAPTOR checkout, refusing to discard local changes."""
    if dry_run:
        return
    if not raptor_home.exists() or not any(raptor_home.iterdir()):
        raptor_home.parent.mkdir(parents=True, exist_ok=True)
        _run(
            run,
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                ref,
                UPSTREAM_URL,
                str(raptor_home),
            ],
        )
        return
    if not (raptor_home / ".git").is_dir():
        raise RaptorOpencodeError(
            f"{raptor_home} exists but is not a git checkout; move it away or pass "
            "another --raptor-home"
        )
    status = _run(
        run,
        ["git", "-C", str(raptor_home), "status", "--porcelain"],
        capture=True,
    )
    if status.strip():
        raise RaptorOpencodeError(
            f"{raptor_home} has local changes; commit or stash them before installing "
            "(raptor-opencode never resets a dirty checkout)"
        )
    _run(run, ["git", "-C", str(raptor_home), "fetch", "--tags"])
    _run(run, ["git", "-C", str(raptor_home), "checkout", "--detach", ref])


def ensure_dependencies(
    venv_dir: Path,
    requirements: Path,
    *,
    run: Runner = subprocess.run,
    skip: bool = False,
    dry_run: bool = False,
) -> Path | None:
    """Create the venv and install RAPTOR's requirements; return its Python."""
    if skip or dry_run:
        return None
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    _run(run, [sys.executable, "-m", "venv", str(venv_dir)])
    python = venv_dir / "bin" / "python"
    _run(run, [str(python), "-m", "pip", "install", "-r", str(requirements)])
    return python


def install(
    *,
    raptor_home: str | Path | None = None,
    ref: str = DEFAULT_REF,
    generated: str | Path | None = None,
    opencode: str | Path | None = None,
    skills: str | Path | None = None,
    copy: bool = False,
    dry_run: bool = False,
    yes: bool = False,
    merge_config: bool = False,
    skip_deps: bool = False,
    run: Runner = subprocess.run,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> InstallResult:
    """Install RAPTOR into OpenCode.

    ``yes`` is accepted by the CLI to acknowledge the prompt; programmatic
    callers are assumed to have confirmed already.
    """
    environ = os.environ if env is None else env
    root, generated_dir, opencode_dir, skills_dir = default_paths(
        raptor_home=raptor_home,
        generated=generated,
        opencode=opencode,
        skills=skills,
        env=environ,
    )
    manifest_path = generated_dir.parent / MANIFEST_NAME
    warnings: list[str] = []
    timestamp = _timestamp(now)

    if skip_deps:
        warnings.append("--skip-deps: RAPTOR_PYTHON will not be set")
    if dry_run:
        return InstallResult(
            agents=0,
            commands=0,
            skills=0,
            generated_dir=generated_dir,
            opencode_dir=opencode_dir,
            skills_dir=skills_dir,
            manifest_path=manifest_path,
            links=0,
            backups=0,
            warnings=tuple(warnings),
        )
    if not yes and not sys.stdin.isatty():
        raise RaptorOpencodeError("refusing a non-interactive install without --yes")

    ensure_checkout(root, ref, run=run)
    manifest = _load_manifest(manifest_path)
    created_dirs: list[str] = list(manifest.created_dirs)
    venv_dir = generated_dir.parent / "venv"
    _ensure_parent(venv_dir.parent, created_dirs)
    python = ensure_dependencies(
        venv_dir,
        root / "requirements.txt",
        run=run,
        skip=skip_deps,
    )

    tree = generate_tree(
        root,
        generated_dir,
        tag=ref,
        skills_mode="copy" if copy else "symlink",
        install_home=root,
        raptor_python=python,
        plugin_path=generated_dir / "plugins" / PLUGIN_NAME,
    )

    manifest.ref = ref
    manifest.raptor_home = str(root)
    manifest.generated_dir = str(generated_dir)
    manifest.updated_at = timestamp
    manifest.mode = "copy" if copy else "symlink"
    if not manifest.created_at:
        manifest.created_at = timestamp

    links = _plan_links(tree.paths, generated_dir, opencode_dir, skills_dir)
    previous = {(item["destination"], item["source"]) for item in manifest.links}
    for source, destination, kind in links:
        _place(
            source,
            destination,
            kind=kind,
            copy=copy,
            timestamp=timestamp,
            previous=previous,
            backups=manifest.backups,
            created_dirs=created_dirs,
        )
    manifest.links = [
        {"source": str(source), "destination": str(destination), "kind": kind}
        for source, destination, kind in links
    ]
    manifest.created_dirs = created_dirs

    if merge_config:
        _merge_into_opencode(
            tree.fragment,
            opencode_dir,
            timestamp=timestamp,
            manifest=manifest,
            created_dirs=created_dirs,
        )

    _write_json(manifest_path, manifest.to_dict(), created_dirs)
    return InstallResult(
        agents=tree.agents,
        commands=tree.commands,
        skills=tree.skills,
        generated_dir=generated_dir,
        opencode_dir=opencode_dir,
        skills_dir=skills_dir,
        manifest_path=manifest_path,
        links=len(links),
        backups=len(manifest.backups),
        warnings=tuple(warnings),
    )


def uninstall(
    *,
    generated: str | Path | None = None,
    opencode: str | Path | None = None,
    skills: str | Path | None = None,
    dry_run: bool = False,
    env: Mapping[str, str] | None = None,
) -> UninstallResult:
    """Reverse a previous install using its manifest."""
    environ = os.environ if env is None else env
    _root, generated_dir, _opencode_dir, _skills_dir = default_paths(
        generated=generated, opencode=opencode, skills=skills, env=environ
    )
    manifest_path = generated_dir.parent / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    if not manifest.links and not manifest.backups and not manifest.config:
        raise RaptorOpencodeError(f"no install manifest at {manifest_path}")

    removed = 0
    restored = 0
    if not dry_run:
        for item in manifest.links:
            destination = Path(item.get("destination", ""))
            source = Path(item.get("source", ""))
            if _is_our_symlink(destination, source):
                destination.unlink()
                removed += 1
            elif manifest.mode == "copy" and _same_content(destination, source):
                # Only remove copies that are still byte-identical to what we
                # generated; a file the user edited is theirs.
                _remove_owned(destination)
                removed += 1

        for item in manifest.backups:
            destination = Path(item.get("path", ""))
            backup = Path(item.get("backup", ""))
            if not backup.exists():
                continue
            if destination.exists() or destination.is_symlink():
                if not _is_our_symlink(destination, None):
                    continue
                destination.unlink()
            destination.parent.mkdir(parents=True, exist_ok=True)
            backup.rename(destination)
            restored += 1

        restored = _restore_config(manifest, restored)
        manifest_path.unlink(missing_ok=True)
        _remove_generated(generated_dir, manifest)

    return UninstallResult(
        removed_links=removed,
        restored_backups=restored,
        manifest_path=manifest_path,
    )


def _plan_links(
    paths: Any,
    generated_dir: Path,
    opencode_dir: Path,
    skills_dir: Path,
) -> list[tuple[Path, Path, str]]:
    links: list[tuple[Path, Path, str]] = []
    for kind, source_dir, target_dir, pattern in (
        ("agent", paths.agents, opencode_dir / "agents", "*.md"),
        ("command", paths.commands, opencode_dir / "commands", "*.md"),
    ):
        for source in sorted(source_dir.glob(pattern)):
            links.append((source, target_dir / source.name, kind))
    for name in PLUGIN_FILES:
        links.append((generated_dir / "plugins" / name, opencode_dir / "plugins" / name, "plugin"))
    if paths.generated_skills.is_dir():
        for skill in sorted(paths.generated_skills.iterdir()):
            if skill.is_dir() or skill.is_symlink():
                links.append((skill, skills_dir / skill.name, "skill"))
    return links


def _place(
    source: Path,
    destination: Path,
    *,
    kind: str,
    copy: bool,
    timestamp: str,
    previous: set[tuple[str, str]],
    backups: list[dict[str, str]],
    created_dirs: list[str],
) -> None:
    if not source.exists() and not source.is_symlink():
        raise GenerationError(f"generated artifact {source} is missing")
    _ensure_parent(destination.parent, created_dirs)
    is_previous = (str(destination), str(source)) in previous
    if destination.is_symlink() or destination.exists():
        if _is_our_symlink(destination, source) or is_previous:
            _remove_owned(destination)
        else:
            backup = destination.with_name(f"{destination.name}.{BACKUP_SUFFIX}-{timestamp}")
            if backup.exists():
                backup = destination.with_name(
                    f"{destination.name}.{BACKUP_SUFFIX}-{timestamp}-{len(backups)}"
                )
            destination.rename(backup)
            backups.append({"path": str(destination), "backup": str(backup)})
    if copy:
        resolved = source.resolve()
        if resolved.is_dir():
            shutil.copytree(resolved, destination)
        else:
            shutil.copy2(resolved, destination)
    else:
        destination.symlink_to(source, target_is_directory=source.is_dir())


def _remove_owned(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _same_content(left: Path, right: Path) -> bool:
    """Return whether two files or directory trees hold identical content."""
    if left.is_file() and right.is_file():
        try:
            return left.read_bytes() == right.read_bytes()
        except OSError:
            return False
    if left.is_dir() and right.is_dir():
        try:
            left_files = {path.relative_to(left) for path in left.rglob("*") if path.is_file()}
            right_files = {path.relative_to(right) for path in right.rglob("*") if path.is_file()}
        except OSError:
            return False
        if left_files != right_files:
            return False
        return all((left / name).read_bytes() == (right / name).read_bytes() for name in left_files)
    return False


def _is_our_symlink(path: Path, source: Path | None) -> bool:
    if not path.is_symlink():
        return False
    try:
        target = path.resolve(strict=False)
    except OSError:
        return False
    if source is None:
        return True
    try:
        return target == source.resolve(strict=False)
    except OSError:
        return False


def _ensure_parent(directory: Path, created_dirs: list[str]) -> None:
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for path in reversed(missing):
        path.mkdir()
        created_dirs.append(str(path))
    created_dirs[:] = list(dict.fromkeys(created_dirs))


def _load_manifest(path: Path) -> _Manifest:
    if not path.is_file():
        return _Manifest()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _Manifest()
    if not isinstance(raw, dict):
        return _Manifest()
    manifest = _Manifest()
    manifest.version = int(raw.get("version", MANIFEST_VERSION))
    manifest.ref = str(raw.get("ref", ""))
    manifest.raptor_home = str(raw.get("raptor_home", ""))
    manifest.generated_dir = str(raw.get("generated_dir", ""))
    manifest.created_at = str(raw.get("created_at", ""))
    manifest.updated_at = str(raw.get("updated_at", ""))
    manifest.mode = str(raw.get("mode", "symlink"))
    manifest.links = [item for item in raw.get("links", []) if isinstance(item, dict)]
    manifest.backups = [item for item in raw.get("backups", []) if isinstance(item, dict)]
    manifest.created_dirs = [str(item) for item in raw.get("created_dirs", [])]
    manifest.config = raw.get("config", {}) if isinstance(raw.get("config"), dict) else {}
    return manifest


def _write_json(
    path: Path,
    document: Mapping[str, Any],
    created_dirs: list[str] | None = None,
) -> None:
    if created_dirs is None:
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        _ensure_parent(path.parent, created_dirs)
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _timestamp(now: datetime | None) -> str:
    moment = now or datetime.now(UTC)
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _run(
    run: Runner,
    args: Sequence[str],
    *,
    capture: bool = False,
) -> str:
    result = run(
        list(args),
        check=False,
        capture_output=capture,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RaptorOpencodeError(
            f"command failed ({result.returncode}): {' '.join(args)}"
            + (f"\n{detail}" if detail else "")
        )
    return result.stdout or ""


def _merge_into_opencode(
    fragment_path: Path,
    opencode_dir: Path,
    *,
    timestamp: str,
    manifest: _Manifest,
    created_dirs: list[str],
) -> None:
    fragment = _loads_jsonc(fragment_path.read_text(encoding="utf-8"))
    if not isinstance(fragment, dict):
        raise GenerationError(f"{fragment_path}: fragment must be a JSON object")
    target = opencode_dir / "opencode.jsonc"
    _ensure_parent(target.parent, created_dirs)
    if target.is_file():
        original = target.read_text(encoding="utf-8")
        merged = merge_jsonc(original, fragment)
        if merged == original:
            return
        backup = target.with_name(f"{target.name}.{BACKUP_SUFFIX}-{timestamp}")
        suffix = 0
        while backup.exists():
            suffix += 1
            backup = target.with_name(f"{target.name}.{BACKUP_SUFFIX}-{timestamp}-{suffix}")
        target.rename(backup)
        target.write_text(merged, encoding="utf-8")
        manifest.config = {"path": str(target), "backup": str(backup)}
    else:
        target.write_text(fragment_path.read_text(encoding="utf-8"), encoding="utf-8")
        manifest.config = {"path": str(target), "created": True}


def _restore_config(manifest: _Manifest, restored: int) -> int:
    config = manifest.config
    path_raw = config.get("path")
    if not path_raw:
        return restored
    path = Path(str(path_raw))
    backup_raw = config.get("backup")
    if backup_raw:
        backup = Path(str(backup_raw))
        if backup.exists():
            path.unlink(missing_ok=True)
            backup.rename(path)
            return restored + 1
    elif config.get("created") and path.exists():
        path.unlink()
    return restored


def _remove_generated(generated_dir: Path, manifest: _Manifest) -> None:
    if generated_dir.exists():
        shutil.rmtree(generated_dir)
    venv = generated_dir.parent / "venv"
    if venv.exists():
        shutil.rmtree(venv)
    for raw in sorted(manifest.created_dirs, key=len, reverse=True):
        path = Path(raw)
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()


# --- minimal JSONC parser / comment-preserving merger -----------------------


class JsoncError(GenerationError):
    """Raised when a JSONC file cannot be parsed or merged safely."""


@dataclass(slots=True)
class _Node:
    start: int
    end: int
    value: Any
    members: list[tuple[str, _Node]] | None = None
    items: list[_Node] | None = None


_NUMBER = __import__("re").compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")


def _skip_trivia(text: str, index: int) -> int:
    size = len(text)
    while index < size:
        char = text[index]
        if char in " \t\r\n":
            index += 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = size if newline < 0 else newline + 1
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                raise JsoncError("unterminated block comment")
            index = end + 2
        else:
            break
    return index


def _parse_document(text: str) -> _Node:
    if text.startswith("\ufeff"):
        text = text[1:]
    node, index = _parse_value(text, _skip_trivia(text, 0))
    trailing = _skip_trivia(text, index)
    if trailing != len(text):
        raise JsoncError(f"unexpected data at offset {trailing}")
    return node


def _parse_value(text: str, index: int) -> tuple[_Node, int]:
    index = _skip_trivia(text, index)
    if index >= len(text):
        raise JsoncError("unexpected end of document")
    char = text[index]
    if char == "{":
        return _parse_object(text, index)
    if char == "[":
        return _parse_array(text, index)
    if char == '"':
        value, end = _parse_string(text, index)
        return _Node(index, end, value), end
    for literal, value in (("true", True), ("false", False), ("null", None)):
        if text.startswith(literal, index):
            return _Node(index, index + len(literal), value), index + len(literal)
    match = _NUMBER.match(text, index)
    if match:
        return _Node(index, match.end(), json.loads(match.group(0))), match.end()
    raise JsoncError(f"unexpected character {char!r} at offset {index}")


def _parse_string(text: str, index: int) -> tuple[str, int]:
    end = index + 1
    while end < len(text):
        if text[end] == "\\":
            end += 2
            continue
        if text[end] == '"':
            end += 1
            try:
                return json.loads(text[index:end]), end
            except ValueError as exc:
                raise JsoncError(f"invalid string at offset {index}: {exc}") from exc
        end += 1
    raise JsoncError("unterminated string")


def _parse_object(text: str, index: int) -> tuple[_Node, int]:
    start = index
    index = _skip_trivia(text, index + 1)
    members: list[tuple[str, _Node]] = []
    values: dict[str, Any] = {}
    if index < len(text) and text[index] == "}":
        return _Node(start, index + 1, values, members=members), index + 1
    while True:
        if index >= len(text) or text[index] != '"':
            raise JsoncError(f"expected object key at offset {index}")
        key, index = _parse_string(text, index)
        index = _skip_trivia(text, index)
        if index >= len(text) or text[index] != ":":
            raise JsoncError(f"expected ':' after {key!r}")
        value, index = _parse_value(text, index + 1)
        members.append((key, value))
        values[key] = value.value
        index = _skip_trivia(text, index)
        if index < len(text) and text[index] == ",":
            index = _skip_trivia(text, index + 1)
            if index < len(text) and text[index] == "}":
                return _Node(start, index + 1, values, members=members), index + 1
            continue
        if index < len(text) and text[index] == "}":
            return _Node(start, index + 1, values, members=members), index + 1
        raise JsoncError(f"expected ',' or '}}' at offset {index}")


def _parse_array(text: str, index: int) -> tuple[_Node, int]:
    start = index
    index = _skip_trivia(text, index + 1)
    items: list[_Node] = []
    if index < len(text) and text[index] == "]":
        return _Node(start, index + 1, [], items=items), index + 1
    while True:
        value, index = _parse_value(text, index)
        items.append(value)
        index = _skip_trivia(text, index)
        if index < len(text) and text[index] == ",":
            index = _skip_trivia(text, index + 1)
            if index < len(text) and text[index] == "]":
                return _Node(
                    start, index + 1, [item.value for item in items], items=items
                ), index + 1
            continue
        if index < len(text) and text[index] == "]":
            return _Node(start, index + 1, [item.value for item in items], items=items), index + 1
        raise JsoncError(f"expected ',' or ']' at offset {index}")


def _loads_jsonc(text: str) -> Any:
    return _parse_document(text).value


def loads_jsonc(text: str) -> Any:
    """Parse JSONC (comments and trailing commas) without rewriting the text."""
    return _parse_document(text).value


def merge_jsonc(text: str, fragment: Mapping[str, Any]) -> str:
    """Merge *fragment* into *text*, preserving comments and foreign keys.

    Only insertions and our own scalar updates are performed. If the result
    cannot be verified, generation aborts instead of writing a lossy config.
    """
    original = _loads_jsonc(text)
    if not isinstance(original, dict):
        raise JsoncError("the OpenCode config must be a JSON object")
    expected = _deep_merge(original, fragment)
    merged = text
    for key, value in fragment.items():
        merged = _merge_top_member(merged, key, value)
    if _loads_jsonc(merged) != expected:
        raise JsoncError(
            "cannot merge the fragment without losing information; merge "
            "opencode.fragment.jsonc by hand"
        )
    return merged


def _deep_merge(base: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in incoming.items():
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = _deep_merge(current, value)
        elif isinstance(value, list) and isinstance(current, list):
            for item in value:
                if item not in current:
                    current.append(copy.deepcopy(item))
        else:
            result[key] = copy.deepcopy(value)
    return result


def _find_node(root: _Node, start: int) -> _Node:
    if root.start == start:
        return root
    for child in (root.members or []) + [(None, item) for item in (root.items or [])]:
        node = child[1]  # type: ignore[index]
        try:
            return _find_node(node, start)
        except KeyError:
            continue
    raise KeyError(start)


def _find_member(node: _Node, key: str) -> _Node | None:
    for name, child in node.members or []:
        if name == key:
            return child
    return None


def _merge_top_member(text: str, key: str, value: Any) -> str:
    root = _parse_document(text)
    member = _find_member(root, key)
    if member is None:
        return _insert_member(text, root, key, value)
    return _merge_node(text, member, value)


def _merge_node(text: str, node: _Node, value: Any) -> str:
    if isinstance(value, dict) and isinstance(node.value, dict):
        updated = text
        for key, subvalue in value.items():
            updated = _merge_member_at(updated, node.start, key, subvalue)
        return updated
    if isinstance(value, list) and isinstance(node.value, list):
        updated = text
        for item in value:
            current = _find_node(_parse_document(updated), node.start)
            if item in (current.value or []):
                continue
            updated = _insert_array_item(updated, current, item)
        return updated
    if node.value == value:
        return text
    return text[: node.start] + json.dumps(value, ensure_ascii=False) + text[node.end :]


def _merge_member_at(text: str, object_start: int, key: str, value: Any) -> str:
    root = _parse_document(text)
    obj = _find_node(root, object_start)
    member = _find_member(obj, key)
    if member is None:
        return _insert_member(text, obj, key, value)
    return _merge_node(text, member, value)


def _insert_member(text: str, obj: _Node, key: str, value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False)
    insert_at = obj.end - 1
    if not obj.members:
        insertion = f'\n  "{key}": {payload}\n'
    else:
        tail = text[(obj.members[-1][1].end) : insert_at]
        comma = "" if _has_comma(tail) else ","
        insertion = f'{comma}\n  "{key}": {payload}'
    return text[:insert_at] + insertion + text[insert_at:]


def _insert_array_item(text: str, node: _Node, item: Any) -> str:
    payload = json.dumps(item, ensure_ascii=False)
    insert_at = node.end - 1
    if not node.items:
        insertion = f"\n  {payload}\n"
    else:
        tail = text[(node.items[-1].end) : insert_at]
        comma = "" if _has_comma(tail) else ","
        insertion = f"{comma}\n  {payload}"
    return text[:insert_at] + insertion + text[insert_at:]


def _has_comma(tail: str) -> bool:
    stripped = _strip_comments(tail)
    return "," in stripped


def _strip_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end < 0 else end + 2
            continue
        if text[index] == '"':
            end = index + 1
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == '"':
                    end += 1
                    break
                end += 1
            result.append(text[index:end])
            index = end
            continue
        result.append(text[index])
        index += 1
    return "".join(result)


__all__ = [
    "DEFAULT_REF",
    "InstallResult",
    "JsoncError",
    "UninstallResult",
    "default_paths",
    "ensure_checkout",
    "ensure_dependencies",
    "install",
    "loads_jsonc",
    "merge_jsonc",
    "uninstall",
]
