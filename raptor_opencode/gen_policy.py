"""Extract per-agent tool policy from RAPTOR's agent frontmatter.

RAPTOR scopes Bash and WebFetch restrictions with ``PreToolUse`` hooks declared
inside each ``.claude/agents/*.md`` file. The hooks themselves are ordinary
Python scripts; their command line encodes the policy:

* ``bash-command-allowlist.py <prefix> [<prefix> ...]``
* ``webfetch-domain-allowlist.py [<host> ...] [--https-any] [--anchor-file <path>]``

This module turns those command lines into a stable JSON document so the
OpenCode plugin can enforce the same policy without re-parsing YAML. Unknown
hooks and malformed commands never abort generation: they are reported in
``warnings`` and in the log.
"""

from __future__ import annotations

import json
import logging
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import GenerationError
from .gen_agents import slugify
from .parse_claude import parse_file

POLICY_VERSION = 1
logger = logging.getLogger(__name__)
BASH_HOOK = "bash-command-allowlist.py"
WEBFETCH_HOOK = "webfetch-domain-allowlist.py"
CLAUDE_PROJECT_DIR = "$CLAUDE_PROJECT_DIR"
_PROJECT_DIR_PREFIXES = ("${CLAUDE_PROJECT_DIR}", "$CLAUDE_PROJECT_DIR")
POLICY_COMMENT = (
    "Empty 'bash' or 'webfetch' rules mean the agent had no such PreToolUse hook "
    "in Claude Code, so the guard leaves that tool unrestricted (parity). "
    "'default.unrestricted' only applies to sessions whose agent was never "
    "reported by chat.params; false fails closed."
)


def build_policy(raptor_home: str | Path) -> dict[str, Any]:
    """Build the policy document for every agent under *raptor_home*."""
    root = Path(raptor_home)
    agents_dir = root / ".claude" / "agents"
    warnings: list[str] = []
    hooks: dict[str, str] = {}
    agents: dict[str, dict[str, Any]] = {}

    for path in sorted(agents_dir.glob("*.md")):
        frontmatter, _body = parse_file(path)
        agent_name = _agent_name(frontmatter, path, warnings)
        if agent_name in agents:
            raise GenerationError(
                f"duplicate agent name {agent_name!r} in {path} (policy document keys "
                "must be unique)"
            )
        agents[agent_name] = _agent_policy(frontmatter, agent_name, hooks, warnings)

    for hook_name in (BASH_HOOK, WEBFETCH_HOOK):
        if hook_name in hooks:
            continue
        candidate = root / ".claude" / "hooks" / hook_name
        if candidate.is_file():
            hooks[_hook_key(hook_name)] = f".claude/hooks/{hook_name}"

    resolved_home = str(Path(raptor_home).resolve())
    for warning in warnings:
        logger.warning("%s", warning)

    return {
        "version": POLICY_VERSION,
        "comment": POLICY_COMMENT,
        "raptor_home": resolved_home,
        "default": {"unrestricted": False},
        "hooks": {key: hooks[key] for key in sorted(hooks)},
        "agents": {name: agents[name] for name in sorted(agents)},
        "warnings": warnings,
    }


def write_policy(raptor_home: str | Path, destination: str | Path) -> dict[str, Any]:
    """Build the policy and write it as JSON to *destination*."""
    policy = build_policy(raptor_home)
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_policy(policy), encoding="utf-8")
    return policy


def _render_policy(policy: Mapping[str, Any]) -> str:
    return json.dumps(policy, indent=2, ensure_ascii=False) + "\n"


def _agent_name(frontmatter: Mapping[str, Any], path: Path, warnings: list[str]) -> str:
    raw = frontmatter.get("name")
    if isinstance(raw, str) and raw.strip():
        return slugify(raw.strip())
    warnings.append(f"{path.name}: missing 'name'; using the file stem")
    return slugify(path.stem)


def _agent_policy(
    frontmatter: Mapping[str, Any],
    agent_name: str,
    hooks: dict[str, str],
    warnings: list[str],
) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "bash": [],
        "webfetch": {"domains": [], "https_any": False},
    }
    raw_hooks = frontmatter.get("hooks")
    if raw_hooks is None:
        return policy
    if not isinstance(raw_hooks, Mapping):
        warnings.append(f"{agent_name}: 'hooks' must be a mapping, ignoring it")
        return policy

    for event, entries in raw_hooks.items():
        if event != "PreToolUse":
            warnings.append(f"{agent_name}: unsupported hook event {event!r}")
            continue
        for entry in _as_sequence(entries):
            if not isinstance(entry, Mapping):
                warnings.append(f"{agent_name}: PreToolUse entry is not a mapping")
                continue
            _apply_entry(agent_name, entry, policy, hooks, warnings)
    return policy


