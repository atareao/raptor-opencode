"""Tests for the `install.sh` convenience wrapper.

The wrapper is exercised as a real subprocess. Every branch installs a fake
`pipx`/`python3` on a restricted PATH, so nothing touches the network, the
developer machine or the repository's own `.venv`.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "install.sh"

RECORDER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "${ARGS_LOG:?}"
exit "${FAKE_EXIT_CODE:-0}"
"""


def _executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _checkout(tmp_path: Path) -> Path:
    """Copy the wrapper into a throwaway checkout root."""
    root = tmp_path / "repo"
    root.mkdir()
    shutil.copy2(SCRIPT, root / "install.sh")
    (root / "pyproject.toml").write_text("[project]\nname = 'fake'\n", encoding="utf-8")
    return root


def _minimal_path(tmp_path: Path) -> Path:
    """A PATH with only the utilities the wrapper needs (no real pipx)."""
    shims = tmp_path / "shims"
    shims.mkdir()
    for tool in ("bash", "cat", "dirname", "cp", "mkdir"):
        (shims / tool).symlink_to(shutil.which(tool) or f"/usr/bin/{tool}")
    _executable(
        shims / "python3",
        """#!/usr/bin/env bash
if [[ ${1:-} == "-c" ]]; then
  if [[ ${2:-} == *print* ]]; then echo "3.12.0"; fi
  exit 0
fi
exit 0
""",
    )
    return shims


def _run(
    root: Path, argv: list[str], env: dict[str, str], timeout: int = 60
) -> subprocess.CompletedProcess[str]:
    merged = {
        "PATH": str(root.parent / "shims"),
        "HOME": str(root.parent / "home"),
        "ARGS_LOG": str(root.parent / "args.log"),
        **env,
    }
    return subprocess.run(
        [str(root / "install.sh"), *argv],
        capture_output=True,
        text=True,
        env=merged,
        cwd=root,
        timeout=timeout,
        check=False,
    )


@pytest.fixture
def fake_raptor(tmp_path: Path) -> Path:
    return _executable(tmp_path / "fake-raptor-opencode", RECORDER)


def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file()
    mode = SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "install.sh must be executable"


def test_script_parses() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_help_exits_zero_and_documents_flags(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    _minimal_path(tmp_path)

    result = _run(root, ["--help"], {})

    assert result.returncode == 0
    assert "Usage: ./install.sh" in result.stdout
    for flag in ("--ref", "--merge-config", "--skip-deps", "--dry-run", "--raptor-home"):
        assert flag in result.stdout


def test_pipx_branch_forwards_arguments(tmp_path: Path, fake_raptor: Path) -> None:
    root = _checkout(tmp_path)
    shims = _minimal_path(tmp_path)
    pipx_log = tmp_path / "pipx.log"
    _executable(
        shims / "pipx",
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{pipx_log}"
cp "{fake_raptor}" "{shims}/raptor-opencode"
""",
    )

    args = ["--ref", "v9.9.9", "--merge-config", "--skip-deps", "--yes", "--raptor-home", "/tmp/r"]
    result = _run(root, args, {})

    assert result.returncode == 0, result.stderr
    assert pipx_log.read_text(encoding="utf-8").strip() == "install --force ."
    recorded = (tmp_path / "args.log").read_text(encoding="utf-8").strip()
    assert recorded == "install " + " ".join(args)
    assert "done. Restart OpenCode" in result.stderr


def test_venv_branch_when_pipx_is_missing(tmp_path: Path, fake_raptor: Path) -> None:
    root = _checkout(tmp_path)
    shims = _minimal_path(tmp_path)
    pip_log = tmp_path / "pip.log"
    python_stub = _executable(
        shims / "python-stub",
        f"""#!/usr/bin/env bash
printf '%s\\n' "$*" >> "{pip_log}"
""",
    )
    _executable(
        shims / "python3",
        f"""#!/usr/bin/env bash
if [[ ${{1:-}} == "-c" ]]; then
  if [[ ${{2:-}} == *print* ]]; then echo "3.12.0"; fi
  exit 0
fi
if [[ ${{1:-}} == "-m" ]]; then
  case ${{2:-}} in
    venv)
      mkdir -p "$3/bin"
      cp "{python_stub}" "$3/bin/python"
      cp "{fake_raptor}" "$3/bin/raptor-opencode"
      ;;
    pip) printf '%s\\n' "$*" >> "{pip_log}" ;;
  esac
fi
exit 0
""",
    )

    result = _run(root, ["--yes", "--skip-deps"], {})

    assert result.returncode == 0, result.stderr
    assert "pipx not found" in result.stderr
    venv_bin = root / ".venv" / "bin" / "raptor-opencode"
    assert venv_bin.is_file()
    assert (tmp_path / "args.log").read_text(
        encoding="utf-8"
    ).strip() == "install --yes --skip-deps"
    assert pip_log.read_text(encoding="utf-8").split()[:3] == ["-m", "pip", "install"]


def test_rejects_python_below_311(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    shims = _minimal_path(tmp_path)
    _executable(
        shims / "python3",
        """#!/usr/bin/env bash
if [[ ${1:-} == "-c" ]]; then
  if [[ ${2:-} == *print* ]]; then echo "3.10.0"; exit 0; fi
  exit 1
fi
exit 0
""",
    )

    result = _run(root, ["--yes"], {})

    assert result.returncode == 1
    assert "Python 3.11+ required" in result.stderr
    assert "3.10.0" in result.stderr
    assert not (tmp_path / "args.log").exists()


def test_dry_run_skips_installation(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    shims = _minimal_path(tmp_path)
    python_log = tmp_path / "python.log"
    _executable(
        shims / "python3",
        f"""#!/usr/bin/env bash
if [[ ${{1:-}} == "-c" ]]; then
  if [[ ${{2:-}} == *print* ]]; then echo "3.12.0"; fi
  exit 0
fi
printf '%s\\n' "$*" >> "{python_log}"
exit 0
""",
    )
    _executable(shims / "pipx", "#!/usr/bin/env bash\nexit 99\n")

    result = _run(
        root, ["--dry-run", "--yes", "--raptor-home", "/tmp/r", "--out-dir", "/tmp/o"], {}
    )

    assert result.returncode == 0, result.stderr
    assert python_log.read_text(encoding="utf-8").strip() == (
        "-m raptor_opencode install --dry-run --yes --raptor-home /tmp/r --out-dir /tmp/o"
    )
    assert not (root / ".venv").exists()


def test_propagates_cli_failure_exit_code(tmp_path: Path, fake_raptor: Path) -> None:
    root = _checkout(tmp_path)
    shims = _minimal_path(tmp_path)
    _executable(
        shims / "pipx",
        f"""#!/usr/bin/env bash
cp "{fake_raptor}" "{shims}/raptor-opencode"
""",
    )

    result = _run(root, ["--yes"], {"FAKE_EXIT_CODE": "3"})

    assert result.returncode == 3
    assert "install failed with exit code 3" in result.stderr


def test_fails_without_pyproject(tmp_path: Path) -> None:
    root = _checkout(tmp_path)
    (root / "pyproject.toml").unlink()
    _minimal_path(tmp_path)

    result = _run(root, ["--yes"], {})

    assert result.returncode == 1
    assert "pyproject.toml not found" in result.stderr
