"""Tests for summary fan-out: summarizer reply -> Telegram + pm_agent.

The orchestrator and the Telegram outbox are mocked; we assert the dispatcher
reads the summarizer reply and routes it to both legs with the right chat id.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import meeting_capture.dispatcher as dispatcher_mod
from meeting_capture.config import CaptureSettings
from meeting_capture.dispatcher import MeetingDispatcher
from meeting_capture.transcription import TranscriptionResult


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpClient:
    """Scripts /rpc responses by JSON-RPC method; records invoke calls."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.invoke_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> "_FakeHttpClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, json: dict[str, Any]) -> _FakeResponse:
        method = json.get("method")
        if method == "list_agents":
            return _FakeResponse({"result": [{"name": "meeting_summarizer"}, {"name": "pm_agent"}]})
        if method == "invoke":
            params = json.get("params", {})
            self.invoke_calls.append(params)
            if params.get("agent") == "meeting_summarizer":
                return _FakeResponse({"result": {"reply": "## Краткое резюме\nВсё ок"}})
            return _FakeResponse({"result": {"reply": "Создана TASK-1"}})
        return _FakeResponse({"result": {}})


def _dispatcher(tmp_path: Path) -> tuple[MeetingDispatcher, Any]:
    settings = CaptureSettings(
        CAPTURE_WORK_DIR=tmp_path / "work",
        CAPTURE_OBJECT_STORAGE_DIR=tmp_path / "objects",
        ORCHESTRATOR_URL="http://orchestrator:8000",
    )
    repo = SimpleNamespace(team_id=uuid.uuid4())
    disp = MeetingDispatcher(
        repository=repo,  # type: ignore[arg-type]
        settings=settings,
        object_store=SimpleNamespace(),  # type: ignore[arg-type]
        transcriber=SimpleNamespace(),  # type: ignore[arg-type]
        bot_factory=lambda: SimpleNamespace(),  # type: ignore[arg-type]
        recorder_factory=lambda work_dir: SimpleNamespace(),  # type: ignore[arg-type]
    )
    return disp, repo


async def test_fanout_delivers_to_telegram_and_pm_agent(tmp_path: Path, monkeypatch) -> None:
    disp, repo = _dispatcher(tmp_path)
    meeting_id = uuid.uuid4()
    transcription = TranscriptionResult(
        source="speechkit",
        segments=[{"start_ms": 0, "end_ms": 500, "speaker_label": "SPEAKER_00", "text": "привет"}],
    )

    fake_client = _FakeHttpClient()
    monkeypatch.setattr(dispatcher_mod.httpx, "AsyncClient", lambda *a, **k: fake_client)

    # set_status (store summary) is a no-op SimpleNamespace; give it a coroutine.
    async def _set_status(*args: Any, **kwargs: Any) -> None:
        return None

    repo.set_status = _set_status

    enqueued: list[dict[str, Any]] = []

    async def _fake_enqueue(**kwargs: Any) -> uuid.UUID:
        enqueued.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr("meeting_capture.telegram_outbox.enqueue_telegram_message", _fake_enqueue)

    await disp._summarize_and_fanout(meeting_id, transcription, target_chat_id="-100123")

    # Telegram leg
    assert len(enqueued) == 1
    assert enqueued[0]["target_chat_id"] == "-100123"
    assert "Всё ок" in enqueued[0]["text"]
    assert enqueued[0]["team_id"] == repo.team_id

    # pm_agent leg
    pm_calls = [c for c in fake_client.invoke_calls if c.get("agent") == "pm_agent"]
    assert len(pm_calls) == 1
    assert pm_calls[0]["message"].startswith("Оформи доску")
    assert "Всё ок" in pm_calls[0]["message"]
    assert pm_calls[0]["context"]["chat_id"] == "-100123"


async def test_empty_transcript_sends_notice_to_telegram(tmp_path: Path, monkeypatch) -> None:
    disp, repo = _dispatcher(tmp_path)
    meeting_id = uuid.uuid4()
    transcription = TranscriptionResult(
        source="speechkit_s3_not_configured",
        segments=[],
    )

    enqueued: list[dict[str, Any]] = []

    async def _fake_enqueue(**kwargs: Any) -> uuid.UUID:
        enqueued.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr("meeting_capture.telegram_outbox.enqueue_telegram_message", _fake_enqueue)

    await disp._deliver_empty_transcription_notice(
        meeting_id, transcription.source, target_chat_id="-100123"
    )

    assert len(enqueued) == 1
    assert enqueued[0]["target_chat_id"] == "-100123"
    assert "S3 не подключён" in enqueued[0]["text"]
    assert enqueued[0]["team_id"] == repo.team_id


def test_format_transcript_prefers_name_over_label(tmp_path: Path) -> None:
    disp, _ = _dispatcher(tmp_path)
    transcription = TranscriptionResult(
        source="speechkit",
        segments=[
            {
                "start_ms": 0,
                "end_ms": 1000,
                "speaker_label": "SPEAKER_00",
                "speaker_name": "Алиса",
                "text": "привет",
            },
            {
                "start_ms": 65000,
                "end_ms": 66000,
                "speaker_label": "SPEAKER_01",
                "speaker_name": None,
                "text": "ага",
            },
        ],
    )
    text = disp._format_transcript(transcription)
    assert "[00:00] Алиса: привет" in text
    assert "[01:05] SPEAKER_01: ага" in text
