"""Resolve every directory an install may write to, for each install scope.

Two scopes exist:

``global``
    The historical layout: artifacts under ``$XDG_CONFIG_HOME/opencode``, skills
    under ``~/.agents/skills`` and the generated tree plus manifest under
    ``$XDG_DATA_HOME/raptor-opencode``.

``project``
    Everything stays inside the project directory (``--scope project``):
    agents, commands, plugins and the manifest under ``./.opencode``, skills
    under ``./.agents/skills`` and the configuration in the project config file.

Which project config file OpenCode reads was verified empirically with
OpenCode 2.2.6: ``opencode debug config`` reports a ``document`` source for
``./opencode.json``, ``./opencode.jsonc``, ``./.opencode/opencode.json`` and
``./.opencode/opencode.jsonc`` (in that order, later wins), and for no other
name. ``opencode.json`` is the canonical one; the others are reused only when
they are the sole candidate, and more than one candidate is an error instead of
a guess.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import RaptorOpencodeError
from .paths import data_dir, generated_dir, opencode_config_dir, skills_dir

Scope = Literal["global", "project"]
SCOPES: tuple[Scope, ...] = ("global", "project")

GLOBAL_MANIFEST_NAME = "install-manifest.json"
PROJECT_MANIFEST_NAME = "raptor-manifest.json"
PROJECT_OPENCODE_DIRNAME = ".opencode"
PROJECT_GENERATED_DIRNAME = "generated"
PROJECT_SKILLS_PARTS = (".agents", "skills")

#: Project config files OpenCode loads, in the order it loads them.
PROJECT_CONFIG_CANDIDATES: tuple[str, ...] = (
    "opencode.json",
    "opencode.jsonc",
    ".opencode/opencode.json",
    ".opencode/opencode.jsonc",
)
#: File used when the project has no OpenCode config yet.
DEFAULT_PROJECT_CONFIG = "opencode.json"
GLOBAL_CONFIG_CANDIDATES: tuple[str, ...] = ("opencode.jsonc", "opencode.json")


@dataclass(frozen=True, slots=True)
class Layout:
    """Every root an install/uninstall/doctor run may touch."""

    scope: Scope
    project_root: Path | None
    generated: Path
    opencode: Path
    skills: Path
    manifest: Path
    config: Path
    venv: Path

    def as_report(self) -> dict[str, str]:
        """Return the JSON-friendly path map used by ``doctor --json``."""
        return {
            "scope": self.scope,
            "generated_dir": str(self.generated),
            "opencode_dir": str(self.opencode),
            "skills_dir": str(self.skills),
            "manifest": str(self.manifest),
            "config": str(self.config),
            "venv": str(self.venv),
        }


def project_config_candidates(root: str | Path) -> list[Path]:
    """Return the project config files OpenCode would load, in load order."""
    base = Path(root)
    return [base / relative for relative in PROJECT_CONFIG_CANDIDATES]


def resolve_project_config(root: str | Path) -> Path:
    """Return the project config file to write.

    Raises:
        RaptorOpencodeError: when more than one candidate exists, because
            OpenCode would load all of them and picking one silently could
            split the configuration across files.
    """
    found = [path for path in project_config_candidates(root) if path.is_file()]
    if len(found) > 1:
        listed = ", ".join(str(path) for path in found)
        raise RaptorOpencodeError(
            f"ambiguous OpenCode project config in {Path(root)}: {listed}. OpenCode "
            "loads every one of them (later files win), so raptor-opencode refuses "
            "to guess which to edit. Keep a single project config file (preferably "
            f"{DEFAULT_PROJECT_CONFIG}) and rerun."
        )
    return found[0] if found else Path(root) / DEFAULT_PROJECT_CONFIG


def global_config_file(opencode_dir: str | Path) -> Path:
    """Return the existing global config file, or where a new one would go."""
    directory = Path(opencode_dir)
    for name in GLOBAL_CONFIG_CANDIDATES:
        if (directory / name).is_file():
            return directory / name
    return directory / GLOBAL_CONFIG_CANDIDATES[0]


def resolve_layout(
    scope: Scope = "global",
    *,
    cwd: str | Path | None = None,
    generated: str | Path | None = None,
    opencode: str | Path | None = None,
    skills: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Layout:
    """Build the :class:`Layout` for *scope*.

    Args:
        scope: ``global`` or ``project``.
        cwd: Project directory for ``project`` scope (default: the process
            working directory). Ignored by ``global``.
        generated: Override for the generated artifacts directory.
        opencode: Override for the OpenCode configuration directory.
        skills: Override for the skills directory.
        env: Environment mapping used for the ``XDG_*`` fallbacks.

    Raises:
        RaptorOpencodeError: on an unknown scope or an ambiguous project config.
    """
    environ = os.environ if env is None else env
    if scope not in SCOPES:
        raise RaptorOpencodeError(f"unknown scope {scope!r}; expected one of {', '.join(SCOPES)}")
    if scope == "global":
        opencode_dir = Path(opencode) if opencode is not None else opencode_config_dir(env=environ)
        generated_dir_ = Path(generated) if generated is not None else generated_dir(env=environ)
        skills_dir_ = Path(skills) if skills is not None else skills_dir(env=environ)
        return Layout(
            scope="global",
            project_root=None,
            generated=generated_dir_,
            opencode=opencode_dir,
            skills=skills_dir_,
            manifest=generated_dir_.parent / GLOBAL_MANIFEST_NAME,
            config=global_config_file(opencode_dir),
            venv=generated_dir_.parent / "venv",
        )

    root = Path(cwd) if cwd is not None else Path.cwd()
    root = root.resolve()
    opencode_dir = Path(opencode) if opencode is not None else root / PROJECT_OPENCODE_DIRNAME
    generated_dir_ = (
        Path(generated) if generated is not None else opencode_dir / PROJECT_GENERATED_DIRNAME
    )
    skills_dir_ = Path(skills) if skills is not None else root.joinpath(*PROJECT_SKILLS_PARTS)
    return Layout(
        scope="project",
        project_root=root,
        generated=generated_dir_,
        opencode=opencode_dir,
        skills=skills_dir_,
        manifest=opencode_dir / PROJECT_MANIFEST_NAME,
        config=resolve_project_config(root),
        # The virtualenv must never land inside the project (it would pollute
        # the workspace and the repository), so it lives in the data dir keyed
        # by the absolute project path.
        venv=data_dir(env=environ) / "venvs" / _project_slug(root),
    )


def _project_slug(root: Path) -> str:
    """Return a stable, filesystem-safe identifier for an absolute project path."""
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-") or "project"
    return f"{name}-{digest}"


__all__ = [
    "DEFAULT_PROJECT_CONFIG",
    "GLOBAL_CONFIG_CANDIDATES",
    "GLOBAL_MANIFEST_NAME",
    "PROJECT_CONFIG_CANDIDATES",
    "PROJECT_MANIFEST_NAME",
    "SCOPES",
    "Layout",
    "Scope",
    "global_config_file",
    "project_config_candidates",
    "resolve_layout",
    "resolve_project_config",
]
