from __future__ import annotations

from meeting_capture.transcription import (
    empty_transcription_user_message,
    map_speakers_to_names,
    parse_speechkit_segments,
)


def test_empty_transcription_user_message_for_missing_s3() -> None:
    message = empty_transcription_user_message("speechkit_missing_audio_uri")
    assert "Object Storage" in message
    assert "S3_" in message


def test_parse_speechkit_segments_from_nested_payload() -> None:
    payload = {
        "responses": [
            {
                "final": {
                    "channelTag": "SPEAKER_01",
                    "alternatives": [
                        {
                            "text": "привет команда",
                            "startTimeMs": "100",
                            "endTimeMs": "1200",
                        }
                    ],
                }
            },
            {
                "final": {
                    "alternatives": [
                        {
                            "text": "беру задачу",
                            "words": [
                                {"text": "беру", "start_time_ms": 1300, "speakerTag": "SPEAKER_02"},
                                {"text": "задачу", "end_time_ms": 2100, "speakerTag": "SPEAKER_02"},
                            ],
                        }
                    ],
                }
            },
        ]
    }

    assert parse_speechkit_segments(payload) == [
        {
            "start_ms": 100,
            "end_ms": 1200,
            "speaker_label": "SPEAKER_01",
            "text": "привет команда",
        },
        {
            "start_ms": 1300,
            "end_ms": 2100,
            "speaker_label": "SPEAKER_02",
            "text": "беру задачу",
        },
    ]


def test_parse_ignores_empty_final_alternatives() -> None:
    assert parse_speechkit_segments({"final": {"alternatives": [{"text": ""}]}}) == []


def test_map_speakers_assigns_names_by_overlap() -> None:
    segments = [
        {"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00", "text": "привет"},
        {"start_ms": 1000, "end_ms": 2000, "speaker_label": "SPEAKER_01", "text": "ага"},
    ]
    timeline = [
        {"start_ms": 0, "end_ms": 1000, "display_name": "Алиса"},
        {"start_ms": 1000, "end_ms": 2000, "display_name": "Боб"},
    ]
    result = map_speakers_to_names(segments, timeline)
    assert result[0]["speaker_name"] == "Алиса"
    assert result[1]["speaker_name"] == "Боб"
    # Original fields preserved.
    assert result[0]["text"] == "привет"
    assert result[0]["speaker_label"] == "SPEAKER_00"


def test_map_speakers_majority_wins_per_label() -> None:
    # Same label across two segments — the name with more total overlap wins.
    segments = [
        {"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00", "text": "a"},
        {"start_ms": 1000, "end_ms": 5000, "speaker_label": "SPEAKER_00", "text": "b"},
    ]
    timeline = [
        {"start_ms": 0, "end_ms": 1000, "display_name": "Алиса"},
        {"start_ms": 1000, "end_ms": 5000, "display_name": "Боб"},  # 4000ms > 1000ms
    ]
    result = map_speakers_to_names(segments, timeline)
    assert {seg["speaker_name"] for seg in result} == {"Боб"}


def test_map_speakers_empty_timeline_sets_none() -> None:
    segments = [{"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00", "text": "x"}]
    result = map_speakers_to_names(segments, [])
    assert result[0]["speaker_name"] is None
    assert result[0]["speaker_label"] == "SPEAKER_00"


def test_map_speakers_no_overlap_sets_none() -> None:
    segments = [{"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00", "text": "x"}]
    timeline = [{"start_ms": 5000, "end_ms": 6000, "display_name": "Алиса"}]
    result = map_speakers_to_names(segments, timeline)
    assert result[0]["speaker_name"] is None
