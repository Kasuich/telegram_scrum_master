from __future__ import annotations

from telegram_gateway.bridge import MainBridgeClient


def test_request_url_accepts_bridge_root() -> None:
    client = MainBridgeClient(
        base_url="http://10.99.0.1:8000",
        key_id="key1",
        key_secret="secret",
    )

    url, signed_path = client._request_url("/internal/telegram/v1/outbox:lease")

    assert url == "http://10.99.0.1:8000/internal/telegram/v1/outbox:lease"
    assert signed_path == "/internal/telegram/v1/outbox:lease"


def test_request_url_accepts_bridge_prefix() -> None:
    client = MainBridgeClient(
        base_url="http://10.99.0.1:8000/internal/telegram/v1",
        key_id="key1",
        key_secret="secret",
    )

    url, signed_path = client._request_url("/internal/telegram/v1/outbox:lease")

    assert url == "http://10.99.0.1:8000/internal/telegram/v1/outbox:lease"
    assert signed_path == "/internal/telegram/v1/outbox:lease"
