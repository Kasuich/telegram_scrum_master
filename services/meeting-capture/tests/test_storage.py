from __future__ import annotations

import pytest
from meeting_capture.config import CaptureSettings
from meeting_capture.storage import LocalObjectStore, artifact_key


def test_artifact_key_is_namespaced_by_meeting() -> None:
    assert artifact_key("m1", "recording.webm") == "meetings/m1/recording.webm"


def test_artifact_key_sanitizes_slashes() -> None:
    assert artifact_key("m1", "../audio.ogg") == "meetings/m1/.._audio.ogg"


@pytest.mark.asyncio
async def test_local_object_store_upload_bytes(tmp_path) -> None:
    settings = CaptureSettings(CAPTURE_OBJECT_STORAGE_DIR=tmp_path / "objects")
    store = LocalObjectStore(settings.object_storage_dir)
    obj = await store.upload_bytes(
        b"hello",
        key=artifact_key("m1", "transcript.txt"),
        content_type="text/plain; charset=utf-8",
    )
    assert obj.size_bytes == 5
    assert (settings.object_storage_dir / obj.key).read_bytes() == b"hello"
