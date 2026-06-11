from __future__ import annotations

from platform_api.telegram_import import (
    ImportStats,
    _build_media_json,
    _extract_text,
    _message_kind_from_type,
    _parse_timestamp,
    parse_export_messages,
)


class TestParseTimestamp:
    def test_iso_format(self) -> None:
        dt = _parse_timestamp("2023-06-15T10:30:00")
        assert dt is not None
        assert dt.year == 2023
        assert dt.month == 6
        assert dt.day == 15

    def test_iso_with_z(self) -> None:
        dt = _parse_timestamp("2023-06-15T10:30:00Z")
        assert dt is not None
        assert dt.tzinfo is not None

    def test_unixtime(self) -> None:
        dt = _parse_timestamp("1673776200")
        assert dt is not None
        assert dt.year == 2023
        assert dt.month == 1
        assert dt.day == 15

    def test_none(self) -> None:
        assert _parse_timestamp(None) is None

    def test_invalid(self) -> None:
        assert _parse_timestamp("not-a-date") is None


class TestExtractText:
    def test_string(self) -> None:
        assert _extract_text("Hello") == "Hello"

    def test_string_truncate(self) -> None:
        long_text = "x" * 5000
        result = _extract_text(long_text)
        assert len(result) == 4096

    def test_entity_list(self) -> None:
        text = [
            {"type": "plain", "text": "Hello "},
            {"type": "plain", "text": "World"},
        ]
        assert _extract_text(text) == "Hello World"

    def test_entity_with_non_plain(self) -> None:
        text = [
            {"type": "bold", "text": "Bold"},
            {"type": "plain", "text": " normal"},
        ]
        assert _extract_text(text) == " normal"

    def test_none(self) -> None:
        assert _extract_text(None) is None


class TestMessageKindFromType:
    def test_regular_message(self) -> None:
        assert _message_kind_from_type("message", None) == "text"

    def test_photo(self) -> None:
        assert _message_kind_from_type("message", "photo") == "photo"

    def test_video(self) -> None:
        assert _message_kind_from_type("video", None) == "video"

    def test_service(self) -> None:
        assert _message_kind_from_type("service", None) == "service"

    def test_migration(self) -> None:
        assert _message_kind_from_type("migration", None) == "service"


class TestBuildMediaJson:
    def test_photo(self) -> None:
        msg = {
            "photo": {"id": "abc123", "access_hash": "xyz789"},
            "width": 800,
            "height": 600,
        }
        result = _build_media_json(msg)
        assert result is not None
        assert result["media_type"] == "photo"
        assert result["file_id"] == "abc123"
        assert result["width"] == 800
        assert result["height"] == 600

    def test_no_media(self) -> None:
        msg = {"text": "no media"}
        assert _build_media_json(msg) is None

    def test_video_with_duration(self) -> None:
        msg = {
            "file": {"id": "vid123"},
            "duration_seconds": 120,
            "mime_type": "video/mp4",
        }
        result = _build_media_json(msg)
        assert result is not None
        assert result["media_type"] == "file"
        assert result["duration"] == 120


class TestParseExportMessages:
    def test_parses_regular_message(self) -> None:
        chat_data = {
            "id": -100123,
            "type": "private_channel",
            "messages": [
                {
                    "id": 42,
                    "type": "message",
                    "date": "2023-06-15T10:30:00",
                    "from": "Alice",
                    "from_id": "user999",
                    "text": "Hello from export",
                }
            ],
        }

        messages = []
        for msg in parse_export_messages(chat_data):
            messages.append(msg)

        assert len(messages) == 1
        assert messages[0].external_message_id == "42"
        assert messages[0].external_chat_id == "-100123"
        assert messages[0].text == "Hello from export"
        assert messages[0].message_kind == "text"
        assert messages[0].actor_external_id == "user999"
        assert messages[0].actor_name == "Alice"

    def test_skips_unsupported_types(self) -> None:
        chat_data = {
            "id": -100123,
            "messages": [
                {"id": 1, "type": "unknown_type", "text": "skip"},
                {"id": 2, "type": "message", "text": "keep"},
            ],
        }

        messages = []
        for msg in parse_export_messages(chat_data):
            messages.append(msg)

        assert len(messages) == 1
        assert messages[0].external_message_id == "2"

    def test_parses_service_message(self) -> None:
        chat_data = {
            "id": -100123,
            "messages": [
                {
                    "id": 1,
                    "type": "service",
                    "date": "2023-06-15T10:30:00",
                    "text": "Alice created group",
                }
            ],
        }

        messages = []
        for msg in parse_export_messages(chat_data):
            messages.append(msg)

        assert len(messages) == 1
        assert messages[0].message_kind == "service"
        assert messages[0].actor_external_id is None

    def test_parses_photo_with_caption(self) -> None:
        chat_data = {
            "id": -100123,
            "messages": [
                {
                    "id": 1,
                    "type": "photo",
                    "date": "2023-06-15T10:30:00",
                    "from": "Bob",
                    "from_id": "user888",
                    "text": "Check this out",
                    "media_type": "photo",
                    "photo": {"id": "photo123"},
                }
            ],
        }

        messages = []
        for msg in parse_export_messages(chat_data):
            messages.append(msg)

        assert len(messages) == 1
        assert messages[0].message_kind == "photo"
        assert messages[0].caption == "Check this out"
        assert messages[0].text is None
        assert messages[0].media_json is not None
        assert messages[0].media_json["file_id"] == "photo123"

    def test_handles_reply(self) -> None:
        chat_data = {
            "id": -100123,
            "messages": [
                {
                    "id": 2,
                    "type": "message",
                    "date": "2023-06-15T10:30:00",
                    "from": "Alice",
                    "from_id": "user999",
                    "text": "Reply text",
                    "reply_to_message_id": 1,
                }
            ],
        }

        messages = []
        for msg in parse_export_messages(chat_data):
            messages.append(msg)

        assert messages[0].reply_to_message_id == "1"


class TestImportStats:
    def test_defaults(self) -> None:
        stats = ImportStats()
        assert stats.created == 0
        assert stats.skipped == 0
        assert stats.failed == 0
        assert stats.errors == []

    def test_accumulate(self) -> None:
        stats = ImportStats()
        stats.created = 10
        stats.skipped = 5
        stats.failed = 2
        stats.errors.append("Error 1")

        assert stats.created == 10
        assert stats.skipped == 5
        assert len(stats.errors) == 1