def _apply_entry(
    agent_name: str,
    entry: Mapping[str, Any],
    policy: dict[str, Any],
    hooks: dict[str, str],
    warnings: list[str],
) -> None:
    matcher = entry.get("matcher")
    if not isinstance(matcher, str) or not matcher.strip():
        warnings.append(f"{agent_name}: PreToolUse entry has no matcher")
        return
    if matcher.lower() not in {"bash", "webfetch"}:
        warnings.append(f"{agent_name}: unsupported matcher {matcher!r} (only Bash and WebFetch)")
        return
    for hook in _as_sequence(entry.get("hooks")):
        if not isinstance(hook, Mapping):
            warnings.append(f"{agent_name}: hook for matcher {matcher!r} is not a mapping")
            continue
        if hook.get("type") != "command":
            warnings.append(
                f"{agent_name}: unsupported hook type {hook.get('type')!r} for matcher {matcher!r}"
            )
            continue
        command = hook.get("command")
        if not isinstance(command, str) or not command.strip():
            warnings.append(f"{agent_name}: command hook for matcher {matcher!r} is empty")
            continue
        tokens = _split_command(command, agent_name, matcher, warnings)
        if not tokens:
            continue
        script = _hook_kind(tokens[0])
        if script is None:
            warnings.append(
                f"{agent_name}: unknown hook script {tokens[0]!r} for matcher {matcher!r}"
            )
            continue
        hooks.setdefault(script, _project_relative(tokens[0]))
        if matcher.lower() == "bash":
            if script != "bash":
                warnings.append(f"{agent_name}: matcher 'Bash' wired to {script!r} hook; ignoring")
                continue
            _apply_bash(policy, [_project_relative(arg) for arg in tokens[1:]])
        elif matcher.lower() == "webfetch":
            if script != "webfetch":
                warnings.append(
                    f"{agent_name}: matcher 'WebFetch' wired to {script!r} hook; ignoring"
                )
                continue
            _apply_webfetch(policy, tokens[1:], agent_name, warnings)


def _apply_bash(policy: dict[str, Any], args: Sequence[str]) -> None:
    for prefix in args:
        if prefix not in policy["bash"]:
            policy["bash"].append(prefix)


def _apply_webfetch(
    policy: dict[str, Any],
    args: Sequence[str],
    agent_name: str,
    warnings: list[str],
) -> None:
    target = policy["webfetch"]
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--https-any":
            target["https_any"] = True
            index += 1
            continue
        if arg == "--anchor-file":
            if index + 1 >= len(args):
                warnings.append(f"{agent_name}: --anchor-file without a path")
                index += 1
                continue
            target["anchor_file"] = _project_relative(args[index + 1])
            index += 2
            continue
        if arg.startswith("--anchor-file="):
            target["anchor_file"] = _project_relative(arg.split("=", 1)[1])
            index += 1
            continue
        if arg.startswith("-"):
            warnings.append(f"{agent_name}: unknown webfetch hook flag {arg!r}")
            index += 1
            continue
        host = _hostname(arg)
        if host and host not in target["domains"]:
            target["domains"].append(host)
        index += 1


def _split_command(command: str, agent_name: str, matcher: str, warnings: list[str]) -> list[str]:
    try:
        return shlex.split(command, posix=True)
    except ValueError as exc:
        warnings.append(f"{agent_name}: cannot parse {matcher} hook command ({exc}); ignoring it")
        return []


def _hook_kind(token: str) -> str | None:
    name = Path(token.replace("\\", "/")).name
    if name == BASH_HOOK:
        return "bash"
    if name == WEBFETCH_HOOK:
        return "webfetch"
    return None


def _hook_key(hook_name: str) -> str:
    return "bash" if hook_name == BASH_HOOK else "webfetch"


def _project_relative(token: str) -> str:
    """Rewrite ``$CLAUDE_PROJECT_DIR/...`` as a path relative to RAPTOR_HOME."""
    value = token
    for prefix in _PROJECT_DIR_PREFIXES:
        value = value.replace(f"{prefix}/", "")
        value = value.replace(prefix, "")
    while value.startswith("./"):
        value = value[2:]
    return value


def _hostname(token: str) -> str:
    text = token.strip()
    if not text:
        return ""
    if "://" in text:
        try:
            return (urlsplit(text).hostname or "").lower().rstrip(".")
        except ValueError:
            return ""
    host = text.split("/", 1)[0]
    if host.count(":") == 1:  # host:port, but not bare IPv6
        host = host.split(":", 1)[0]
    return host.lower().rstrip(".")


def _as_sequence(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, str):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []
