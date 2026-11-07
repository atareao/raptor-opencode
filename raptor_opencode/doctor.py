"""Read-only health checks for an installed raptor-opencode tree.

``doctor`` answers one question: is the current OpenCode + RAPTOR setup what
``install`` promised? It never writes. Every check returns ``ok``, ``warn`` or
``fail`` with a message that says what to do next, so the CLI can map results to
exit codes (0 clean, 1 any failure, 2 broken environment such as a missing
``python3`` or ``git``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .gen_agents_md import find_unrewritten_paths
from .gen_plugin import CORE_NAME, PLUGIN_NAME
from .installer import DEFAULT_REF, MANIFEST_NAME, JsoncError, loads_jsonc
from .paths import Paths, resolve_paths
from .paths import generated_dir as default_generated_dir

Status = Literal["ok", "warn", "fail"]
Runner = Callable[..., subprocess.CompletedProcess[str]]
Which = Callable[[str], str | None]

REPORT_VERSION = 1
EXPECTED_AGENTS = 18
EXPECTED_COMMANDS = 37
EXPECTED_SKILLS = 11
EXPECTED_PLUGIN_FILES = (PLUGIN_NAME, CORE_NAME)
HOOK_FILES = ("bash-command-allowlist.py", "webfetch-domain-allowlist.py")
MIN_OPENCODE = (2, 2)
CONFIG_NAMES = ("opencode.jsonc", "opencode.json")

_VERSION = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_GLOBAL_RESOURCES = frozenset({"*", "**"})


@dataclass(frozen=True, slots=True)
class ExpectedCounts:
    """Artifact counts shipped by the pinned RAPTOR release."""

    agents: int = EXPECTED_AGENTS
    commands: int = EXPECTED_COMMANDS
    skills: int = EXPECTED_SKILLS
    plugin_files: int = len(EXPECTED_PLUGIN_FILES)


DEFAULT_EXPECTED = ExpectedCounts()


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One check: a stable name, a status and an actionable message."""

    name: str
    status: Status
    message: str
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-friendly form of this result."""
        payload: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "message": self.message,
        }
        if self.fatal:
            payload["fatal"] = True
        return payload


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Every check plus the resolved paths they looked at."""

    checks: tuple[CheckResult, ...]
    ref: str
    paths: dict[str, str]

    @property
    def exit_code(self) -> int:
        """0 when clean, 1 on failures, 2 when the environment is unusable."""
        if any(check.fatal for check in self.checks):
            return 2
        if any(check.status == "fail" for check in self.checks):
            return 1
        return 0

    @property
    def status(self) -> Status:
        """Worst status across checks (``warn`` outranks ``ok``)."""
        if self.exit_code == 0 and any(check.status == "warn" for check in self.checks):
            return "warn"
        if self.exit_code == 0:
            return "ok"
        return "fail"

    def counts(self) -> dict[str, int]:
        """Return how many checks have each status."""
        counts = {"ok": 0, "warn": 0, "fail": 0}
        for check in self.checks:
            counts[check.status] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        """Return a machine-readable report suitable for ``--json``."""
        counts = self.counts()
        return {
            "version": REPORT_VERSION,
            "status": self.status,
            "exit_code": self.exit_code,
            "ref": self.ref,
            "paths": self.paths,
            "counts": counts,
            "checks": [check.to_dict() for check in self.checks],
        }

    def render(self) -> str:
        """Return the human-readable report, one line per check."""
        lines = [f"raptor-opencode doctor (ref {self.ref})"]
        lines.extend(
            f"[{check.status:<4}] {check.name:<22} {check.message}" for check in self.checks
        )
        counts = self.counts()
        lines.append(
            f"{counts['ok']} ok, {counts['warn']} warn, {counts['fail']} fail "
            f"(exit {self.exit_code})"
        )
        return "\n".join(lines)


