"""
Alembic environment configuration for PM Agent Platform.

Supports async SQLAlchemy 2.0 with asyncpg driver.
Database URL is sourced from:
1. get_config().database_url  (via core.config)
2. ALEMBIC_DATABASE_URL env var (fallback when config is not fully set up)
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

# ---------------------------------------------------------------------------
# Alembic Config object — provides access to values within alembic.ini
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Import application models so Alembic can detect schema changes
# ---------------------------------------------------------------------------
from core.models import Base  # noqa: E402

target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# Resolve database URL
# ---------------------------------------------------------------------------


def _get_database_url() -> str:
    """
    Return the database URL for Alembic.

    Priority:
    1. URL already set in alembic.ini / via -x option
    2. get_config().database_url from application config
    3. ALEMBIC_DATABASE_URL environment variable

    For async migrations the driver must be ``postgresql+asyncpg://``.
    A plain ``postgresql://`` prefix is rewritten automatically.
    """
    # 1. Explicit URL from alembic.ini / command-line
    ini_url = config.get_main_option("sqlalchemy.url")
    if ini_url:
        return _ensure_asyncpg(ini_url)

    # 2. Application config (may raise if env vars are missing)
    try:
        from core.config import get_config

        url = get_config().database_url
        return _ensure_asyncpg(url)
    except Exception:
        pass

    # 3. Fallback env var
    env_url = os.environ.get("ALEMBIC_DATABASE_URL")
    if env_url:
        return _ensure_asyncpg(env_url)

    raise RuntimeError(
        "Database URL is not configured. "
        "Set DATABASE_URL / ALEMBIC_DATABASE_URL env var or configure core.config."
    )


def _ensure_asyncpg(url: str) -> str:
    """Rewrite a plain postgresql:// URL to use the asyncpg driver."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


# ---------------------------------------------------------------------------
# Offline migrations (no live DB connection)
# ---------------------------------------------------------------------------


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine,
    though an Engine is acceptable here as well. By skipping the Engine
    creation we don't even need a DBAPI to be available.
    """
    url = _get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online migrations (async engine)
# ---------------------------------------------------------------------------


async def run_async_migrations() -> None:
    """Run migrations using an async SQLAlchemy engine."""
    url = _get_database_url()

    # Merge our resolved URL into the alembic config so that
    # async_engine_from_config picks it up correctly.
    config.set_main_option("sqlalchemy.url", url)

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,  # Avoid connection pool issues during migrations
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def do_run_migrations(connection):
    """Execute migrations within a synchronous context (called via run_sync)."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Entry point for online migration mode."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
