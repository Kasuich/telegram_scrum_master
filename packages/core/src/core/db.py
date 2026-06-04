"""
Database layer with async SQLAlchemy 2.0 support.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool

from core.config import get_config

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_db_engine(url: str | None = None, **kwargs: Any) -> AsyncEngine:
    """
    Create async database engine with connection pooling.

    Args:
        url: Database URL (uses config if not provided)
        **kwargs: Additional engine arguments

    Returns:
        Configured AsyncEngine
    """
    if url is None:
        url = get_config().database_url

    pool_size = kwargs.pop("pool_size", get_config().database.database_pool_size)
    max_overflow = kwargs.pop("max_overflow", get_config().database.database_max_overflow)
    pool_timeout = kwargs.pop("pool_timeout", get_config().database.database_pool_timeout)

    echo = kwargs.pop("echo", get_config().app.debug)

    return create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        poolclass=AsyncAdaptedQueuePool,
        echo=echo,
        **kwargs,
    )


def get_engine() -> AsyncEngine:
    """
    Get or create the global database engine.

    Returns:
        AsyncEngine singleton
    """
    global _engine
    if _engine is None:
        _engine = create_db_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    Get or create the global session factory.

    Returns:
        async_sessionmaker instance
    """
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager for database sessions.

    Usage:
        async with get_session() as session:
            result = await session.execute(select(Model))
            return result.scalar_one()

    Yields:
        AsyncSession with automatic cleanup

    Raises:
        Exception: Re-raises any exception after rollback
    """
    factory = get_session_factory()
    session = factory()

    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def create_all_tables(engine: AsyncEngine | None = None) -> None:
    """Create all ORM tables if they don't exist (idempotent).

    Used for bootstrapping the test-VPS / dev databases where Alembic
    migrations are not run automatically. ``checkfirst`` is implied by
    ``create_all`` so existing tables and enum types are left untouched.
    """
    from core.models import Base

    eng = engine or get_engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def health_check() -> dict[str, Any]:
    """
    Check database connectivity and pool health.

    Returns:
        Health status dictionary with connection info
    """
    engine = get_engine()
    pool = engine.pool

    try:
        async with get_session() as session:
            await session.execute(text("SELECT 1"))

        return {
            "status": "healthy",
            "pool_size": pool.size(),
            "pool_checked_in": pool.checkedin(),
            "pool_overflow": pool.overflow(),
            "pool_checked_out": pool.checkedout(),
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
        }


async def close_engine() -> None:
    """
    Close the global database engine and all connections.

    Call this on application shutdown.
    """
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def reset_engine() -> None:
    """
    Reset the global engine (useful for testing).
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None


class Checkpointer:
    """
    Checkpoint storage for LangGraph state persistence.

    Provides serialized checkpoint storage in database.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None):
        self._session_factory = session_factory or get_session_factory()

    async def list(
        self,
        thread_id: str,
        checkpoint_ns: str = "",
    ) -> list[dict[str, Any]]:
        """
        List checkpoints for a thread.

        Args:
            thread_id: Thread identifier
            checkpoint_ns: Namespace for checkpoints

        Returns:
            List of checkpoint metadata
        """
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT checkpoint_id, created_at, metadata
                    FROM langchain_checkpoints
                    WHERE thread_id = :thread_id
                      AND checkpoint_ns = :checkpoint_ns
                    ORDER BY created_at DESC
                """),
                {"thread_id": thread_id, "checkpoint_ns": checkpoint_ns},
            )
            return [dict(row._mapping) for row in result.fetchall()]

    async def get(
        self,
        thread_id: str,
        checkpoint_id: str,
    ) -> dict[str, Any] | None:
        """
        Get a specific checkpoint.

        Args:
            thread_id: Thread identifier
            checkpoint_id: Checkpoint identifier

        Returns:
            Checkpoint data or None if not found
        """
        async with self._session_factory() as session:
            result = await session.execute(
                text("""
                    SELECT checkpoint_data, metadata
                    FROM langchain_checkpoints
                    WHERE thread_id = :thread_id
                      AND checkpoint_id = :checkpoint_id
                """),
                {"thread_id": thread_id, "checkpoint_id": checkpoint_id},
            )
            row = result.fetchone()
            if row:
                return {
                    "checkpoint": row.checkpoint_data,
                    "metadata": row.metadata,
                }
            return None

    async def put(
        self,
        thread_id: str,
        checkpoint_id: str,
        checkpoint_data: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        checkpoint_ns: str = "",
    ) -> None:
        """
        Store a checkpoint.

        Args:
            thread_id: Thread identifier
            checkpoint_id: Checkpoint identifier
            checkpoint_data: Serialized checkpoint state
            metadata: Optional metadata
            checkpoint_ns: Namespace for checkpoints
        """
        async with self._session_factory() as session:
            await session.execute(
                text("""
                    INSERT INTO langchain_checkpoints
                        (thread_id, checkpoint_ns, checkpoint_id, checkpoint_data, metadata)
                    VALUES
                        (:thread_id, :checkpoint_ns, :checkpoint_id, :checkpoint_data, :metadata)
                    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)
                    DO UPDATE SET checkpoint_data = :checkpoint_data, metadata = :metadata
                """),
                {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                    "checkpoint_data": checkpoint_data,
                    "metadata": metadata or {},
                },
            )
            await session.commit()


__all__ = [
    "create_db_engine",
    "get_engine",
    "get_session_factory",
    "get_session",
    "create_all_tables",
    "health_check",
    "close_engine",
    "reset_engine",
    "Checkpointer",
]
