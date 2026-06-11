from __future__ import annotations

import pytest
from core.cron_schedule import cron_to_schedule, describe_cron, schedule_to_cron


def test_schedule_to_cron_daily():
    assert schedule_to_cron({"preset": "daily", "time": "09:30"}) == "30 9 * * *"


def test_schedule_to_cron_weekdays():
    assert schedule_to_cron({"preset": "weekdays", "time": "08:00"}) == "0 8 * * 1-5"


def test_schedule_to_cron_weekly_converts_sunday():
    # ISO Mon=1, Sun=7 -> cron Sun=0
    assert schedule_to_cron({"preset": "weekly", "time": "10:00", "days": [1, 7]}) == "0 10 * * 0,1"


def test_schedule_to_cron_rejects_bad_input():
    with pytest.raises(ValueError):
        schedule_to_cron({"preset": "weekly", "time": "10:00", "days": []})
    with pytest.raises(ValueError):
        schedule_to_cron({"preset": "daily", "time": "99:00"})
    with pytest.raises(ValueError):
        schedule_to_cron({"preset": "custom"})


def test_cron_to_schedule_roundtrip():
    assert cron_to_schedule("30 9 * * *") == {"preset": "daily", "time": "09:30"}
    assert cron_to_schedule("0 8 * * 1-5") == {"preset": "weekdays", "time": "08:00"}
    assert cron_to_schedule("0 10 * * 0,1") == {
        "preset": "weekly",
        "time": "10:00",
        "days": [1, 7],
    }


def test_cron_to_schedule_custom():
    assert cron_to_schedule("*/15 * * * *") == {"preset": "custom"}
    assert cron_to_schedule("0 9 1 * *") == {"preset": "custom"}
    assert cron_to_schedule("not a cron") == {"preset": "custom"}


def test_describe_cron():
    assert describe_cron("30 9 * * *") == "ежедневно в 09:30"
    assert describe_cron("0 8 * * 1-5") == "по будням в 08:00"
    assert describe_cron("0 10 * * 0,1") == "Пн Вс в 10:00"
    assert describe_cron("*/15 * * * *").startswith("по расписанию")
