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

    async def poll_active_speakers(
        self,
        *,
        stop_event: asyncio.Event,
        record_start_monotonic: float,
    ) -> list[dict[str, Any]]:
        """Collect an active-speaker timeline while the meeting runs.

        Default: no-op (returns []). Bots that can read the DOM override this.
        """
        return []

    async def close(self) -> None:
        raise NotImplementedError


class PlaywrightTelemostBot(TelemostBot):
    """Join Telemost as a browser guest using Playwright."""

    # How long the bot must be alone (no other participants/video) before the
    # meeting is treated as ended. Guards against a brief solo blip mid-call.
    _ALONE_GRACE_SEC = 30

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
        # The meeting ends when ANY of these fire (whichever first):
        #   * stop requested (host pressed /stop or service shutdown)
        #   * Telemost shows an end screen ("встреча завершена", "вы вышли", ...)
        #   * the page/tab closed (call window gone)
        #   * the bot is left alone — no other participants/video for ALONE_GRACE
        #     consecutive checks (covers the common case where the host just
        #     leaves and Telemost never renders an explicit end screen).
        # ``max_duration_sec`` stays a hard safety ceiling, NOT the normal path.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_duration_sec
        poll_sec = 5
        alone_grace_checks = max(int(self._ALONE_GRACE_SEC / poll_sec), 1)
        alone_checks = 0
        # We must observe the meeting populated at least once before "alone"
        # can mean "ended" — otherwise a slow first render would end instantly.
        saw_others = False
        while loop.time() < deadline:
            if stop_event.is_set():
                return "stop requested"
            if self._page is not None and self._page.is_closed():
                return "page closed"
            body = await self._body_text()
            if self._looks_like_end_screen(body):
                return "meeting ended"
            if self._looks_like_left_call_url():
                return "left call"
            others = await self._other_participant_signal()
            if others:
                saw_others = True
                alone_checks = 0
            elif saw_others:
                alone_checks += 1
                if alone_checks >= alone_grace_checks:
                    return "alone in meeting"
            await asyncio.sleep(poll_sec)
        return "max duration reached"

    async def poll_active_speakers(
        self,
        *,
        stop_event: asyncio.Event,
        record_start_monotonic: float,
    ) -> list[dict[str, Any]]:
        """Sample the highlighted (speaking) participant tile periodically.

        Builds a timeline of ``{start_ms, end_ms, display_name}`` windows whose
        time base is the recording start, so it aligns with SpeechKit timecodes.
        Best-effort: any DOM error is swallowed; an unreadable poll just skips a
        sample. The diarization labels remain a full fallback if this is empty.
        """
        loop = asyncio.get_running_loop()
        timeline: list[dict[str, Any]] = []
        current_name: str | None = None
        current_start_ms = 0
        poll_sec = 1.0
        while not stop_event.is_set():
            if self._page is not None and self._page.is_closed():
                break
            now_ms = int((loop.time() - record_start_monotonic) * 1000)
            name = await self._active_speaker_name()
            if name != current_name:
                if current_name is not None:
                    timeline.append(
                        {
                            "start_ms": current_start_ms,
                            "end_ms": now_ms,
                            "display_name": current_name,
                        }
                    )
                current_name = name
                current_start_ms = now_ms
            await asyncio.sleep(poll_sec)
        # Close the trailing window.
        if current_name is not None:
            end_ms = int((loop.time() - record_start_monotonic) * 1000)
            timeline.append(
                {"start_ms": current_start_ms, "end_ms": end_ms, "display_name": current_name}
            )
        return timeline

    async def _active_speaker_name(self) -> str | None:
        """Best-effort: display name of the currently speaking participant.

        Telemost marks the active tile (speaking ring / data attribute). Selectors
        are tried in order; the first hit's accessible name is returned. Returns
        None when nothing is detected (silence or unreadable DOM).
        """
        if self._page is None:
            return None
        selectors = [
            "[data-speaking='true']",
            "[class*='speaking']",
            "[class*='active-speaker']",
            "[aria-label*='говорит' i]",
            "[aria-label*='speaking' i]",
        ]
        for selector in selectors:
            try:
                locator = self._page.locator(selector).first
                if await locator.count() == 0:
                    continue
                name = (
                    await locator.get_attribute("aria-label")
                    or await locator.get_attribute("data-name")
                    or (await locator.inner_text(timeout=1_000))
                )
                name = (name or "").strip()
                name = re.sub(r"\b(говорит|speaking|микрофон|microphone)\b", "", name, flags=re.I)
                name = name.strip()
                if name and len(name) <= 80:
                    return name
            except Exception:
                continue
        return None

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

    async def _other_participant_signal(self) -> bool:
        """Best-effort: is anyone besides the bot still in the call?

        Telemost renders a remote video/canvas tile per participant and a
        participants counter. We treat the presence of any video/canvas tile,
        or a participants count > 1, as "others present". All DOM reads are
        guarded — on any failure we return True (assume populated) so a flaky
        DOM read never ends a live meeting prematurely.
        """
        if self._page is None:
            return False
        try:
            media = await self._page.locator("video, canvas").count()
            if media > 0:
                return True
        except Exception:
            return True  # DOM hiccup: do not treat as "alone"
        count = await self._participant_count()
        if count is None:
            return True  # unknown: assume populated, rely on end-screen/url
        return count > 1

    async def _participant_count(self) -> int | None:
        """Parse a participants counter from the UI, if present."""
        if self._page is None:
            return None
        for selector in (
            "[aria-label*='участник' i]",
            "[aria-label*='participant' i]",
        ):
            try:
                locator = self._page.locator(selector).first
                if await locator.count() == 0:
                    continue
                label = await locator.get_attribute("aria-label")
                match = re.search(r"\d+", label or "")
                if match:
                    return int(match.group())
            except Exception:
                continue
        return None

    def _looks_like_left_call_url(self) -> bool:
        """The bot was redirected off the call (feedback/landing page)."""
        if self._page is None:
            return False
        try:
            url = self._page.url or ""
        except Exception:
            return False
        pattern = r"(/feedback|/leave|/goodbye|telemost\.yandex\.[a-z]+/?$)"
        return bool(re.search(pattern, url, re.I))

    @staticmethod
    def _looks_like_end_screen(body: str) -> bool:
        return bool(
            re.search(
                r"(встреча (завершена|закончена|завершилась)|звонок (завершен|завершён|окончен)|"
                r"вы вышли|вы покинули|покинули (звонок|встречу)|вы вышли из звонка|"
                r"вернуться на главную|присоединиться снова|"
                r"meeting.*(ended|is over)|call.*ended|you('?ve| have) left|"
                r"rejoin|join again|трансляция закончилась)",
                body,
                re.I,
            )
        )


__all__ = ["JoinResult", "PlaywrightTelemostBot", "TelemostBot"]
