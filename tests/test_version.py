"""Version metadata stays consistent across packaging, package and CLI."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from raptor_opencode import __version__
from raptor_opencode.cli import main

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _project_version() -> str:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def test_package_version_matches_pyproject() -> None:
    assert __version__ == _project_version()


def test_version_is_a_semver_release() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_version_subcommand_prints_the_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"raptor-opencode {__version__}"
