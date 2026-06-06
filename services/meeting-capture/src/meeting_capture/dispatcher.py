"""Meeting capture lifecycle dispatcher."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from meeting_capture.bot import PlaywrightTelemostBot, TelemostBot
from meeting_capture.config import CaptureSettings
from meeting_capture.recorder import FfmpegRecorder, Recorder, RecordingFiles
from meeting_capture.repository import MeetingRepository, utcnow
from meeting_capture.storage import ObjectStore, UploadedObject, artifact_key
from meeting_capture.transcription import Transcriber, TranscriptionResult

logger = logging.getLogger(__name__)

BotFactory = Callable[[], TelemostBot]
RecorderFactory = Callable[[Path], Recorder]


class MeetingDispatcher:
    """Owns in-process capture tasks and stop signals."""

    def __init__(
        self,
        *,
        repository: MeetingRepository,
        settings: CaptureSettings,
        object_store: ObjectStore,
        transcriber: Transcriber,
        bot_factory: BotFactory | None = None,
        recorder_factory: RecorderFactory | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.object_store = object_store
        self.transcriber = transcriber
        self.bot_factory = bot_factory or (lambda: PlaywrightTelemostBot(settings))
        self.recorder_factory = recorder_factory or (
            lambda work_dir: FfmpegRecorder(settings, work_dir)
        )
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._stop_events: dict[uuid.UUID, asyncio.Event] = {}

    def schedule(self, meeting_id: uuid.UUID, starts_at: datetime | None) -> None:
        if meeting_id in self._tasks and not self._tasks[meeting_id].done():
            return
        task = asyncio.create_task(
            self._run_after_delay(meeting_id, starts_at),
            name=f"meeting-capture:{meeting_id}",
        )
        self._tasks[meeting_id] = task
        task.add_done_callback(lambda done: self._tasks.pop(meeting_id, None))

    async def stop(self, meeting_id: uuid.UUID) -> None:
        event = self._stop_events.get(meeting_id)
        if event is not None:
            event.set()
            return
        meeting = await self.repository.get(meeting_id)
        if meeting and meeting.status in {"scheduled", "joining", "waiting_room", "recording"}:
            await self.repository.set_status(
                meeting_id,
                "skipped",
                error="stop requested before active recorder was found",
                ended_at=utcnow(),
            )

    async def _run_after_delay(self, meeting_id: uuid.UUID, starts_at: datetime | None) -> None:
        delay = self._delay_seconds(starts_at)
        if delay > 0:
            await asyncio.sleep(delay)
        await self.run_now(meeting_id)

    async def run_now(self, meeting_id: uuid.UUID) -> None:
        meeting = await self.repository.get(meeting_id)
        if meeting is None:
            logger.warning("Meeting %s not found before capture", meeting_id)
            return

        stop_event = asyncio.Event()
        self._stop_events[meeting_id] = stop_event
        work_dir = self.settings.work_dir / str(meeting_id)
        bot = self.bot_factory()
        recorder = self.recorder_factory(work_dir)
        recorder_started = False

        try:
            await self.repository.set_status(meeting_id, "joining")
            join_result = await bot.join(
                meeting.telemost_url,
                display_name=self.settings.bot_display_name,
                timeout_sec=self.settings.join_timeout_sec,
            )
            if not join_result.admitted:
                if join_result.waiting_room:
                    await self.repository.set_status(
                        meeting_id,
                        "waiting_room",
                        metadata_update={
                            "participants_observed": join_result.participants_observed
                        },
                    )
                await self.repository.set_status(
                    meeting_id,
                    "skipped",
                    error=join_result.skipped_reason or "bot was not admitted",
                    metadata_update={"participants_observed": join_result.participants_observed},
                    ended_at=utcnow(),
                )
                return

            await self.repository.set_status(
                meeting_id,
                "recording",
                joined_at=utcnow(),
                recording_started_at=utcnow(),
                metadata_update={"participants_observed": join_result.participants_observed},
            )
            await recorder.start()
            recorder_started = True
            finish_reason = await bot.wait_until_finished(
                stop_event=stop_event,
                max_duration_sec=self.settings.max_duration_sec,
            )
            files = await recorder.stop()
            recorder_started = False

            await self.repository.set_status(
                meeting_id,
                "transcribing",
                ended_at=utcnow(),
                metadata_update={"finish_reason": finish_reason},
            )

            uploaded = await self._upload_recording_files(meeting_id, files)
            audio_uri = uploaded.get("audio").uri if uploaded.get("audio") else None
            transcription = await self.transcriber.transcribe(
                files.audio_path,
                audio_uri=audio_uri,
                language=meeting.language,
                participants_observed=join_result.participants_observed,
            )
            await self.repository.save_transcript(
                meeting_id=meeting_id,
                source=transcription.source,
                segments=transcription.segments,
                participants_observed=transcription.participants_observed,
            )
            await self.repository.set_status(meeting_id, "ready")
            await self._notify_summarizer(meeting_id, transcription)
        except Exception as exc:
            logger.exception("Meeting capture failed for %s", meeting_id)
            if recorder_started:
                try:
                    await recorder.stop()
                except Exception:
                    logger.exception("Failed to stop recorder after capture error")
            await self.repository.set_status(
                meeting_id,
                "failed",
                error=str(exc),
                ended_at=utcnow(),
            )
        finally:
            self._stop_events.pop(meeting_id, None)
            await bot.close()

    async def _upload_recording_files(
        self,
        meeting_id: uuid.UUID,
        files: RecordingFiles,
    ) -> dict[str, UploadedObject]:
        expires_at = self._artifact_expiration()
        uploaded: dict[str, UploadedObject] = {}
        candidates = [
            ("recording", files.recording_path, "video/webm"),
            ("audio", files.audio_path, "audio/ogg"),
        ]
        for kind, path, content_type in candidates:
            if not path.exists():
                logger.warning("Expected %s artifact does not exist: %s", kind, path)
                continue
            obj = await self.object_store.upload_file(
                path,
                key=artifact_key(str(meeting_id), path.name),
                content_type=content_type,
            )
            await self.repository.add_artifact(
                meeting_id=meeting_id,
                kind=kind,
                object_key=obj.key,
                content_type=obj.content_type,
                size_bytes=obj.size_bytes,
                expires_at=expires_at,
            )
            uploaded[kind] = obj
        return uploaded

    async def _notify_summarizer(
        self,
        meeting_id: uuid.UUID,
        transcription: TranscriptionResult,
    ) -> None:
        if not self.settings.orchestrator_url or not transcription.segments:
            return
        base = self.settings.orchestrator_url.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                agents_response = await client.post(
                    f"{base}/rpc",
                    json={"jsonrpc": "2.0", "method": "list_agents", "params": {}, "id": 1},
                )
                agents_response.raise_for_status()
                agents = agents_response.json().get("result", [])
                if not any(agent.get("name") == "meeting_summarizer" for agent in agents):
                    return
                text = "\n".join(
                    f"[{s['start_ms']}ms-{s['end_ms']}ms] {s['speaker_label']}: {s['text']}"
                    for s in transcription.segments
                )
                await client.post(
                    f"{base}/rpc",
                    json={
                        "jsonrpc": "2.0",
                        "method": "invoke",
                        "params": {
                            "agent": "meeting_summarizer",
                            "message": f"Обработай транскрипт встречи {meeting_id}:\n{text}",
                            "session_id": str(meeting_id),
                        },
                        "id": 2,
                    },
                )
        except Exception:
            logger.exception("Failed to notify meeting_summarizer for %s", meeting_id)

    def _artifact_expiration(self) -> datetime | None:
        if self.settings.audio_ttl_days <= 0:
            return None
        return utcnow() + timedelta(days=self.settings.audio_ttl_days)

    @staticmethod
    def _delay_seconds(starts_at: datetime | None) -> float:
        if starts_at is None:
            return 0
        value = starts_at
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max((value - utcnow()).total_seconds(), 0)


__all__ = ["MeetingDispatcher"]
