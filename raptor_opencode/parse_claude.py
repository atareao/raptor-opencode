"""Tolerant reader/writer for YAML frontmatter in Claude Code Markdown files.

RAPTOR's ``.claude/agents`` and ``.claude/commands`` files only use a small
subset of YAML, but that subset includes nested hook mappings, inline and block
lists, quoted strings and multi-line scalars. Rather than depend on PyYAML, this
module implements the subset the adapter needs and fails loudly on anything it
cannot understand.

Public API:
    parse_frontmatter(text) -> (frontmatter, body)
    parse_file(path) -> (frontmatter, body)
    dump_frontmatter(mapping) -> str
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import FrontmatterError

DELIMITER = "---"
LIST_KEYS = frozenset({"tools", "skills", "allowed-tools", "disallowed-tools", "tags", "keywords"})
_BLOCK_SCALARS = frozenset({"|", "|-", "|+", ">", ">-", ">+"})
_NULLS = frozenset({"null", "~"})
_RESERVED = frozenset({"true", "false", "null", "~", "yes", "no", "on", "off"})
_ESCAPES = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    '"': '"',
    "\\": "\\",
    "/": "/",
    "0": "\0",
    "b": "\b",
    "f": "\f",
}
_ESCAPE_BY_CHAR = {v: k for k, v in _ESCAPES.items() if v != "/"}


def parse_file(path: str | Path) -> tuple[dict[str, Any], str]:
    """Parse *path* and return ``(frontmatter, body)``."""
    return parse_frontmatter(Path(path).read_text(encoding="utf-8"))


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split *text* into frontmatter data and body.

    Files without a frontmatter block yield ``({}, text)``. Line endings are
    normalised to ``\\n`` and leading blank lines of the body are dropped, so
    generated output is deterministic.

    Raises:
        FrontmatterError: the block is opened but never closed, or its content
            is not a YAML mapping this parser supports.
    """
    frontmatter_text, body, has_frontmatter = _split_frontmatter(text)
    if not has_frontmatter:
        return {}, text.replace("\r\n", "\n").replace("\r", "\n")
    if not frontmatter_text.strip():
        return {}, body.lstrip("\n")
    lines = frontmatter_text.split("\n")
    data, next_index = _parse_block(lines, 0, 0)
    leftover = _skip_ignorable(lines, next_index)
    if leftover < len(lines):
        raise FrontmatterError(
            f"unexpected content after frontmatter at line {leftover + 1}: "
            f"{lines[leftover].strip()!r}"
        )
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter must be a YAML mapping")
    return data, body.lstrip("\n")


