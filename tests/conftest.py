"""Shared pytest fixtures for the gamba_pick test suite."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    """The repository root, useful for tests that read fixture data."""
    return REPO_ROOT
