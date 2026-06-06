from __future__ import annotations

from meeting_capture.storage import artifact_key


def test_artifact_key_is_namespaced_by_meeting() -> None:
    assert artifact_key("m1", "recording.webm") == "meetings/m1/recording.webm"


def test_artifact_key_sanitizes_slashes() -> None:
    assert artifact_key("m1", "../audio.ogg") == "meetings/m1/.._audio.ogg"
