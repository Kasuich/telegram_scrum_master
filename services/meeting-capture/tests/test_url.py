from __future__ import annotations

import pytest
from meeting_capture.url import TelemostUrlError, normalize_telemost_url


def test_normalize_telemost_join_url() -> None:
    assert (
        normalize_telemost_url("telemost.yandex.ru/j/ABC_123/?utm=x#frag")
        == "https://telemost.yandex.ru/j/ABC_123?utm=x"
    )


def test_normalize_telemost_live_url() -> None:
    assert (
        normalize_telemost_url("https://telemost.yandex.com/live/abc-def")
        == "https://telemost.yandex.com/live/abc-def"
    )


def test_normalize_telemost_360_join_url() -> None:
    assert (
        normalize_telemost_url("https://telemost.360.yandex.ru/j/2548640103")
        == "https://telemost.360.yandex.ru/j/2548640103"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://telemost.yandex.ru/j/123",
        "https://example.com/j/123",
        "https://telemost.yandex.ru/not-a-meeting/123",
        "",
    ],
)
def test_rejects_unsupported_urls(url: str) -> None:
    with pytest.raises(TelemostUrlError):
        normalize_telemost_url(url)
