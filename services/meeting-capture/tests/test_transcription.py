from __future__ import annotations

from meeting_capture.transcription import (
    SPEAKER_SOURCE_DOM_SEGMENT,
    SPEAKER_SOURCE_UNKNOWN,
    compute_speaker_diagnostics,
    deduplicate_mirror_segments,
    diarization_collapsed,
    empty_transcription_user_message,
    map_speakers_to_names,
    merge_speaker_timeline_windows,
    parse_speechkit_segments,
)


def test_empty_transcription_user_message_for_s3_not_configured() -> None:
    message = empty_transcription_user_message("speechkit_s3_not_configured")
    assert "S3 не подключён" in message
    assert "force-recreate" in message


def test_empty_transcription_user_message_for_missing_s3_upload() -> None:
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


def test_map_speakers_assigns_names_per_segment() -> None:
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
    assert result[0]["speaker_source"] == SPEAKER_SOURCE_DOM_SEGMENT
    assert result[0]["speaker_confidence"] == 1.0


def test_map_speakers_per_segment_not_majority_per_label() -> None:
    segments = [
        {"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00", "text": "a"},
        {"start_ms": 1000, "end_ms": 5000, "speaker_label": "SPEAKER_00", "text": "b"},
    ]
    timeline = [
        {"start_ms": 0, "end_ms": 1000, "display_name": "Алиса"},
        {"start_ms": 1000, "end_ms": 5000, "display_name": "Боб"},
    ]
    result = map_speakers_to_names(segments, timeline)
    assert result[0]["speaker_name"] == "Алиса"
    assert result[1]["speaker_name"] == "Боб"


def test_map_speakers_empty_timeline_sets_unknown() -> None:
    segments = [{"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00", "text": "x"}]
    result = map_speakers_to_names(segments, [])
    assert result[0]["speaker_name"] is None
    assert result[0]["speaker_source"] == SPEAKER_SOURCE_UNKNOWN


def test_map_speakers_roster_fallback_disabled_when_timeline_empty() -> None:
    segments = [
        {"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_01", "text": "привет"},
        {"start_ms": 1000, "end_ms": 2000, "speaker_label": "SPEAKER_02", "text": "ага"},
    ]
    roster = [
        {"display_name": "Коля", "source": "telemost_ui"},
        {"display_name": "Рома", "source": "telemost_ui"},
    ]
    result = map_speakers_to_names(
        segments,
        [],
        participants_observed=roster,
        bot_display_name="PM Assistant (recording)",
    )
    assert all(seg["speaker_name"] is None for seg in result)


def test_deduplicate_mirror_segments_collapses_speaker_doubles() -> None:
    segments = [
        {"start_ms": 16850, "end_ms": 17350, "speaker_label": "SPEAKER_01", "text": "Привет"},
        {"start_ms": 16850, "end_ms": 17350, "speaker_label": "SPEAKER_02", "text": "Привет"},
        {
            "start_ms": 20450,
            "end_ms": 23130,
            "speaker_label": "SPEAKER_01",
            "text": "Мы сегодня планируем",
        },
        {
            "start_ms": 20450,
            "end_ms": 23130,
            "speaker_label": "SPEAKER_02",
            "text": "Мы сегодня планируем",
        },
    ]
    result = deduplicate_mirror_segments(segments)
    assert len(result) == 2
    assert {seg["text"] for seg in result} == {"Привет", "Мы сегодня планируем"}


def test_deduplicate_mirror_segments_ignores_end_ms_and_near_start() -> None:
    segments = [
        {"start_ms": 64000, "end_ms": 64200, "speaker_label": "SPEAKER_02", "text": "Ничего"},
        {"start_ms": 64000, "end_ms": 64800, "speaker_label": "SPEAKER_01", "text": "Ничего"},
        {
            "start_ms": 162000,
            "end_ms": 162500,
            "speaker_label": "SPEAKER_02",
            "text": "А что это много",
        },
        {
            "start_ms": 162400,
            "end_ms": 163000,
            "speaker_label": "SPEAKER_01",
            "text": "А что это много",
        },
    ]
    result = deduplicate_mirror_segments(segments)
    assert len(result) == 2
    assert [seg["text"] for seg in result] == ["Ничего", "А что это много"]