def run_checks(
    *,
    raptor_home: str | Path | None = None,
    ref: str = DEFAULT_REF,
    opencode_dir: str | Path | None = None,
    generated_dir: str | Path | None = None,
    skills: str | Path | None = None,
    expected: ExpectedCounts = DEFAULT_EXPECTED,
    run: Runner = subprocess.run,
    which: Which = shutil.which,
    env: Mapping[str, str] | None = None,
) -> DoctorReport:
    """Run every check and return the report.

    ``run`` and ``which`` are injectable so tests can drive the git and version
    probes without spawning processes. Nothing is written to disk.
    """
    environ = os.environ if env is None else env
    paths = resolve_paths(raptor_root=raptor_home, out_dir=opencode_dir, env=environ)
    generated = (
        Path(generated_dir) if generated_dir is not None else default_generated_dir(env=environ)
    )
    skills_root = Path(skills) if skills is not None else paths.skills

    checks: list[CheckResult] = []
    checks.extend(_check_prerequisites(which))
    checks.extend(_check_raptor_home(paths.raptor_home, ref, run))
    if paths.raptor_home.is_dir():
        checks.extend(_check_hooks(paths.raptor_home))

    artifacts = _Artifacts.from_paths(paths, generated)
    checks.extend(_check_artifacts(artifacts, expected))
    checks.extend(_check_manifest(artifacts, skills_root))
    checks.extend(_check_agents_md(artifacts))
    checks.extend(_check_config(paths.opencode_config))
    checks.extend(_check_opencode_version(run, which))

    report_paths = {
        "raptor_home": str(paths.raptor_home),
        "opencode_dir": str(paths.opencode_config),
        "generated_dir": str(generated),
        "skills_dir": str(skills_root),
    }
    return DoctorReport(checks=tuple(checks), ref=ref, paths=report_paths)


def doctor(
    *,
    raptor_home: str | Path | None = None,
    ref: str = DEFAULT_REF,
    opencode_dir: str | Path | None = None,
    generated_dir: str | Path | None = None,
    skills: str | Path | None = None,
    expected: ExpectedCounts = DEFAULT_EXPECTED,
    run: Runner = subprocess.run,
    which: Which = shutil.which,
    env: Mapping[str, str] | None = None,
) -> DoctorReport:
    """Alias of :func:`run_checks` kept for the CLI's vocabulary."""
    return run_checks(
        raptor_home=raptor_home,
        ref=ref,
        opencode_dir=opencode_dir,
        generated_dir=generated_dir,
        skills=skills,
        expected=expected,
        run=run,
        which=which,
        env=env,
    )


# --- individual checks ------------------------------------------------------


def _check_prerequisites(which: Which) -> list[CheckResult]:
    results: list[CheckResult] = []
    python = which("python3")
    if python:
        results.append(CheckResult("prerequisites", "ok", f"python3 found at {python}"))
    else:
        results.append(
            CheckResult(
                "prerequisites",
                "fail",
                "python3 is not on PATH; the RAPTOR hooks and the guard plugin need it. "
                "Install Python 3.11+ and retry.",
                fatal=True,
            )
        )
    git = which("git")
    if git:
        results.append(CheckResult("git", "ok", f"git found at {git}"))
    else:
        results.append(
            CheckResult(
                "git",
                "fail",
                "git is not on PATH; install it so RAPTOR can be cloned and its ref verified.",
                fatal=True,
            )
        )
    return results


