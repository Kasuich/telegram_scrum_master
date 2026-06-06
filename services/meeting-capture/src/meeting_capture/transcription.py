"""SpeechKit transcription and speaker-label parsing."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from meeting_capture.config import CaptureSettings


@dataclass(frozen=True)
class TranscriptionResult:
    source: str
    segments: list[dict[str, Any]]
    participants_observed: list[dict[str, Any]] = field(default_factory=list)


class Transcriber:
    async def transcribe(
        self,
        audio_path: Path,
        *,
        audio_uri: str | None,
        language: str,
        participants_observed: list[dict[str, Any]],
    ) -> TranscriptionResult:
        raise NotImplementedError


class SpeechKitTranscriber(Transcriber):
    """Yandex SpeechKit v3 async file transcription."""

    def __init__(self, settings: CaptureSettings) -> None:
        self.settings = settings

    async def transcribe(
        self,
        audio_path: Path,
        *,
        audio_uri: str | None,
        language: str,
        participants_observed: list[dict[str, Any]],
    ) -> TranscriptionResult:
        del audio_path
        if not self.settings.speechkit_api_key:
            return TranscriptionResult(
                source="speechkit_unconfigured",
                segments=[],
                participants_observed=participants_observed,
            )
        if not audio_uri:
            return TranscriptionResult(
                source="speechkit_missing_audio_uri",
                segments=[],
                participants_observed=participants_observed,
            )

        async with httpx.AsyncClient(timeout=30) as client:
            operation_id = await self._start_operation(
                client,
                audio_uri=audio_uri,
                language=language,
            )
            payload = await self._poll_result(client, operation_id)

        return TranscriptionResult(
            source="speechkit",
            segments=parse_speechkit_segments(payload),
            participants_observed=participants_observed,
        )

    async def _start_operation(
        self,
        client: httpx.AsyncClient,
        *,
        audio_uri: str,
        language: str,
    ) -> str:
        url = f"{self.settings.speechkit_base_url.rstrip('/')}/stt/v3/recognizeFileAsync"
        body = {
            "uri": audio_uri,
            "recognitionModel": {
                "model": "general",
                "audioFormat": {"containerAudio": {"containerAudioType": "OGG_OPUS"}},
                "languageRestriction": {
                    "restrictionType": "WHITELIST",
                    "languageCode": [language],
                },
            },
            "speechAnalysis": {"enableSpeakerAnalysis": True},
            "speakerLabeling": {"speakerLabeling": "SPEAKER_LABELING_ENABLED"},
        }
        response = await client.post(
            url,
            json=body,
            headers={"Authorization": f"Api-Key {self.settings.speechkit_api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        operation_id = (
            payload.get("id") or payload.get("operation_id") or payload.get("operationId")
        )
        if not operation_id:
            raise RuntimeError("SpeechKit did not return an operation id")
        return str(operation_id)

    async def _poll_result(self, client: httpx.AsyncClient, operation_id: str) -> Any:
        deadline = time.monotonic() + self.settings.speechkit_timeout_sec
        url = f"{self.settings.speechkit_base_url.rstrip('/')}/stt/v3/getRecognition"
        while time.monotonic() < deadline:
            response = await client.get(
                url,
                params={"operationId": operation_id},
                headers={"Authorization": f"Api-Key {self.settings.speechkit_api_key}"},
            )
            if response.status_code in (202, 404, 409):
                await asyncio.sleep(self.settings.speechkit_poll_interval_sec)
                continue
            response.raise_for_status()
            payload = response.json()
            if payload.get("done") is False:
                await asyncio.sleep(self.settings.speechkit_poll_interval_sec)
                continue
            return payload
        raise TimeoutError("SpeechKit transcription timed out")


def parse_speechkit_segments(payload: Any) -> list[dict[str, Any]]:
    """Normalize SpeechKit response events into transcript segments."""

    segments: list[dict[str, Any]] = []
    for event in _iter_dicts(payload):
        final = event.get("final")
        if not isinstance(final, dict):
            continue
        channel_or_speaker = _first_value(
            final,
            "speaker_tag",
            "speakerTag",
            "channel_tag",
            "channelTag",
        )
        alternatives = final.get("alternatives") or []
        if not isinstance(alternatives, list):
            continue
        for alternative in alternatives:
            if not isinstance(alternative, dict):
                continue
            text = str(alternative.get("text") or "").strip()
            if not text:
                continue
            words = alternative.get("words")
            speaker = (
                _first_value(alternative, "speaker_tag", "speakerTag", "channel_tag", "channelTag")
                or _speaker_from_words(words)
                or channel_or_speaker
                or "SPEAKER_00"
            )
            start_ms = _int_value(alternative, "start_time_ms", "startTimeMs")
            end_ms = _int_value(alternative, "end_time_ms", "endTimeMs")
            if start_ms is None and isinstance(words, list) and words:
                start_ms = _int_value(words[0], "start_time_ms", "startTimeMs")
            if end_ms is None and isinstance(words, list) and words:
                end_ms = _int_value(words[-1], "end_time_ms", "endTimeMs")
            segments.append(
                {
                    "start_ms": max(start_ms or 0, 0),
                    "end_ms": max(end_ms or start_ms or 0, 0),
                    "speaker_label": str(speaker),
                    "text": text,
                }
            )
    return sorted(segments, key=lambda item: (item["start_ms"], item["end_ms"]))


def _iter_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _int_value(mapping: dict[str, Any], *keys: str) -> int | None:
    value = _first_value(mapping, *keys)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _speaker_from_words(words: Any) -> Any:
    if not isinstance(words, list):
        return None
    for word in words:
        if isinstance(word, dict):
            value = _first_value(word, "speaker_tag", "speakerTag", "channel_tag", "channelTag")
            if value:
                return value
    return None


__all__ = [
    "SpeechKitTranscriber",
    "Transcriber",
    "TranscriptionResult",
    "parse_speechkit_segments",
]
