"""Tests for fuzzy assignee resolution (no API)."""

from core.assignee_resolver import (
    TrackerUser,
    best_user_match,
    extract_assignee_mention,
    resolve_first_person,
)


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


def test_extract_not_novaya_from_nuzhna_novaya_zadacha():
    """Regression: «нужна новая задача» must not yield assignee «новая»."""
    assert extract_assignee_mention("Нам нужна новая задача — разобраться") is None


def test_extract_from_chat_transcript_naznachim_kolyu():
    msg = (
        "Рома: Нам нужна новая задача.\n"
        "Артем: Ответственным назначим Колю?\n"
        "Коля: Ок, сделаю.\n"
        "Рома: Задача: Коля готовит инструкцию до пятницы."
    )
    assert extract_assignee_mention(msg) == "Колю"


def test_extract_zadacha_kolya_line():
    assert extract_assignee_mention("Задача: Коля готовит инструкцию") == "Коля"


def test_extract_chat_status_prefix():
    assert extract_assignee_mention("Коля: добавил meeting_summarizer") == "Коля"


def test_resolve_first_person_mne():
    assert resolve_first_person("назначь мне задачу", tracker_login="nukolaus") == "nukolaus"


def test_resolve_first_person_ya():
    assert resolve_first_person("я сделаю это", tracker_login="nukolaus") == "nukolaus"


def test_resolve_first_person_moi():
    assert resolve_first_person("мои задачи", tracker_login="nukolaus") == "nukolaus"


def test_resolve_first_person_moya():
    assert resolve_first_person("моя задача", tracker_login="nukolaus") == "nukolaus"


def test_resolve_first_person_mnoy():
    assert resolve_first_person("за мной", tracker_login="nukolaus") == "nukolaus"


def test_resolve_first_person_ko_mne():
    assert resolve_first_person("ко мне обращаются", tracker_login="nukolaus") == "nukolaus"


def test_resolve_first_person_u_menya():
    assert resolve_first_person("у меня проблемы", tracker_login="nukolaus") == "nukolaus"


def test_resolve_first_person_no_first_person():
    assert resolve_first_person("создай задачу Коле", tracker_login="nukolaus") is None


def test_resolve_first_person_no_login():
    assert resolve_first_person("назначь мне", tracker_login=None) is None


def test_resolve_first_person_empty_login():
    assert resolve_first_person("назначь мне", tracker_login="") is None


def test_resolve_first_person_empty_message():
    assert resolve_first_person("", tracker_login="nukolaus") is None


def test_resolve_first_person_case_insensitive():
    assert resolve_first_person("МНЕ нужна задача", tracker_login="nukolaus") == "nukolaus"


def test_resolve_first_person_embedded_in_sentence():
    result = resolve_first_person(
        "пожалуйста, назначь мне задачу по CI",
        tracker_login="nukolaus",
    )
    assert result == "nukolaus"


def test_resolve_first_person_third_person_unaffected():
    assert resolve_first_person("создай задачу Коле", tracker_login="nukolaus") is None
