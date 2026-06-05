from __future__ import annotations

from console_api.main import _ensure_default_console_user
from console_api.security import verify_password


class _ExecuteResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    def __init__(self) -> None:
        self.added = []

    async def execute(self, stmt):
        del stmt
        return _ExecuteResult()

    def add(self, row) -> None:
        self.added.append(row)


async def test_ensure_default_console_user_creates_admin_from_env(monkeypatch) -> None:
    monkeypatch.setenv("CONSOLE_ADMIN_EMAIL", "owner@example.com")
    monkeypatch.setenv("CONSOLE_ADMIN_PASSWORD", "secret-password")
    monkeypatch.setenv("CONSOLE_ADMIN_NAME", "Owner")
    session = _FakeSession()

    await _ensure_default_console_user(session)

    assert len(session.added) == 1
    user = session.added[0]
    assert user.email == "owner@example.com"
    assert user.display_name == "Owner"
    assert user.role == "admin"
    assert user.active is True
    assert verify_password("secret-password", user.password_hash)
