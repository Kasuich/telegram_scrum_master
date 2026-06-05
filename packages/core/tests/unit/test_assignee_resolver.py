"""Tests for fuzzy assignee resolution (no API)."""

from core.assignee_resolver import TrackerUser, best_user_match, extract_assignee_mention


def _team() -> list[TrackerUser]:
    return [
        TrackerUser(
            login="nukolaus",
            display="Николай Ус",
            first_name="Nikolai",
            last_name="Us",
        ),
        TrackerUser(
            login="shinkarenkorom",
            display="Roman Shinkarenko",
            first_name="Roman",
            last_name="Shinkarenko",
        ),
        TrackerUser(
            login="geroi.serg",
            display="Сергей Героев",
            first_name="Sergey",
            last_name="Geroev",
        ),
    ]


def test_match_roma_cyrillic():
    m = best_user_match("Рома", _team())
    assert m is not None
    assert m.login == "shinkarenkorom"


def test_match_roma_login():
    m = best_user_match("shinkarenkorom", _team())
    assert m is not None
    assert m.login == "shinkarenkorom"


def test_match_nikolai_display():
    m = best_user_match("Николай", _team())
    assert m is not None
    assert m.login == "nukolaus"


def test_extract_mention_na_kolyu():
    assert extract_assignee_mention("Создай задачу на Колю MCP") in ("Колю", "Колю")


def test_extract_mention_dlya_romy():
    m = extract_assignee_mention("Заведи для Ромы задачу CI")
    assert m is not None
