"""Exceptions shared by the adapter modules.

All errors raised deliberately by raptor-opencode derive from
:class:`RaptorOpencodeError`, so the CLI can distinguish expected failures
(bad input, unsupported construct) from genuine bugs.
"""

from __future__ import annotations


class RaptorOpencodeError(Exception):
    """Base class for all expected raptor-opencode failures."""


class FrontmatterError(RaptorOpencodeError, ValueError):
    """Raised when a Markdown file has a malformed frontmatter block."""


class UnknownToolError(RaptorOpencodeError, ValueError):
    """Raised when a Claude tool has no known OpenCode equivalent."""


class GenerationError(RaptorOpencodeError):
    """Raised when a source file cannot be converted into an OpenCode file."""
