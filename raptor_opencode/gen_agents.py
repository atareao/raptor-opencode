"""Convert Claude Code subagents (``.claude/agents/*.md``) to OpenCode agents.

The conversion keeps the original system prompt verbatim and rewrites only the
frontmatter:

* ``description`` is preserved (it is what OpenCode shows when choosing a
  subagent);
* ``mode: subagent`` makes the file usable only through the task tool;
* ``tools`` becomes a map of allowed OpenCode tool names;
* ``permission`` denies ``bash``/``edit`` when the Claude definition did not
  grant them, so a restrictive agent stays restrictive.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import GenerationError
from .parse_claude import dump_frontmatter, parse_file
from .toolmap import map_tools


@dataclass(frozen=True, slots=True)
class Agent:
    """A generated OpenCode agent file."""

    slug: str
    content: str
    source: Path


def slugify(value: str) -> str:
    """Return a lowercase, hyphen-separated identifier from *value*."""
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    if not slug:
        raise GenerationError(f"cannot derive a slug from {value!r}")
    return slug


def load_agents(agents_dir: Path) -> list[Agent]:
    """Load every ``*.md`` in *agents_dir*, sorted by filename."""
    if not agents_dir.is_dir():
        return []
    agents = [agent_from_file(path) for path in sorted(agents_dir.glob("*.md"))]
    seen: dict[str, Path] = {}
    for agent in agents:
        if agent.slug in seen:
            raise GenerationError(
                f"duplicate agent slug {agent.slug!r}: {seen[agent.slug]} and {agent.source}"
            )
        seen[agent.slug] = agent.source
    return agents


def agent_from_file(path: Path) -> Agent:
    """Read *path* and build the corresponding OpenCode agent."""
    frontmatter, body = parse_file(path)
    name = frontmatter.get("name", path.stem)
    if not isinstance(name, str) or not name.strip():
        raise GenerationError(f"{path}: 'name' must be a non-empty string")
    slug = slugify(name)
    return Agent(slug=slug, content=render_agent(frontmatter, body, source=path), source=path)


def render_agent(frontmatter: dict[str, Any], body: str, *, source: Path | None = None) -> str:
    """Render an OpenCode agent Markdown document."""
    description = frontmatter.get("description")
    if not isinstance(description, str) or not description.strip():
        where = f"{source}: " if source else ""
        raise GenerationError(f"{where}missing required 'description' field")

    data: dict[str, Any] = {
        "description": description.strip(),
        "mode": "subagent",
    }

    tools = _allowed_tools(frontmatter.get("tools"), source)
    if tools is not None:
        data["tools"] = {tool: True for tool in tools}
        permission = _permission(tools)
        if permission:
            data["permission"] = permission

    cleaned_body = body.strip("\n")
    if not cleaned_body:
        raise GenerationError(f"{source or '<memory>'}: agent body is empty")
    return f"{dump_frontmatter(data)}\n\n{cleaned_body}\n"


def _allowed_tools(raw: Any, source: Path | None) -> list[str] | None:
    if raw is None:
        return None
    where = f"{source}: " if source else ""
    if isinstance(raw, str):
        entries = [entry.strip() for entry in raw.split(",") if entry.strip()]
    elif isinstance(raw, list | tuple):
        entries = [str(entry).strip() for entry in raw if str(entry).strip()]
    else:
        raise GenerationError(
            f"{where}'tools' must be a string or a list, got {type(raw).__name__}"
        )
    return map_tools(entries)


def _permission(tools: list[str]) -> dict[str, str]:
    permission: dict[str, str] = {}
    if "bash" not in tools:
        permission["bash"] = "deny"
    if "edit" not in tools and "write" not in tools:
        permission["edit"] = "deny"
    return permission
