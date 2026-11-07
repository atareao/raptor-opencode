"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def raptor_fixture() -> Path:
    """Synthetic RAPTOR checkout mirroring the shape of upstream v3.1.0."""
    return FIXTURES / "raptor"
