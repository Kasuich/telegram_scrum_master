"""
Example 02: Database operations.

Shows engine creation, session management, health check, and checkpointer.
Run: python -m examples.02_db_operations  (from packages/core/, requires real DB)
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/pm_agent")
os.environ.setdefault("YC_API_KEY", "example_api_key_12345678901234567890")
os.environ.setdefault("YC_FOLDER_ID", "b1g1234567890abcdef")
os.environ.setdefault("TRACKER_TOKEN", "example_oauth_token_12345678901234567890")
os.environ.setdefault("TRACKER_ORG_ID", "12345678901234567890")


async def demo_with_mock() -> None:
    """Demonstrate DB API using mocked session (no real DB required)."""
    from core.db import get_session, health_check, Checkpointer, reset_engine

    reset_engine()

    # --- Session lifecycle demo (mocked) ---
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()

    with patch("core.db.get_session_factory") as mock_factory:
        mock_factory.return_value = MagicMock(return_value=mock_session)

        async with get_session() as session:
            print("Session acquired")
            # In real code: result = await session.execute(select(Organization))
            print("Query executed (mocked)")
        print("Session committed and closed")

    # --- Health check demo (mocked) ---
    mock_pool = MagicMock()
    mock_pool.size.return_value = 20
    mock_pool.checkedin.return_value = 18
    mock_pool.overflow.return_value = 0
    mock_pool.checkedout.return_value = 2

    with patch("core.db.get_engine") as mock_engine:
        mock_engine.return_value.pool = mock_pool
        with patch("core.db.get_session") as mock_get_session:
            mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_get_session.return_value.__aexit__ = AsyncMock()

            health = await health_check()
            print(f"\nDB health: {health['status']}")
            print(f"Pool size: {health['pool_size']}, checked out: {health['pool_checked_out']}")

    # --- Checkpointer demo (mocked) ---
    mock_cp_session = AsyncMock()
    mock_cp_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
    mock_cp_session.commit = AsyncMock()

    with patch("core.db.async_sessionmaker") as mock_sm:
        mock_sm.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_cp_session)
        mock_sm.return_value.return_value.__aexit__ = AsyncMock()

        cp = Checkpointer(session_factory=mock_sm.return_value)

        await cp.put(
            thread_id="thread-001",
            checkpoint_id="cp-001",
            checkpoint_data={"step": 3, "messages": ["Hello", "World"]},
            metadata={"model": "yandexgpt-pro"},
        )
        print("\nCheckpoint saved")

        result = await cp.get("thread-001", "nonexistent")
        print(f"Checkpoint lookup: {result}")


def main() -> None:
    asyncio.run(demo_with_mock())


if __name__ == "__main__":
    main()
