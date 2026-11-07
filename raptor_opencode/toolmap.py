"""Translate Claude Code tool names into their OpenCode equivalents.

Claude and OpenCode agree on most built-in tools but not on their spelling.
A name with no mapping is always an error: silently dropping a tool would
produce an agent with permissions nobody asked for.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

from .errors import UnknownToolError

CLAUDE_TO_OPENCODE: MappingProxyType[str, str] = MappingProxyType(
    {
        "Bash": "bash",
        "Read": "read",
        "Write": "write",
        "Edit": "edit",
        "Grep": "grep",
        "Glob": "glob",
        "WebFetch": "webfetch",
        "Task": "task",
        "NotebookEdit": "edit",
    }
)


def map_tool(name: str) -> str:
    """Return the OpenCode name for a Claude tool.

    Raises:
        UnknownToolError: *name* is not in :data:`CLAUDE_TO_OPENCODE`.
    """
    cleaned = name.strip()
    try:
        return CLAUDE_TO_OPENCODE[cleaned]
    except KeyError:
        known = ", ".join(sorted(CLAUDE_TO_OPENCODE))
        raise UnknownToolError(f"unknown Claude tool {cleaned!r}; known tools: {known}") from None


def map_tools(tools: Iterable[str]) -> list[str]:
    """Map *tools* in order, dropping duplicates that map to the same tool."""
    mapped: list[str] = []
    for tool in tools:
        if not tool.strip():
            continue
        opencode_tool = map_tool(tool)
        if opencode_tool not in mapped:
            mapped.append(opencode_tool)
    return mapped
