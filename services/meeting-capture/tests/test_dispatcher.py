from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from meeting_capture.bot import JoinResult, TelemostBot
from meeting_capture.config import CaptureSettings
from meeting_capture.dispatcher import MeetingDispatcher
from meeting_capture.recorder import Recorder, RecordingFiles
from meeting_capture.storage import LocalObjectStore, UploadedObject
from meeting_capture.transcription import Transcriber, TranscriptionResult


class FakeRepository:
    def __init__(self, meeting_id: uuid.UUID) -> None:
        self.meeting = SimpleNamespace(
            id=meeting_id,
            telemost_url="https://telemost.yandex.ru/j/123",
            language="ru-RU",
            status="scheduled",
            metadata_json={},
        )
        self.statuses: list[str] = []
        self.artifacts: list[dict[str, Any]] = []
        self.transcript: dict[str, Any] | None = None

    async def get(self, meeting_id: uuid.UUID):
        assert meeting_id == self.meeting.id
        return self.meeting

    async def set_status(self, meeting_id: uuid.UUID, status: str, **kwargs: Any):
        assert meeting_id == self.meeting.id
        self.statuses.append(status)
        self.meeting.status = status
        self.meeting.error = kwargs.get("error")
        if kwargs.get("metadata_update"):
            self.meeting.metadata_json = {
                **(self.meeting.metadata_json or {}),
                **kwargs["metadata_update"],
            }
        return self.meeting

    async def add_artifact(self, **kwargs: Any):
        self.artifacts.append(kwargs)
        return SimpleNamespace(**kwargs)

    async def save_transcript(self, **kwargs: Any):
        self.transcript = kwargs
        return SimpleNamespace(**kwargs)


class FakeBot(TelemostBot):
    def __init__(
        self,
        join_result: JoinResult,
        speaker_timeline: list[dict[str, Any]] | None = None,
    ) -> None:
        self.join_result = join_result
        self.closed = False
        self.speaker_timeline = speaker_timeline or []

    async def join(self, telemost_url: str, *, display_name: str, timeout_sec: int) -> JoinResult:
        assert telemost_url.startswith("https://telemost.yandex.ru")
        assert "recording" in display_name
        assert timeout_sec > 0
        return self.join_result

    async def wait_until_finished(
        self,
        *,
        stop_event: asyncio.Event,
        max_duration_sec: int,
    ) -> str:
        assert not stop_event.is_set()
        assert max_duration_sec > 0
        return "meeting ended"

    async def poll_active_speakers(
        self,
        *,
        stop_event: asyncio.Event,
        record_start_monotonic: float,
    ) -> list[dict[str, Any]]:
        return self.speaker_timeline

    async def close(self) -> None:
        self.closed = True


class FakeRecorder(Recorder):
    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.started = False

    async def start(self) -> None:
        self.started = True
        self.work_dir.mkdir(parents=True, exist_ok=True)

    async def stop(self) -> RecordingFiles:
        assert self.started
        recording = self.work_dir / "recording.webm"
        audio = self.work_dir / "audio.ogg"
        recording.write_bytes(b"webm")
        audio.write_bytes(b"ogg")
        return RecordingFiles(recording, audio)


class FakeObjectStore(LocalObjectStore):
    """Local store that also exposes a public URI (simulates S3 for tests)."""

    async def upload_file(self, source: Path, *, key: str, content_type: str) -> UploadedObject:
        obj = await super().upload_file(source, key=key, content_type=content_type)
        return UploadedObject(
            key=obj.key,
            size_bytes=obj.size_bytes,
            content_type=obj.content_type,
            uri=f"https://storage.test/bucket/{key}",
        )


class FakeTranscriber(Transcriber):
    async def transcribe(
        self,
        audio_path: Path,
        *,
        audio_uri: str | None,
        language: str,
        participants_observed: list[dict[str, Any]],
    ) -> TranscriptionResult:
        assert audio_path.name == "audio.ogg"
        assert audio_uri is not None
        assert language == "ru-RU"
        return TranscriptionResult(
            source="speechkit",
            segments=[
                {
                    "start_ms": 0,
                    "end_ms": 500,
                    "speaker_label": "SPEAKER_00",
                    "text": "готово",
                }
            ],
            participants_observed=participants_observed,
        )


def _settings(tmp_path: Path, **overrides: Any) -> CaptureSettings:
    return CaptureSettings(
        CAPTURE_WORK_DIR=tmp_path / "work",
        CAPTURE_OBJECT_STORAGE_DIR=tmp_path / "objects",
        CAPTURE_JOIN_TIMEOUT_SEC=30,
        **overrides,
    )


async def test_dispatcher_records_uploads_transcribes_and_marks_ready(tmp_path: Path) -> None:
    meeting_id = uuid.uuid4()
    repo = FakeRepository(meeting_id)
    settings = _settings(tmp_path)
    bot = FakeBot(
        JoinResult(
            admitted=True,
            participants_observed=[{"display_name": "Alice", "source": "telemost_ui"}],
        )
    )
    dispatcher = MeetingDispatcher(
        repository=repo,  # type: ignore[arg-type]
        settings=settings,
        object_store=FakeObjectStore(settings.object_storage_dir),
        transcriber=FakeTranscriber(),
        bot_factory=lambda: bot,
        recorder_factory=lambda work_dir: FakeRecorder(work_dir),
    )

    await dispatcher.run_now(meeting_id)

    assert repo.statuses == ["joining", "recording", "transcribing", "ready"]
    assert {artifact["kind"] for artifact in repo.artifacts} == {"recording", "audio"}
    assert repo.transcript is not None
    assert repo.transcript["segments"][0]["speaker_label"] == "SPEAKER_00"
    assert bot.closed is True


async def test_dispatcher_maps_speaker_names_from_timeline(tmp_path: Path) -> None:
    meeting_id = uuid.uuid4()
    repo = FakeRepository(meeting_id)
    settings = _settings(tmp_path)
    bot = FakeBot(
        JoinResult(admitted=True),
        speaker_timeline=[{"start_ms": 0, "end_ms": 500, "display_name": "Алиса"}],
    )
    dispatcher = MeetingDispatcher(
        repository=repo,  # type: ignore[arg-type]
        settings=settings,
        object_store=FakeObjectStore(settings.object_storage_dir),
        transcriber=FakeTranscriber(),
        bot_factory=lambda: bot,
        recorder_factory=lambda work_dir: FakeRecorder(work_dir),
    )

    await dispatcher.run_now(meeting_id)

    assert repo.transcript is not None
    seg = repo.transcript["segments"][0]
    assert seg["speaker_label"] == "SPEAKER_00"
    assert seg["speaker_name"] == "Алиса"


async def test_dispatcher_skips_when_bot_waits_too_long(tmp_path: Path) -> None:
    meeting_id = uuid.uuid4()
    repo = FakeRepository(meeting_id)
    settings = _settings(tmp_path)
    dispatcher = MeetingDispatcher(
        repository=repo,  # type: ignore[arg-type]
        settings=settings,
        object_store=LocalObjectStore(settings.object_storage_dir),
        transcriber=FakeTranscriber(),
        bot_factory=lambda: FakeBot(
            JoinResult(
                admitted=False,
                waiting_room=True,
                skipped_reason="not admitted before join timeout",
            )
        ),
        recorder_factory=lambda work_dir: FakeRecorder(work_dir),
    )

    await dispatcher.run_now(meeting_id)

    assert repo.statuses == ["joining", "waiting_room", "skipped"]
    assert repo.meeting.error == "not admitted before join timeout"
    assert repo.artifacts == []
