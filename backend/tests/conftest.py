"""Shared pytest fixtures. Tests are written for the first run on test day (nothing here was executed when written).

Run from backend/:  pytest -q          (needs: pip install -e .[dev])
Sandbox-dependent checks are not in this folder: they live in the evals suites (python -m verity.evals).
"""

from pathlib import Path

import pytest

from verity.config import AGENT_ROOT, Settings
from verity.core.pack import TaskPack, load_packs

from verity_test_support import make_settings


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    """Fake provider, throwaway data dir, plan gate that closes immediately."""
    return make_settings(tmp_path / "data")


@pytest.fixture(scope="session")
def packs() -> dict[str, TaskPack]:
    return load_packs(AGENT_ROOT / "tasks")


@pytest.fixture(scope="session")
def mac4(packs: dict[str, TaskPack]) -> TaskPack:
    return packs["mac4"]
