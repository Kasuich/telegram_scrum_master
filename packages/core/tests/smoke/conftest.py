"""
Load the nearest .env file into os.environ before smoke tests run.

This is needed because nested BaseSettings subclasses (DatabaseConfig etc.)
read from os.environ directly, not from the parent Config's env_file.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_env_file() -> None:
    """Parse .env and set missing keys into os.environ."""
    for parent in Path(__file__).parents:
        env_file = parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
            return


_load_env_file()
