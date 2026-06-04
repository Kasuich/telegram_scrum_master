"""
Load the nearest .env file into os.environ for smoke tests only.

This is needed because nested BaseSettings subclasses (DatabaseConfig etc.)
read from os.environ directly, not from the parent Config's env_file.

IMPORTANT: the env load is an autouse fixture (not a module-level call) so it
only runs when a smoke test actually executes. A module-level side effect
would pollute os.environ for the whole pytest session — including unrelated
config tests — even when smoke tests are deselected via ``-m "not smoke"``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _load_env_file() -> None:
    """Parse the nearest .env and set missing keys into os.environ."""
    for parent in Path(__file__).parents:
        env_file = parent / ".env"
        if env_file.exists():
            for raw in env_file.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
            return


@pytest.fixture(autouse=True)
def _smoke_env() -> None:
    """Ensure real credentials from .env are available for smoke tests."""
    _load_env_file()
