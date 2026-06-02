"""
Tests for database layer.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_db_config() -> MagicMock:
    """Mock database configuration."""
    config = MagicMock()
    config.database_url = "postgresql+asyncpg://test:test@localhost:5432/test"
    config.database.database_pool_size = 10
    config.database.database_max_overflow = 5
    config.database.database_pool_timeout = 15
    config.app.debug = False
    return config


class TestCreateDbEngine:
    """Tests for create_db_engine function."""

    def test_creates_engine_with_url(self) -> None:
        """Engine created with provided URL."""
        from core.db import create_db_engine

        with patch("core.db.get_config") as mock_get_config:
            mock_get_config.return_value = MagicMock(
                database_url="postgresql+asyncpg://test:test@localhost:5432/test",
                database=MagicMock(
                    database_pool_size=10,
                    database_max_overflow=5,
                    database_pool_timeout=15,
                ),
                app=MagicMock(debug=False),
            )
            engine = create_db_engine("postgresql+asyncpg://test:test@localhost:5432/test")
            assert engine is not None
            assert "postgresql" in str(engine.url)

    def test_creates_engine_with_defaults(self) -> None:
        """Engine created with config defaults."""
        from core.db import create_db_engine

        with patch("core.db.get_config") as mock_get_config:
            mock_cfg = MagicMock()
            mock_cfg.database_url = "postgresql+asyncpg://test:test@localhost:5432/test"
            mock_cfg.database.database_pool_size = 10
            mock_cfg.database.database_max_overflow = 5
            mock_cfg.database.database_pool_timeout = 15
            mock_cfg.app.debug = False
            mock_get_config.return_value = mock_cfg

            engine = create_db_engine()
            assert engine is not None

    def test_custom_pool_size(self) -> None:
        """Engine respects custom pool size."""
        from core.db import create_db_engine

        with patch("core.db.get_config") as mock_get_config:
            mock_get_config.return_value = MagicMock(
                database_url="postgresql+asyncpg://test:test@localhost:5432/test",
                database=MagicMock(
                    database_pool_size=10,
                    database_max_overflow=5,
                    database_pool_timeout=15,
                ),
                app=MagicMock(debug=False),
            )
            engine = create_db_engine(
                "postgresql+asyncpg://test:test@localhost:5432/test",
                pool_size=50,
            )
            assert engine is not None


class TestGetEngine:
    """Tests for get_engine singleton."""

    def test_returns_singleton(self) -> None:
        """get_engine returns same instance."""
        from core.db import get_engine, reset_engine

        with patch("core.db.create_db_engine") as mock_create:
            mock_create.return_value = MagicMock()
            reset_engine()
            engine1 = get_engine()
            engine2 = get_engine()
            assert engine1 is engine2


class TestGetSession:
    """Tests for get_session context manager."""

    @pytest.mark.asyncio
    async def test_session_commits_on_success(self) -> None:
        """Session commits when no exception."""
        from core.db import get_session

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.close = AsyncMock()

        with patch("core.db.get_session_factory") as mock_factory:
            mock_factory.return_value = MagicMock(return_value=mock_session)

            async with get_session() as session:
                assert session is mock_session

            mock_session.commit.assert_called_once()
            mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_session_rollbacks_on_exception(self) -> None:
        """Session rolls back when exception raised."""
        from core.db import get_session

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        with patch("core.db.get_session_factory") as mock_factory:
            mock_factory.return_value = MagicMock(return_value=mock_session)

            with pytest.raises(ValueError):
                async with get_session() as _session:
                    raise ValueError("Test error")

            mock_session.rollback.assert_called_once()
            mock_session.commit.assert_not_called()
            mock_session.close.assert_called_once()


class TestHealthCheck:
    """Tests for health_check function."""

    @pytest.mark.asyncio
    async def test_healthy_status(self) -> None:
        """Returns healthy when DB responds."""
        from core.db import health_check

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()

        mock_pool = MagicMock()
        mock_pool.size.return_value = 5
        mock_pool.checkedin.return_value = 3
        mock_pool.overflow.return_value = 0
        mock_pool.checkedout.return_value = 2

        with patch("core.db.get_engine") as mock_engine:
            mock_engine.return_value.pool = mock_pool
            with patch("core.db.get_session") as mock_get_session:
                mock_get_session.return_value.__aenter__ = AsyncMock(return_value=mock_session)
                mock_get_session.return_value.__aexit__ = AsyncMock()

                result = await health_check()

        assert result["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_unhealthy_on_error(self) -> None:
        """Returns unhealthy when DB fails."""
        from core.db import health_check

        mock_pool = MagicMock()
        mock_pool.size.return_value = 0

        with patch("core.db.get_engine") as mock_engine:
            mock_engine.return_value.pool = mock_pool
            with patch("core.db.get_session") as mock_get_session:
                mock_get_session.return_value.__aenter__ = AsyncMock(side_effect=Exception("DB error"))

                result = await health_check()

        assert result["status"] == "unhealthy"
        assert "error" in result


class TestCheckpointer:
    """Tests for Checkpointer class."""

    @pytest.mark.asyncio
    async def test_list_checkpoints(self) -> None:
        """Lists checkpoints for thread."""
        from core.db import Checkpointer

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            MagicMock(_mapping={"checkpoint_id": "cp1", "created_at": "2024-01-01", "metadata": {}}),
            MagicMock(_mapping={"checkpoint_id": "cp2", "created_at": "2024-01-02", "metadata": {}}),
        ]
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("core.db.async_sessionmaker") as mock_factory:
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock()

            cp = Checkpointer(session_factory=mock_factory.return_value)
            checkpoints = await cp.list("thread-1")

        assert len(checkpoints) == 2

    @pytest.mark.asyncio
    async def test_get_checkpoint_found(self) -> None:
        """Gets checkpoint when exists."""
        from core.db import Checkpointer

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = MagicMock(
            checkpoint_data={"state": "test"},
            metadata={"key": "value"},
        )
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("core.db.async_sessionmaker") as mock_factory:
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock()

            cp = Checkpointer(session_factory=mock_factory.return_value)
            result = await cp.get("thread-1", "cp1")

        assert result is not None
        assert result["checkpoint"] == {"state": "test"}

    @pytest.mark.asyncio
    async def test_get_checkpoint_not_found(self) -> None:
        """Returns None when checkpoint not found."""
        from core.db import Checkpointer

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("core.db.async_sessionmaker") as mock_factory:
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock()

            cp = Checkpointer(session_factory=mock_factory.return_value)
            result = await cp.get("thread-1", "nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_put_checkpoint(self) -> None:
        """Stores checkpoint."""
        from core.db import Checkpointer

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        with patch("core.db.async_sessionmaker") as mock_factory:
            mock_factory.return_value.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_factory.return_value.return_value.__aexit__ = AsyncMock()

            cp = Checkpointer(session_factory=mock_factory.return_value)
            await cp.put(
                "thread-1",
                "cp1",
                {"state": "test"},
                metadata={"key": "value"},
            )

        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()


class TestResetEngine:
    """Tests for reset_engine function."""

    def test_resets_singleton(self) -> None:
        """reset_engine clears singleton."""
        from core.db import get_engine, reset_engine

        with patch("core.db.create_db_engine") as mock_create:
            mock_create.side_effect = [MagicMock(), MagicMock()]
            reset_engine()
            engine1 = get_engine()
            reset_engine()
            engine2 = get_engine()
            assert engine1 is not engine2