# --------------------------------------------------------------------------
# Tokenising
# --------------------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str, bool]:
    """Return ``(frontmatter, body, found)`` with LF line endings."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if not lines or lines[0].strip() != DELIMITER:
        return "", text, False
    for index in range(1, len(lines)):
        if lines[index].strip() == DELIMITER:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :]), True
    raise FrontmatterError("unterminated frontmatter: missing closing '---' line")


def _indent_of(line: str) -> int:
    if line.startswith("\t"):
        raise FrontmatterError("tabs are not allowed for YAML indentation")
    return len(line) - len(line.lstrip(" "))


def _skip_ignorable(lines: list[str], index: int) -> int:
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith("#"):
            break
        index += 1
    return index


def _is_sequence_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def _split_key(content: str) -> tuple[str, str]:
    """Split ``key: value`` at the first unquoted colon followed by space/EOL."""
    quote: str | None = None
    index = 0
    while index < len(content):
        char = content[index]
        if quote is not None:
            if char == "\\" and quote == '"':
                index += 2
                continue
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == ":" and (index + 1 == len(content) or content[index + 1] in " \t"):
            return content[:index].strip(), content[index + 1 :].strip()
        index += 1
    return "", ""


# --------------------------------------------------------------------------
# Parsing blocks
# --------------------------------------------------------------------------


def _parse_block(lines: list[str], index: int, indent: int) -> tuple[Any, int]:
    start = _skip_ignorable(lines, index)
    if start >= len(lines):
        return None, start
    first_indent = _indent_of(lines[start])
    if first_indent < indent:
        return None, start
    if _is_sequence_item(lines[start].strip()):
        return _parse_sequence(lines, start, first_indent)
    return _parse_mapping(lines, start, first_indent)


def _parse_mapping(lines: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    last_key: str | None = None
    while index < len(lines):
        index = _skip_ignorable(lines, index)
        if index >= len(lines):
            break
        line = lines[index]
        line_indent = _indent_of(line)
        if line_indent < indent:
            break
        content = line.strip()
        if line_indent > indent:
            # YAML allows plain scalars to continue on indented lines.
            if last_key is not None and isinstance(result[last_key], str):
                result[last_key] = f"{result[last_key]} {content}"
                index += 1
                continue
            raise FrontmatterError(f"unexpected indentation at line {index + 1}: {content!r}")
        if _is_sequence_item(content):
            break
        key, rest = _split_key(content)
        if not key:
            raise FrontmatterError(f"expected 'key: value' at line {index + 1}, got {content!r}")
        key = _unquote_key(key)
        value, index = _parse_value(lines, index + 1, indent, rest, key)
        result[key] = value
        last_key = key
    return result, index


def _parse_sequence(lines: list[str], index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines):
        index = _skip_ignorable(lines, index)
        if index >= len(lines):
            break
        line = lines[index]
        line_indent = _indent_of(line)
        content = line.strip()
        if line_indent < indent:
            break
        if not _is_sequence_item(content):
            if line_indent == indent:
                break
            raise FrontmatterError(f"unexpected indentation at line {index + 1}: {content!r}")
        if line_indent > indent:
            raise FrontmatterError(f"unexpected indentation at line {index + 1}: {content!r}")
        item_text = content[1:].strip()
        if not item_text:
            value, index = _parse_value(lines, index + 1, indent, "", None)
            items.append(value)
            continue
        key, rest = _split_key(item_text)
        if key:
            item: dict[str, Any] = {}
            value, index = _parse_value(lines, index + 1, indent, rest, _unquote_key(key))
            item[_unquote_key(key)] = value
            following = _skip_ignorable(lines, index)
            if following < len(lines) and _indent_of(lines[following]) > indent:
                extra, index = _parse_mapping(lines, following, _indent_of(lines[following]))
                item.update(extra)
            items.append(item)
        else:
            value = _parse_scalar(item_text)
            items.append(value)
            index += 1
    return items, index


def _parse_value(
    lines: list[str], index: int, parent_indent: int, raw: str, key: str | None
) -> tuple[Any, int]:
    raw = raw.strip()
    if raw in _BLOCK_SCALARS:
        return _parse_block_scalar(lines, index, parent_indent, raw)
    if raw:
        return _parse_scalar(raw, key), index

    start = _skip_ignorable(lines, index)
    if start < len(lines) and _indent_of(lines[start]) > parent_indent:
        return _parse_block(lines, start, _indent_of(lines[start]))
    if (
        start < len(lines)
        and _indent_of(lines[start]) == parent_indent
        and _is_sequence_item(lines[start].strip())
    ):
        return _parse_sequence(lines, start, parent_indent)
    return None, start


def _parse_block_scalar(
    lines: list[str], index: int, parent_indent: int, marker: str
) -> tuple[str, int]:
    """Collect a ``|`` (literal) or ``>`` (folded) block scalar."""
    chunks: list[str] = []
    block_indent: int | None = None
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            chunks.append("")
            index += 1
            continue
        line_indent = _indent_of(line)
        if line_indent <= parent_indent:
            break
        if block_indent is None:
            block_indent = line_indent
        if line_indent < block_indent:
            break
        chunks.append(line[block_indent:])
        index += 1

    keep_trailing = marker.endswith("+")
    while chunks and not chunks[-1] and not keep_trailing:
        chunks.pop()

    if marker.startswith(">"):
        folded: list[str] = []
        for chunk in chunks:
            if not chunk:
                folded.append("\n")
            elif folded and folded[-1] not in ("", "\n"):
                folded[-1] = f"{folded[-1]} {chunk}"
            else:
                folded.append(chunk)
        return "".join(folded).strip("\n"), index

    return "\n".join(chunks), index


# --------------------------------------------------------------------------
# Scalars
# --------------------------------------------------------------------------


def _unquote_key(key: str) -> str:
    if len(key) >= 2 and key[0] == key[-1] and key[0] in {"'", '"'}:
        parsed = _parse_scalar(key)
        return str(parsed) if parsed is not None else ""
    return key


def _parse_scalar(raw: str, key: str | None = None) -> Any:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        return [_parse_scalar(item, None) for item in _split_flow(raw[1:-1])]
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        value: Any = _unescape_double(raw[1:-1])
    elif len(raw) >= 2 and raw[0] == raw[-1] == "'":
        value = raw[1:-1].replace("''", "'")
    elif raw in _NULLS:
        value = None
    elif raw.lower() in {"true", "false"}:
        value = raw.lower() == "true"
    elif re.fullmatch(r"-?\d+", raw):
        value = int(raw)
    else:
        value = raw
    if key in LIST_KEYS:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if value is None:
            return []
    return value


def _split_flow(inner: str) -> list[str]:
    """Split a flow sequence body on commas outside quotes/nesting."""
    if not inner.strip():
        return []
    items: list[str] = []
    current: list[str] = []
    quote: str | None = None
    depth = 0
    index = 0
    while index < len(inner):
        char = inner[index]
        if quote is not None:
            current.append(char)
            if char == "\\" and quote == '"' and index + 1 < len(inner):
                index += 1
                current.append(inner[index])
            elif char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
            current.append(char)
        elif char in "[{":
            depth += 1
            current.append(char)
        elif char in "]}":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        items.append(tail)
    return items


def _unescape_double(raw: str) -> str:
    chars: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw):
            escaped = raw[index + 1]
            if escaped in _ESCAPES:
                chars.append(_ESCAPES[escaped])
                index += 2
                continue
            chars.append(char + escaped)
            index += 2
            continue
        chars.append(char)
        index += 1
    return "".join(chars)


# --------------------------------------------------------------------------
# Dumping
# --------------------------------------------------------------------------


def dump_frontmatter(data: Mapping[str, Any]) -> str:
    """Serialise *data* as a frontmatter block, without a trailing newline."""
    lines = [DELIMITER]
    for key, value in data.items():
        _dump_pair(lines, str(key), value, indent=0)
    lines.append(DELIMITER)
    return "\n".join(lines)


def format_scalar(value: Any) -> str:
    """Render a scalar in the most readable YAML form that round-trips."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    if _is_plain_safe(text):
        return text
    return f'"{_escape_double(text)}"'


