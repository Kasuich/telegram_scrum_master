"""Human-friendly cron schedules for the teamlead UI.

Lets the UI present and edit recurring jobs as a small structured schedule
(preset + time + weekdays) instead of raw ``* * * * *`` cron strings, while
still storing a standard 5-field cron expression in ``ScheduledJob.cron_expr``.
"""

from __future__ import annotations

from typing import Any

# ISO weekday (1=Mon .. 7=Sun) -> short Russian label.
_ISO_LABELS = {1: "Пн", 2: "Вт", 3: "Ср", 4: "Чт", 5: "Пт", 6: "Сб", 7: "Вс"}


def _iso_to_cron_dow(iso: int) -> int:
    """ISO weekday (1=Mon..7=Sun) -> cron weekday (0=Sun..6=Sat)."""
    return 0 if iso == 7 else iso


def _cron_to_iso_dow(cron: int) -> int:
    """cron weekday (0=Sun..6=Sat, 7=Sun) -> ISO weekday (1=Mon..7=Sun)."""
    if cron in (0, 7):
        return 7
    return cron


def _parse_time(value: str) -> tuple[int, int]:
    hours_str, _, minutes_str = value.partition(":")
    hours, minutes = int(hours_str), int(minutes_str)
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise ValueError(f"invalid time: {value!r}")
    return hours, minutes


def schedule_to_cron(schedule: dict[str, Any]) -> str:
    """Build a 5-field cron expression from a structured schedule.

    schedule = {"preset": "daily"|"weekdays"|"weekly", "time": "HH:MM",
                "days": [1..7]}  (days required only for "weekly")
    """
    preset = schedule.get("preset")
    hours, minutes = _parse_time(str(schedule.get("time", "09:00")))

    if preset == "daily":
        return f"{minutes} {hours} * * *"
    if preset == "weekdays":
        return f"{minutes} {hours} * * 1-5"
    if preset == "weekly":
        days = schedule.get("days") or []
        iso_days = {int(d) for d in days}
        if not iso_days or any(d < 1 or d > 7 for d in iso_days):
            raise ValueError("weekly schedule requires days in 1..7")
        cron_days = ",".join(str(c) for c in sorted(_iso_to_cron_dow(d) for d in iso_days))
        return f"{minutes} {hours} * * {cron_days}"
    raise ValueError(f"unsupported preset: {preset!r}")


def cron_to_schedule(cron_expr: str) -> dict[str, Any]:
    """Parse a cron string into a structured schedule.

    Returns ``{"preset": "custom"}`` for anything that does not map cleanly to
    a daily/weekdays/weekly time-of-day schedule.
    """
    parts = cron_expr.split()
    if len(parts) != 5:
        return {"preset": "custom"}
    minute, hour, dom, month, dow = parts

    if not (minute.isdigit() and hour.isdigit()):
        return {"preset": "custom"}
    if dom != "*" or month != "*":
        return {"preset": "custom"}
    time = f"{int(hour):02d}:{int(minute):02d}"

    if dow == "*":
        return {"preset": "daily", "time": time}
    if dow == "1-5":
        return {"preset": "weekdays", "time": time}
    if all(token.isdigit() for token in dow.split(",")):
        iso_days = sorted({_cron_to_iso_dow(int(token)) for token in dow.split(",")})
        return {"preset": "weekly", "time": time, "days": iso_days}
    return {"preset": "custom"}


def describe_cron(cron_expr: str) -> str:
    """Human-readable Russian description of a cron schedule."""
    schedule = cron_to_schedule(cron_expr)
    preset = schedule.get("preset")
    if preset == "daily":
        return f"ежедневно в {schedule['time']}"
    if preset == "weekdays":
        return f"по будням в {schedule['time']}"
    if preset == "weekly":
        labels = " ".join(_ISO_LABELS[d] for d in schedule["days"])
        return f"{labels} в {schedule['time']}"
    return f"по расписанию ({cron_expr})"