def test_deduplicate_mirror_segments_keeps_distinct_same_second_phrases() -> None:
    segments = [
        {"start_ms": 1000, "end_ms": 1500, "speaker_label": "SPEAKER_01", "text": "да"},
        {"start_ms": 1200, "end_ms": 1700, "speaker_label": "SPEAKER_02", "text": "нет"},
    ]
    result = deduplicate_mirror_segments(segments)
    assert len(result) == 2


def test_map_speakers_no_overlap_sets_unknown() -> None:
    segments = [{"start_ms": 0, "end_ms": 1000, "speaker_label": "SPEAKER_00", "text": "x"}]
    timeline = [{"start_ms": 5000, "end_ms": 6000, "display_name": "Алиса"}]
    result = map_speakers_to_names(segments, timeline)
    assert result[0]["speaker_name"] is None
    assert result[0]["speaker_source"] == SPEAKER_SOURCE_UNKNOWN


def test_map_speakers_weak_overlap_sets_unknown() -> None:
    segments = [{"start_ms": 10000, "end_ms": 15000, "speaker_label": "SPEAKER_00", "text": "x"}]
    timeline = [{"start_ms": 10000, "end_ms": 10100, "display_name": "Алиса"}]
    result = map_speakers_to_names(segments, timeline)
    assert result[0]["speaker_name"] is None


def test_deduplicate_mirror_segments_collapses_diverging_tail() -> None:
    segments = [
        {
            "start_ms": 9000,
            "end_ms": 9500,
            "speaker_label": "SPEAKER_01",
            "text": "9000",
        },
        {
            "start_ms": 9000,
            "end_ms": 9800,
            "speaker_label": "SPEAKER_02",
            "text": "9000 ха ха ха",
        },
    ]
    result = deduplicate_mirror_segments(segments)
    assert len(result) == 1
    assert result[0]["text"] == "9000 ха ха ха"


def test_merge_speaker_timeline_windows() -> None:
    raw = [
        {"start_ms": 0, "end_ms": 400, "display_name": "Алиса", "source": "goloom_grid"},
        {"start_ms": 500, "end_ms": 1000, "display_name": "Алиса", "source": "goloom_grid"},
        {"start_ms": 2000, "end_ms": 3000, "display_name": "Боб", "source": "panel"},
    ]
    merged = merge_speaker_timeline_windows(raw)
    assert len(merged) == 2
    assert merged[0]["end_ms"] == 1000
    assert merged[0]["samples"] == 2


def test_diarization_collapsed_detects_single_label_multi_party() -> None:
    segments = [
        {"start_ms": 0, "end_ms": 5000, "speaker_label": "SPEAKER_00", "text": "a"},
        {"start_ms": 5000, "end_ms": 6000, "speaker_label": "SPEAKER_00", "text": "b"},
    ]
    assert diarization_collapsed(segments, participants_count=3) is True


def test_compute_speaker_diagnostics() -> None:
    segments = [
        {
            "start_ms": 0,
            "end_ms": 1000,
            "speaker_label": "SPEAKER_00",
            "speaker_name": "Алиса",
            "speaker_source": SPEAKER_SOURCE_DOM_SEGMENT,
            "text": "hi",
        },
        {
            "start_ms": 1000,
            "end_ms": 2000,
            "speaker_label": "SPEAKER_00",
            "speaker_name": None,
            "speaker_source": SPEAKER_SOURCE_UNKNOWN,
            "text": "there",
        },
    ]
    diag = compute_speaker_diagnostics(
        segments,
        [{"start_ms": 0, "end_ms": 1000, "display_name": "Алиса"}],
        participants_observed=[{"display_name": "Алиса"}, {"display_name": "Боб"}],
    )
    assert diag["participants_observed_count"] == 2
    assert diag["speechkit_unique_labels"] == 1
    assert diag["diarization_quality"] == "collapsed"
    assert diag["segments_by_source"][SPEAKER_SOURCE_DOM_SEGMENT] == 1
    assert diag["segments_by_source"][SPEAKER_SOURCE_UNKNOWN] == 1