def _dump_pair(lines: list[str], key: str, value: Any, indent: int) -> None:
    pad = " " * indent
    if isinstance(value, Mapping):
        if not value:
            lines.append(f"{pad}{key}: {{}}")
            return
        lines.append(f"{pad}{key}:")
        for child_key, child_value in value.items():
            _dump_pair(lines, str(child_key), child_value, indent + 2)
        return
    if isinstance(value, list | tuple):
        if not value:
            lines.append(f"{pad}{key}: []")
            return
        lines.append(f"{pad}{key}:")
        for item in value:
            if isinstance(item, Mapping):
                _dump_mapping_item(lines, item, indent + 2)
            else:
                lines.append(f"{pad}  - {format_scalar(item)}")
        return
    lines.append(f"{pad}{key}: {format_scalar(value)}")


def _dump_mapping_item(lines: list[str], item: Mapping[str, Any], indent: int) -> None:
    pad = " " * indent
    entries = list(item.items())
    if not entries:
        lines.append(f"{pad}- {{}}")
        return
    first_key, first_value = entries[0]
    if _is_inline_safe(first_value):
        lines.append(f"{pad}- {first_key}: {format_scalar(first_value)}")
    else:
        start = len(lines)
        _dump_pair(lines, str(first_key), first_value, indent + 2)
        lines[start] = f"{pad}- {lines[start][indent + 2 :]}"
    for child_key, child_value in entries[1:]:
        _dump_pair(lines, str(child_key), child_value, indent + 2)


def _is_inline_safe(value: Any) -> bool:
    return not isinstance(value, Mapping | list | tuple)


def _is_plain_safe(text: str) -> bool:
    if not text or text != text.strip():
        return False
    if text.lower() in _RESERVED:
        return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return False
    if text[0] in "-?:,[]{}#&*!|>'\"%@`":
        return False
    if ": " in text or text.endswith(":") or " #" in text:
        return False
    return not any(char in text for char in "\n\r\t")


def _escape_double(text: str) -> str:
    out: list[str] = []
    for char in text:
        if char in _ESCAPE_BY_CHAR:
            out.append("\\" + _ESCAPE_BY_CHAR[char])
        else:
            out.append(char)
    return "".join(out)
