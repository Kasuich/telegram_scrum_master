"""Playwright Telemost joiner."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from meeting_capture.config import CaptureSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JoinResult:
    admitted: bool
    waiting_room: bool = False
    skipped_reason: str | None = None
    participants_observed: list[dict[str, Any]] = field(default_factory=list)


class TelemostBot:
    async def join(self, telemost_url: str, *, display_name: str, timeout_sec: int) -> JoinResult:
        raise NotImplementedError

    async def wait_until_finished(
        self,
        *,
        stop_event: asyncio.Event,
        max_duration_sec: int,
    ) -> str:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError


class PlaywrightTelemostBot(TelemostBot):
    """Join Telemost as a browser guest using Playwright."""

    def __init__(self, settings: CaptureSettings) -> None:
        self.settings = settings
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def join(self, telemost_url: str, *, display_name: str, timeout_sec: int) -> JoinResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("playwright is not installed for meeting-capture") from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=False,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--use-fake-ui-for-media-stream",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            permissions=["camera", "microphone"],
        )
        self._page = await self._context.new_page()
        page = self._page

        await page.goto(telemost_url, wait_until="domcontentloaded", timeout=timeout_sec * 1000)
        await self._click_first(
            [
                "text=/Продолжить в браузере/i",
                "text=/Continue in browser/i",
                "text=/Try in browser/i",
                "text=/Остаться в браузере/i",
            ],
            timeout_ms=8_000,
        )

        await self._fill_name(display_name)
        await self._disable_media()
        await self._click_first(
            [
                "text=/Продолжить$/i",
                "text=/Подключиться/i",
                "text=/Join meeting/i",
                "text=/Join$/i",
                "button:has-text('Continue')",
            ],
            timeout_ms=20_000,
        )

        deadline = asyncio.get_running_loop().time() + timeout_sec
        saw_waiting_room = False
        while asyncio.get_running_loop().time() < deadline:
            body = await self._body_text()
            if self._looks_like_end_screen(body):
                return JoinResult(
                    admitted=False,
                    waiting_room=saw_waiting_room,
                    skipped_reason="meeting ended before bot was admitted",
                )
            if self._looks_like_waiting_room(body):
                saw_waiting_room = True
            if await self._looks_admitted():
                return JoinResult(
                    admitted=True,
                    waiting_room=saw_waiting_room,
                    participants_observed=await self._participants_best_effort(),
                )
            await asyncio.sleep(2)

        return JoinResult(
            admitted=False,
            waiting_room=saw_waiting_room,
            skipped_reason="not admitted before join timeout",
        )

    async def wait_until_finished(
        self,
        *,
        stop_event: asyncio.Event,
        max_duration_sec: int,
    ) -> str:
        deadline = asyncio.get_running_loop().time() + max_duration_sec
        while asyncio.get_running_loop().time() < deadline:
            if stop_event.is_set():
                return "stop requested"
            body = await self._body_text()
            if self._looks_like_end_screen(body):
                return "meeting ended"
            if self._page is not None and self._page.is_closed():
                return "page closed"
            await asyncio.sleep(5)
        return "max duration reached"

    async def close(self) -> None:
        for obj in (self._context, self._browser):
            if obj is not None:
                try:
                    await obj.close()
                except Exception:
                    logger.exception("Failed to close Playwright resource")
        if self._playwright is not None:
            await self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    async def _click_first(self, selectors: list[str], *, timeout_ms: int) -> bool:
        if self._page is None:
            return False
        for selector in selectors:
            try:
                locator = self._page.locator(selector).first
                await locator.click(timeout=timeout_ms)
                return True
            except Exception:
                continue
        return False

    async def _fill_name(self, display_name: str) -> None:
        if self._page is None:
            return
        selectors = [
            "input[name='name']",
            "input[placeholder*='имя' i]",
            "input[placeholder*='name' i]",
            "input[type='text']",
        ]
        for selector in selectors:
            try:
                locator = self._page.locator(selector).first
                await locator.fill(display_name, timeout=5_000)
                return
            except Exception:
                continue

    async def _disable_media(self) -> None:
        await self._click_first(
            [
                "button[aria-label*='Выключить микрофон' i]",
                "button[aria-label*='Mute' i]",
                "button:has-text('Микрофон')",
            ],
            timeout_ms=2_000,
        )
        await self._click_first(
            [
                "button[aria-label*='Выключить камеру' i]",
                "button[aria-label*='camera' i]",
                "button:has-text('Камера')",
            ],
            timeout_ms=2_000,
        )

    async def _body_text(self) -> str:
        if self._page is None:
            return ""
        try:
            return await self._page.locator("body").inner_text(timeout=2_000)
        except Exception:
            return ""

    async def _looks_admitted(self) -> bool:
        if self._page is None:
            return False
        selectors = [
            "[aria-label*='participants' i]",
            "[aria-label*='участник' i]",
            "button[aria-label*='microphone' i]",
            "button[aria-label*='микрофон' i]",
            "video",
            "canvas",
        ]
        for selector in selectors:
            try:
                if await self._page.locator(selector).count() > 0:
                    return True
            except Exception:
                continue
        return False

    async def _participants_best_effort(self) -> list[dict[str, Any]]:
        body = await self._body_text()
        names: list[dict[str, Any]] = []
        for line in body.splitlines():
            value = line.strip()
            if not value or len(value) > 80:
                continue
            if re.search(r"(микрофон|камера|чат|запись|meeting|camera|microphone)", value, re.I):
                continue
            names.append({"display_name": value, "source": "telemost_ui"})
        return names[:30]

    @staticmethod
    def _looks_like_waiting_room(body: str) -> bool:
        return bool(
            re.search(
                r"(комнат[ае] ожидания|waiting room|wait.*host|допустит)",
                body,
                re.I,
            )
        )

    @staticmethod
    def _looks_like_end_screen(body: str) -> bool:
        return bool(
            re.search(
                r"(встреча завершена|звонок завершен|meeting.*ended|"
                r"call.*ended|трансляция закончилась)",
                body,
                re.I,
            )
        )


__all__ = ["JoinResult", "PlaywrightTelemostBot", "TelemostBot"]
