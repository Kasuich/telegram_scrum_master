from __future__ import annotations

from meeting_capture.transcription import parse_speechkit_segments


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