def _check_raptor_home(home: Path, ref: str, run: Runner) -> list[CheckResult]:
    if not home.is_absolute():
        return [
            CheckResult(
                "raptor-home",
                "fail",
                f"RAPTOR_HOME resolves to {home}, which is relative; set RAPTOR_HOME "
                "or --raptor-home to an absolute path such as "
                "/home/<user>/.local/share/raptor",
            )
        ]
    if not home.exists():
        return [
            CheckResult(
                "raptor-home",
                "fail",
                f"RAPTOR_HOME {home} does not exist; run `raptor-opencode install` to clone it.",
            )
        ]
    if not home.is_dir():
        return [CheckResult("raptor-home", "fail", f"RAPTOR_HOME {home} is not a directory")]

    results = [CheckResult("raptor-home", "ok", f"checkout found at {home}")]
    if not (home / ".git").exists():
        results.append(
            CheckResult(
                "raptor-git",
                "fail",
                f"{home} is not a git checkout; move it away and rerun "
                "`raptor-opencode install` (it clones upstream for you).",
            )
        )
        return results

    results.append(_check_ref(home, ref, run))
    results.append(_check_clean(home, run))
    return results


def _check_ref(home: Path, ref: str, run: Runner) -> CheckResult:
    code, wanted = _git(run, home, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if code != 0 or not wanted.strip():
        return CheckResult(
            "raptor-ref",
            "fail",
            f"ref {ref} not found in {home}; run `git -C {home} fetch --tags` or "
            f"`raptor-opencode install --ref {ref}`.",
        )
    code, head = _git(run, home, "rev-parse", "HEAD")
    if code != 0 or not head.strip():
        return CheckResult("raptor-ref", "fail", f"cannot read HEAD of {home}; check the checkout")
    if head.strip() != wanted.strip():
        _tag_code, described = _git(run, home, "describe", "--tags", "--always")
        current = described.strip() or head.strip()[:12]
        return CheckResult(
            "raptor-ref",
            "fail",
            f"checkout is at {current}, expected {ref}; run "
            f"`raptor-opencode install --ref {ref}` or check out that tag by hand.",
        )
    return CheckResult("raptor-ref", "ok", f"checkout is at {ref}")


def _check_clean(home: Path, run: Runner) -> CheckResult:
    code, status = _git(run, home, "status", "--porcelain")
    if code != 0:
        return CheckResult(
            "raptor-clean",
            "warn",
            f"cannot read the working tree status of {home}; verify with `git status`.",
        )
    changed = [line for line in status.splitlines() if line.strip()]
    if changed:
        return CheckResult(
            "raptor-clean",
            "warn",
            f"{len(changed)} local change(s) in {home}; commit or stash them, otherwise "
            "`raptor-opencode install` refuses to update the checkout.",
        )
    return CheckResult("raptor-clean", "ok", "working tree is clean")


def _check_artifacts(artifacts: _Artifacts, expected: ExpectedCounts) -> list[CheckResult]:
    return [
        _count_check("agents", artifacts.agents, "*.md", expected.agents, "agent"),
        _count_check("commands", artifacts.commands, "*.md", expected.commands, "command"),
        _count_check("skills", artifacts.skills, None, expected.skills, "skill"),
        _plugin_check(artifacts, expected),
        _policy_check(artifacts),
    ]


def _count_check(
    name: str,
    directory: Path | None,
    pattern: str | None,
    expected: int,
    noun: str,
) -> CheckResult:
    if directory is None or not directory.is_dir():
        if expected == 0:
            return CheckResult(name, "ok", f"no {noun}s expected")
        return CheckResult(
            name,
            "fail",
            f"no {noun} directory at {directory}; run `raptor-opencode install` "
            f"(expected {expected} {noun}s).",
        )
    try:
        entries = (
            [path for path in directory.glob(pattern) if path.is_file()]
            if pattern is not None
            else [path for path in directory.iterdir() if path.is_dir() or path.is_symlink()]
        )
    except OSError as exc:
        return CheckResult(name, "fail", f"cannot read {directory}: {exc}")
    found = len(entries)
    if found == expected:
        return CheckResult(name, "ok", f"{found} {noun}s at {directory}")
    return CheckResult(
        name,
        "fail",
        f"found {found} {noun}s at {directory}, expected {expected} for the pinned RAPTOR "
        f"release; rerun `raptor-opencode install` to regenerate.",
    )


def _plugin_check(artifacts: _Artifacts, expected: ExpectedCounts) -> CheckResult:
    directory = artifacts.plugins
    if directory is None or not directory.is_dir():
        return CheckResult(
            "plugins",
            "fail",
            f"no plugin directory at {directory}; run `raptor-opencode install` "
            f"(expected {', '.join(EXPECTED_PLUGIN_FILES)}).",
        )
    missing = [name for name in EXPECTED_PLUGIN_FILES if not (directory / name).is_file()]
    if missing:
        return CheckResult(
            "plugins", "fail", f"missing plugin file(s) {', '.join(missing)} in {directory}"
        )
    found = len(EXPECTED_PLUGIN_FILES)
    if expected.plugin_files != found:
        return CheckResult(
            "plugins",
            "fail",
            f"expected {expected.plugin_files} plugin files, the adapter ships {found}",
        )
    return CheckResult("plugins", "ok", f"guard plugin present at {directory}")


def _policy_check(artifacts: _Artifacts) -> CheckResult:
    path = _first_file(
        artifacts.generated / "raptor-policy.json",
        artifacts.opencode / "raptor-policy.json",
    )
    if path is None:
        return CheckResult(
            "policy",
            "fail",
            "raptor-policy.json not found in the generated or OpenCode directories; "
            "rerun `raptor-opencode install` (the guard fails closed without it).",
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult(
            "policy",
            "fail",
            f"cannot parse {path}: {exc}; regenerate the artifacts.",
        )
    if not isinstance(document, dict) or not isinstance(document.get("agents"), dict):
        return CheckResult(
            "policy",
            "fail",
            f"{path} has no 'agents' map; regenerate the artifacts (the guard fails closed).",
        )
    if (
        not isinstance(document.get("raptor_home"), str)
        or not Path(str(document.get("raptor_home"))).is_absolute()
    ):
        return CheckResult(
            "policy",
            "fail",
            f"{path} does not bake an absolute 'raptor_home'; regenerate with "
            "`raptor-opencode install` so the guard can find the hooks.",
        )
    agents = document["agents"]
    if not agents:
        return CheckResult(
            "policy",
            "warn",
            f"{path} has an empty 'agents' map; regenerate the artifacts so the guard "
            "enforces RAPTOR's per-agent policy.",
        )
    return CheckResult("policy", "ok", f"{path} parses ({len(agents)} agent entries)")


def _check_manifest(artifacts: _Artifacts, skills_root: Path) -> list[CheckResult]:
    manifest_path = artifacts.generated.parent / MANIFEST_NAME
    if not manifest_path.is_file():
        return [
            CheckResult(
                "manifest",
                "warn",
                f"no install manifest at {manifest_path}; `generate` alone does not create "
                "symlinks. Run `raptor-opencode install` to link the artifacts.",
            )
        ]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [
            CheckResult("manifest", "fail", f"cannot parse {manifest_path}: {exc}; rerun install")
        ]
    if not isinstance(manifest, dict):
        return [
            CheckResult("manifest", "fail", f"{manifest_path} is not a JSON object; rerun install")
        ]

    links = [item for item in manifest.get("links", []) if isinstance(item, dict)]
    mode = str(manifest.get("mode", "symlink"))
    generated = artifacts.generated.resolve(strict=False)
    problems: list[str] = []
    recorded: set[Path] = set()
    for item in links:
        destination = Path(str(item.get("destination", "")))
        source = Path(str(item.get("source", "")))
        recorded.add(destination)
        if not destination.is_symlink() and not destination.exists():
            problems.append(f"{destination} is missing (rerun install)")
            continue
        if destination.is_symlink():
            direct = _direct_target(destination)
            target = destination.resolve(strict=False)
            if direct is None:
                problems.append(f"{destination} is not a readable link (rerun install)")
            elif not _same_path(direct, source):
                problems.append(f"{destination} -> {direct}, expected {source}")
            elif not _is_within(direct, generated):
                problems.append(f"{destination} points outside {generated}")
            elif not target.exists():
                problems.append(f"{destination} -> {target} is dangling (rerun install)")
        elif mode != "copy":
            problems.append(f"{destination} is a regular file, expected a symlink (rerun install)")
        elif not source.exists():
            problems.append(f"{destination} has no source at {source}")

    results: list[CheckResult] = []
    if problems:
        shown = "; ".join(problems[:3])
        more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
        results.append(
            CheckResult(
                "manifest",
                "fail",
                f"{len(problems)} broken link(s) in {manifest_path}: {shown}{more}",
            )
        )
    else:
        results.append(
            CheckResult(
                "manifest",
                "ok",
                f"{len(links)} link(s) in {manifest_path} point at the generated tree",
            )
        )

    orphans = _find_orphans(artifacts, skills_root, recorded, generated)
    if orphans:
        shown = ", ".join(str(path) for path in orphans[:3])
        more = f" (+{len(orphans) - 3} more)" if len(orphans) > 3 else ""
        results.append(
            CheckResult(
                "manifest-orphans",
                "warn",
                f"{len(orphans)} link(s) point into {generated} but are not in the manifest: "
                f"{shown}{more}. Rerun `raptor-opencode install` to reconcile.",
            )
        )
    else:
        results.append(
            CheckResult("manifest-orphans", "ok", "no unrecorded links into the generated tree")
        )
    return results


def _find_orphans(
    artifacts: _Artifacts,
    skills_root: Path,
    recorded: set[Path],
    generated: Path,
) -> list[Path]:
    directories = [
        artifacts.opencode / "agents",
        artifacts.opencode / "commands",
        artifacts.plugins,
        skills_root,
    ]
    orphans: list[Path] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_symlink() or entry in recorded:
                continue
            target = entry.resolve(strict=False)
            if _is_within(target, generated):
                orphans.append(entry)
    return sorted(orphans)


def _check_agents_md(artifacts: _Artifacts) -> list[CheckResult]:
    path = _first_file(artifacts.generated / "AGENTS.md", artifacts.opencode / "AGENTS.md")
    if path is None:
        return [
            CheckResult(
                "agents-md",
                "fail",
                "AGENTS.md not found in the generated or OpenCode directories; rerun "
                "`raptor-opencode install`.",
            )
        ]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [CheckResult("agents-md", "fail", f"cannot read {path}: {exc}")]
    problems = find_unrewritten_paths(text)
    if problems:
        shown = "; ".join(problems[:3])
        more = f" (+{len(problems) - 3} more)" if len(problems) > 3 else ""
        return [
            CheckResult(
                "agents-md",
                "fail",
                f"{path} keeps Claude-relative paths that break under OpenCode: {shown}{more}. "
                "Regenerate with `raptor-opencode install`; if it persists, report a rewriter bug.",
            )
        ]
    return [
        CheckResult("agents-md", "ok", f"{path} has no relative .claude/ or bare libexec/ paths")
    ]


def _check_config(opencode: Path) -> list[CheckResult]:
    path = next((opencode / name for name in CONFIG_NAMES if (opencode / name).is_file()), None)
    if path is None:
        return [
            CheckResult(
                "config",
                "fail",
                f"no opencode.jsonc under {opencode}; merge the generated fragment "
                "(`raptor-opencode install --merge-config`) or copy opencode.fragment.jsonc "
                "into it, then restart OpenCode.",
            )
        ]
    try:
        document = loads_jsonc(path.read_text(encoding="utf-8"))
    except (OSError, JsoncError) as exc:
        return [
            CheckResult(
                "config",
                "fail",
                f"cannot parse {path}: {exc}. Fix the JSONC syntax (trailing commas and "
                "comments are supported) or restore a backup.",
            )
        ]
    if not isinstance(document, dict):
        return [CheckResult("config", "fail", f"{path} must contain a JSON object")]

    results = [_check_instructions(document, path)]
    results.append(_check_env(document, path))
    results.append(_check_plugin(document, path))
    results.append(_check_skill_permissions(document, path))
    return results


def _check_instructions(document: Mapping[str, Any], path: Path) -> CheckResult:
    raw = document.get("instructions")
    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    entries = [str(value) for value in values]
    if any(entry.endswith("AGENTS.md") for entry in entries):
        return CheckResult("config-instructions", "ok", f"{path} loads AGENTS.md")
    return CheckResult(
        "config-instructions",
        "fail",
        f"{path} does not list {{{{env:RAPTOR_HOME}}}}/AGENTS.md in 'instructions'; merge "
        "the generated fragment or add it by hand.",
    )


def _check_env(document: Mapping[str, Any], path: Path) -> CheckResult:
    env = document.get("env")
    value = env.get("RAPTOR_HOME") if isinstance(env, Mapping) else None
    if isinstance(value, str) and value.strip():
        return CheckResult("config-env", "ok", f"{path} sets env.RAPTOR_HOME={value}")
    return CheckResult(
        "config-env",
        "warn",
        f"{path} does not set env.RAPTOR_HOME; libexec scripts and AGENTS.md rely on it. "
        "Merge the generated fragment (export it from your shell as a fallback).",
    )


def _check_plugin(document: Mapping[str, Any], path: Path) -> CheckResult:
    raw = document.get("plugin")
    values = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    names = {Path(str(value)).name for value in values}
    if PLUGIN_NAME in names:
        return CheckResult("config-plugin", "ok", f"{path} loads {PLUGIN_NAME}")
    return CheckResult(
        "config-plugin",
        "fail",
        f"{path} does not load {PLUGIN_NAME}; without it bash/webfetch policy is not "
        "enforced. Add the generated 'plugin' entry and restart OpenCode.",
    )


def _check_skill_permissions(document: Mapping[str, Any], path: Path) -> CheckResult:
    raw = document.get("permissions", document.get("permission"))
    rules = [entry for entry in raw if isinstance(entry, Mapping)] if isinstance(raw, list) else []
    deny_index = None
    for index, rule in enumerate(rules):
        if (
            rule.get("action") == "skill"
            and rule.get("effect") == "deny"
            and str(rule.get("resource", "")) in _GLOBAL_RESOURCES
        ):
            deny_index = index
            break
    if deny_index is None:
        return CheckResult(
            "config-skills",
            "ok",
            f"{path} does not deny skills globally",
        )
    allowed_after = any(
        rule.get("action") == "skill" and rule.get("effect") == "allow"
        for rule in rules[deny_index + 1 :]
    )
    if allowed_after:
        return CheckResult(
            "config-skills",
            "ok",
            f"{path} re-allows RAPTOR skills after the global deny",
        )
    return CheckResult(
        "config-skills",
        "fail",
        f"{path} denies every skill ({{'action': 'skill', 'resource': '*', 'effect': "
        "'deny'}}) and no allow rule comes after it, so RAPTOR's skills stay hidden. "
        "OpenCode evaluates the 'permissions' list in order and the last matching rule "
        "wins: append the generated 'raptor-*' allow entries below the deny, or drop the "
        "deny.",
    )


def _check_opencode_version(run: Runner, which: Which) -> list[CheckResult]:
    binary = which("opencode")
    if not binary:
        return [
            CheckResult(
                "opencode-version",
                "warn",
                "opencode not found on PATH, so its version cannot be checked; install "
                f"OpenCode {MIN_OPENCODE[0]}.{MIN_OPENCODE[1]}+ to load this configuration.",
            )
        ]
    try:
        result = run([binary, "--version"], check=False, capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return [
            CheckResult(
                "opencode-version",
                "warn",
                f"cannot run `opencode --version` ({exc}); verify the installation by hand.",
            )
        ]
    output = f"{result.stdout or ''} {result.stderr or ''}".strip()
    match = _VERSION.search(output)
    if match is None:
        return [
            CheckResult(
                "opencode-version",
                "warn",
                f"cannot parse the version from `opencode --version` output ({output!r}); "
                f"OpenCode {MIN_OPENCODE[0]}.{MIN_OPENCODE[1]}+ is required.",
            )
        ]
    version = (int(match.group(1)), int(match.group(2)))
    shown = ".".join(part for part in match.groups() if part is not None)
    if version < MIN_OPENCODE:
        return [
            CheckResult(
                "opencode-version",
                "fail",
                f"OpenCode {shown} is too old; the guard plugin needs "
                f"{MIN_OPENCODE[0]}.{MIN_OPENCODE[1]}+. Upgrade OpenCode.",
            )
        ]
    return [CheckResult("opencode-version", "ok", f"OpenCode {shown}")]


# --- helpers ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Artifacts:
    """Resolved artifact directories, preferring the installed layout."""

    opencode: Path
    generated: Path
    agents: Path | None
    commands: Path | None
    skills: Path | None
    plugins: Path | None

    @classmethod
    def from_paths(cls, paths: Paths, generated: Path) -> _Artifacts:
        """Pick the first existing directory for every artifact group."""
        opencode = paths.opencode_config
        return cls(
            opencode=opencode,
            generated=generated,
            agents=_first_dir(opencode / "agents", generated / "agents"),
            commands=_first_dir(opencode / "commands", generated / "commands"),
            skills=_first_dir(generated / "skills", opencode / "skills"),
            plugins=_first_dir(opencode / "plugins", generated / "plugins"),
        )


def _first_dir(*candidates: Path) -> Path | None:
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _first_file(*candidates: Path) -> Path | None:
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _direct_target(link: Path) -> Path | None:
    """Return the link's immediate target, normalised but not followed."""
    try:
        raw = os.readlink(link)
    except OSError:
        return None
    target = Path(raw)
    if not target.is_absolute():
        target = link.parent / target
    return Path(os.path.normpath(target))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normpath(str(left)) == os.path.normpath(str(right))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _git(run: Runner, home: Path, *args: str) -> tuple[int | None, str]:
    try:
        result = run(
            ["git", "-C", str(home), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None, ""
    return result.returncode, result.stdout or ""


def _path_is_executable(path: Path) -> bool:
    return os.access(path, os.X_OK)


def _check_hooks(home: Path) -> list[CheckResult]:
    """Hook scripts are checked separately so the message names the file."""
    results: list[CheckResult] = []
    for name in HOOK_FILES:
        path = home / ".claude" / "hooks" / name
        if not path.is_file():
            results.append(
                CheckResult(
                    "hooks",
                    "fail",
                    f"missing hook {path}; the checkout is incomplete or at an unsupported "
                    f"ref. Reinstall RAPTOR {DEFAULT_REF}.",
                )
            )
        elif not os.access(path, os.R_OK):
            results.append(
                CheckResult("hooks", "fail", f"hook {path} is not readable; fix its permissions.")
            )
        elif not _path_is_executable(path):
            results.append(
                CheckResult(
                    "hooks",
                    "warn",
                    f"hook {path} is not executable; the guard calls it through python3, so "
                    f"it still works, but run `chmod +x {path}` to match upstream.",
                )
            )
        else:
            results.append(CheckResult("hooks", "ok", f"{name} is executable"))
    return results


__all__ = [
    "DEFAULT_EXPECTED",
    "EXPECTED_AGENTS",
    "EXPECTED_COMMANDS",
    "EXPECTED_SKILLS",
    "MIN_OPENCODE",
    "CheckResult",
    "DoctorReport",
    "ExpectedCounts",
    "doctor",
    "run_checks",
]
