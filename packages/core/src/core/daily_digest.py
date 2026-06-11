"""Daily Tracker digest delivery through Telegram outbox."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from core.config import get_config
from core.models import (
    ScheduledJob,
    Team,
    TelegramChat,
    TelegramInstallation,
    TelegramOutbox,
    TelegramStandupPoll,
)
from core.standup_poll import (
    STANDUP_POLL_JOB_NAME,
    STANDUP_POLL_PAYLOAD_TYPE,
    load_registered_participants,
)
from core.tracker import TrackerClient

logger = logging.getLogger(__name__)

DAILY_DIGEST_JOB_NAME = "team_hourly_digest"
LEGACY_DAILY_DIGEST_JOB_NAMES = ("team_daily_digest_msk_1800",)
DAILY_DIGEST_PAYLOAD_TYPE = "team_daily_digest"
TELEGRAM_TEXT_LIMIT = 3800


@dataclass(frozen=True)
class DigestIssue:
    key: str
    summary: str
    status: str
    assignee_login: str
    assignee_display: str
    url: str


@dataclass(frozen=True)
class DigestMember:
    login: str
    display: str
    in_progress: list[DigestIssue]
    done_today: list[DigestIssue]
    standup_response: str = ""
    applied_items: list[str] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class DigestReport:
    team_id: uuid.UUID
    queue: str
    local_date: str
    local_hour: str
    timezone: str
    members: list[DigestMember]


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown daily digest timezone %r, falling back to Europe/Moscow", name)
        return ZoneInfo("Europe/Moscow")


def day_window_utc(
    now: datetime | None = None,
    *,
    timezone_name: str = "Europe/Moscow",
) -> tuple[str, datetime, datetime]:
    """Return local date and its UTC [start, end) window."""

    tz = _timezone(timezone_name)
    current = now or datetime.now(tz=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local_date = current.astimezone(tz).date()
    start_local = datetime.combine(local_date, time.min, tzinfo=tz)
    end_local = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=tz)
    return (
        local_date.isoformat(),
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def local_hour_key(
    now: datetime | None = None,
    *,
    timezone_name: str = "Europe/Moscow",
) -> str:
    tz = _timezone(timezone_name)
    current = now or datetime.now(tz=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(tz).strftime("%Y-%m-%dT%H")


def _quote_yql(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _next_iso_date(value: str) -> str:
    return (datetime.fromisoformat(value).date() + timedelta(days=1)).isoformat()


def build_done_today_yql(queue: str, local_date: str) -> str:
    next_date = _next_iso_date(local_date)
    return (
        f"Queue: {_quote_yql(queue)} "
        f"AND Resolution: !empty() "
        f"AND Updated: >= {_quote_yql(local_date)} "
        f"AND Updated: < {_quote_yql(next_date)}"
    )


def build_in_progress_yql(queue: str, status: str) -> str:
    return f"Queue: {_quote_yql(queue)} AND Status: {_quote_yql(status)}"


def _assignee_login(issue: dict[str, Any]) -> str:
    assignee = issue.get("assignee")
    if isinstance(assignee, dict):
        return str(assignee.get("login") or assignee.get("id") or "").strip()
    return ""


def _assignee_display(issue: dict[str, Any]) -> str:
    assignee = issue.get("assignee")
    if isinstance(assignee, dict):
        return str(
            assignee.get("display")
            or assignee.get("name")
            or assignee.get("login")
            or assignee.get("id")
            or ""
        ).strip()
    return ""


def _status_display(issue: dict[str, Any]) -> str:
    status = issue.get("status")
    if isinstance(status, dict):
        return str(status.get("display") or status.get("key") or "").strip()
    return str(status or "").strip()


def _to_digest_issue(issue: dict[str, Any]) -> DigestIssue | None:
    key = str(issue.get("key") or "").strip()
    if not key:
        return None
    return DigestIssue(
        key=key,
        summary=str(issue.get("summary") or "").strip(),
        status=_status_display(issue),
        assignee_login=_assignee_login(issue),
        assignee_display=_assignee_display(issue),
        url=f"https://tracker.yandex.ru/{key}",
    )


def _dedupe_issues(issues: list[DigestIssue]) -> list[DigestIssue]:
    seen: set[str] = set()
    out: list[DigestIssue] = []
    for issue in issues:
        if issue.key in seen:
            continue
        seen.add(issue.key)
        out.append(issue)
    return out


def _group_by_assignee(issues: list[DigestIssue]) -> dict[str, list[DigestIssue]]:
    grouped: dict[str, list[DigestIssue]] = {}
    for issue in issues:
        if not issue.assignee_login:
            continue
        grouped.setdefault(issue.assignee_login.casefold(), []).append(issue)
    return grouped


def _poll_issue_map(poll: TelegramStandupPoll) -> dict[str, dict[str, Any]]:
    mapped: dict[str, dict[str, Any]] = {}
    for item in poll.issues_json or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key:
            mapped[key] = item
    return mapped


def _poll_result_rows(poll: TelegramStandupPoll) -> list[dict[str, Any]]:
    applied = poll.applied_json if isinstance(poll.applied_json, dict) else {}
    rows = applied.get("results")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]

    history = applied.get("responses")
    if not isinstance(history, list):
        return []

    result_rows: list[dict[str, Any]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        results = item.get("results")
        if isinstance(results, list):
            result_rows.extend(row for row in results if isinstance(row, dict))
    return result_rows


def _poll_event_rows(poll: TelegramStandupPoll) -> list[dict[str, Any]]:
    applied = poll.applied_json if isinstance(poll.applied_json, dict) else {}
    rows = applied.get("events")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]

    events: list[dict[str, Any]] = []
    for row in _poll_result_rows(poll):
        if not isinstance(row, dict):
            continue
        event = dict(row)
        if not event.get("ok") and not event.get("commented"):
            event["kind"] = "not_applied"
        events.append(event)
    return events


def _event_key(event: dict[str, Any]) -> str:
    issue_key = str(event.get("issue_key") or "").strip()
    if issue_key:
        return issue_key
    if event.get("kind") == "create":
        return str(event.get("summary") or "новая задача").strip()
    issue_number = event.get("issue_number")
    if issue_number is not None:
        return f"задача {issue_number}"
    return "сообщение"


def _event_result_text(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or "")
    ok = bool(event.get("ok"))
    transitioned = event.get("transitioned")
    commented = bool(event.get("commented"))
    if kind == "create" and ok:
        return "создана задача"
    if kind == "close" and ok:
        return "закрыто"
    if kind == "cancel" and ok and transitioned:
        return "отменено"
    if kind == "in_progress" and ok:
        return "переведено в работу"
    if kind == "blocked" and ok and transitioned:
        return "отмечен блокер"
    if commented:
        return "добавлен комментарий, статус не изменен"
    if event.get("error") == "unknown_issue_number":
        return "номер задачи не найден"
    if event.get("error") == "ambiguous":
        return "не удалось понять задачу"
    error = str(event.get("error") or "").strip()
    return f"не применено: {error}" if error else "не применено"


def _event_line(event: dict[str, Any]) -> str:
    key = _event_key(event)
    text = str(event.get("text") or event.get("summary") or "").strip()
    result = _event_result_text(event)
    if text:
        return f"- {key}: {result}. Текст: {text}"
    return f"- {key}: {result}"


def _event_section(event: dict[str, Any]) -> str:
    kind = str(event.get("kind") or "")
    if kind == "create" and event.get("ok"):
        return "Создано"
    if kind == "comment" and event.get("ok"):
        return "Комментарии"
    if kind == "blocked" or event.get("commented") and not event.get("transitioned", True):
        return "Стопперы"
    if kind in {"close", "cancel", "in_progress"} and event.get("ok"):
        return "Статусы"
    return "Не применено"


def _poll_event_sections(poll: TelegramStandupPoll) -> dict[str, list[str]]:
    ordered = ["Статусы", "Создано", "Комментарии", "Стопперы", "Не применено"]
    sections: dict[str, list[str]] = {name: [] for name in ordered}
    for event in _poll_event_rows(poll):
        section = _event_section(event)
        sections.setdefault(section, []).append(_event_line(event))
    return {name: lines for name, lines in sections.items() if lines}


def _poll_response_text(poll: TelegramStandupPoll) -> str:
    applied = poll.applied_json if isinstance(poll.applied_json, dict) else {}
    history = applied.get("responses")
    if not isinstance(history, list):
        return str(poll.response_text or "")

    texts: list[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            texts.append(text)
    if texts:
        return "\n\n".join(texts)
    return str(poll.response_text or "")


def _poll_item_to_digest_issue(
    item: dict[str, Any],
    *,
    assignee_login: str,
    assignee_display: str,
    status: str = "",
) -> DigestIssue | None:
    key = str(item.get("key") or "").strip()
    if not key:
        return None
    return DigestIssue(
        key=key,
        summary=str(item.get("summary") or "").strip(),
        status=status or str(item.get("status") or "").strip(),
        assignee_login=assignee_login,
        assignee_display=assignee_display,
        url=str(item.get("url") or f"https://tracker.yandex.ru/{key}"),
    )


def _poll_row_to_digest_issue(
    row: dict[str, Any],
    issue_map: dict[str, dict[str, Any]],
    *,
    poll: TelegramStandupPoll,
    display: str,
    status: str,
) -> DigestIssue | None:
    key = str(row.get("issue_key") or "").strip()
    if not key and row.get("kind") == "create":
        key = "new-task"
    item = issue_map.get(key, {"key": key, "summary": row.get("summary") or ""})
    return _poll_item_to_digest_issue(
        item,
        assignee_login=poll.tracker_login,
        assignee_display=display,
        status=status,
    )


def _is_terminal_status(status: str) -> bool:
    normalized = status.casefold()
    terminal_tokens = (
        "закры",
        "отмен",
        "closed",
        "cancel",
        "resolved",
        "решен",
        "решён",
    )
    return any(token in normalized for token in terminal_tokens)


def _poll_done_issues(poll: TelegramStandupPoll, *, display: str) -> list[DigestIssue]:
    issue_map = _poll_issue_map(poll)
    issues: list[DigestIssue] = []
    for row in _poll_result_rows(poll):
        if not row.get("ok"):
            continue
        if row.get("kind") not in {"close", "cancel"}:
            continue
        if row.get("kind") == "cancel" and not row.get("transitioned"):
            continue
        status = "Отменена" if row.get("kind") == "cancel" else "Закрыта"
        issue = _poll_row_to_digest_issue(
            row,
            issue_map,
            poll=poll,
            display=display,
            status=status,
        )
        if issue is not None:
            issues.append(issue)
    return issues


def _poll_work_issues(
    poll: TelegramStandupPoll,
    *,
    display: str,
    done_keys: set[str],
) -> list[DigestIssue]:
    issue_map = _poll_issue_map(poll)
    issues: list[DigestIssue] = []
    work_kinds = {"blocked", "comment", "create", "in_progress"}
    status_by_kind = {
        "blocked": "Задерживается",
        "comment": "Обновлено",
        "create": "Создана",
        "in_progress": "В работе",
    }
    for row in _poll_result_rows(poll):
        if not row.get("ok"):
            continue
        kind = str(row.get("kind") or "")
        if kind not in work_kinds:
            continue
        key = str(row.get("issue_key") or "").strip()
        if key and key in done_keys:
            continue
        issue = _poll_row_to_digest_issue(
            row,
            issue_map,
            poll=poll,
            display=display,
            status=status_by_kind.get(kind, "Обновлено"),
        )
        if issue is not None:
            issues.append(issue)
    return issues


def _merge_issue_lists(*groups: list[DigestIssue]) -> list[DigestIssue]:
    seen: set[str] = set()
    merged: list[DigestIssue] = []
    for group in groups:
        for issue in group:
            if issue.key in seen:
                continue
            seen.add(issue.key)
            merged.append(issue)
    return merged


def _poll_applied_items(poll: TelegramStandupPoll) -> list[str]:
    items: list[str] = []
    for row in _poll_result_rows(poll):
        issue_key = str(row.get("issue_key") or "").strip()
        if not row.get("ok"):
            issue_number = row.get("issue_number")
            if row.get("error") == "unknown_issue_number":
                items.append(f"задача {issue_number}: номер не найден")
            else:
                key = issue_key or issue_number or row.get("kind") or "действие"
                items.append(f"{key}: не применено")
            continue
        if row.get("kind") == "create":
            key = issue_key or "новая задача"
            items.append(f"создана {key}: {row.get('summary') or ''}".strip())
        elif row.get("kind") == "close" and issue_key:
            items.append(f"{issue_key}: закрыта")
        elif row.get("kind") == "cancel" and issue_key:
            if row.get("transitioned"):
                items.append(f"{issue_key}: отменена")
            else:
                message = "добавлен комментарий, статус не изменен"
                items.append(f"{issue_key}: {message}")
        elif row.get("kind") == "in_progress" and issue_key:
            items.append(f"{issue_key}: в работе")
        elif row.get("kind") == "blocked" and issue_key:
            suffix = "статус обновлен" if row.get("transitioned") else "добавлен комментарий"
            items.append(f"{issue_key}: задержка, {suffix}")
        elif issue_key:
            items.append(f"{issue_key}: добавлен комментарий")
    return items


async def _load_standup_polls_by_login(
    session: Any,
    *,
    team_id: uuid.UUID,
    local_hour: str,
) -> dict[str, TelegramStandupPoll]:
    stmt = select(TelegramStandupPoll).where(
        TelegramStandupPoll.team_id == team_id,
        TelegramStandupPoll.local_hour == local_hour,
        TelegramStandupPoll.status.in_(("pending", "answered", "ambiguous")),
    )
    polls = (await session.execute(stmt)).scalars().all()
    return {poll.tracker_login.casefold(): poll for poll in polls}


async def _load_team_queue(session: Any, team_id: uuid.UUID) -> str:
    team = await session.get(Team, team_id)
    if team is not None and team.tracker_queue:
        return str(team.tracker_queue)
    return get_config().tracker.tracker_queue


async def _mark_standup_polls_reported(
    session: Any,
    *,
    team_id: uuid.UUID,
    local_hour: str,
) -> None:
    stmt = select(TelegramStandupPoll).where(
        TelegramStandupPoll.team_id == team_id,
        TelegramStandupPoll.local_hour == local_hour,
        TelegramStandupPoll.status.in_(("pending", "answered", "ambiguous")),
    )
    polls = (await session.execute(stmt)).scalars().all()
    for poll in polls:
        poll.status = "reported"


async def build_daily_digest_report(
    session: Any,
    *,
    team_id: uuid.UUID,
    now: datetime | None = None,
    client_factory: Callable[[], TrackerClient] = TrackerClient,
) -> DigestReport:
    cfg = get_config().daily_digest
    local_date, _, _ = day_window_utc(now, timezone_name=cfg.timezone)
    local_hour = local_hour_key(now, timezone_name=cfg.timezone)
    queue = await _load_team_queue(session, team_id)
    del client_factory
    members = await load_registered_participants(session, team_id=team_id)
    polls_by_login = await _load_standup_polls_by_login(
        session,
        team_id=team_id,
        local_hour=local_hour,
    )

    digest_members: list[DigestMember] = []
    for member in members:
        login_key = member.tracker_login.casefold()
        poll = polls_by_login.get(login_key)
        if poll is None:
            continue
        sections = _poll_event_sections(poll)
        if not sections:
            continue
        digest_members.append(
            DigestMember(
                login=member.tracker_login,
                display=member.display,
                in_progress=[],
                done_today=[],
                standup_response=_poll_response_text(poll) if poll is not None else "",
                applied_items=_poll_applied_items(poll) if poll is not None else [],
                sections=sections,
            )
        )

    return DigestReport(
        team_id=team_id,
        queue=queue,
        local_date=local_date,
        local_hour=local_hour,
        timezone=cfg.timezone,
        members=digest_members,
    )


def _format_issue_line(issue: DigestIssue) -> str:
    title = issue.summary or "(без названия)"
    status = f" [{issue.status}]" if issue.status else ""
    return f"- {issue.key}{status}: {title} ({issue.url})"


def _format_issue_section(issues: list[DigestIssue], *, limit: int) -> list[str]:
    if not issues:
        return ["- нет задач"]
    lines = [_format_issue_line(issue) for issue in issues[:limit]]
    overflow = len(issues) - limit
    if overflow > 0:
        lines.append(f"- ещё {overflow}")
    return lines


def format_daily_digest(report: DigestReport, *, max_issues_per_section: int | None = None) -> str:
    del max_issues_per_section
    lines = [
        f"Ежедневный отчёт по задачам за {report.local_date}",
        f"Очередь: {report.queue}",
        "",
    ]
    if not report.members:
        lines.append("За период нет обновлений.")
        return "\n".join(lines)

    for member in report.members:
        lines.append(f"{member.display} (@{member.login})")
        for title, section_lines in member.sections.items():
            if not section_lines:
                continue
            lines.append(f"{title}:")
            lines.extend(section_lines)
        lines.append("")
    return "\n".join(lines).rstrip()


def split_telegram_text(text: str, *, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= limit:
            current = line
            continue
        for start in range(0, len(line), limit):
            chunks.append(line[start : start + limit])
        current = ""
    if current:
        chunks.append(current)
    return chunks


async def _resolve_digest_chat(
    session: Any,
    *,
    team_id: uuid.UUID,
    configured_chat_id: str = "",
) -> TelegramChat | None:
    stmt = (
        select(TelegramChat)
        .join(TelegramInstallation, TelegramChat.installation_id == TelegramInstallation.id)
        .where(
            TelegramInstallation.team_id == team_id,
            TelegramInstallation.status == "active",
            TelegramInstallation.mode == "workspace_bot",
            TelegramChat.active.is_(True),
            TelegramChat.access_mode == "workspace_bot",
        )
    )
    if configured_chat_id.strip():
        stmt = stmt.where(TelegramChat.external_chat_id == configured_chat_id.strip())
    else:
        stmt = stmt.where(TelegramChat.type.in_(("group", "supergroup")))

    chats = (await session.execute(stmt)).scalars().all()
    if len(chats) == 1:
        return chats[0]
    if not chats:
        logger.warning("Daily digest skipped: no active Telegram digest chat for team %s", team_id)
    else:
        logger.warning(
            "Daily digest skipped: multiple active Telegram digest chats for team %s: %s",
            team_id,
            [chat.external_chat_id for chat in chats],
        )
    return None


async def _enqueue_digest_messages(
    session: Any,
    *,
    team_id: uuid.UUID,
    chat: TelegramChat,
    dedupe_slot: str,
    local_date: str,
    text: str,
) -> list[uuid.UUID]:
    chunks = split_telegram_text(text)
    outbox_ids: list[uuid.UUID] = []
    for index, chunk in enumerate(chunks, start=1):
        dedupe_key = f"daily-digest:{team_id}:{dedupe_slot}:{chat.external_chat_id}:part:{index}"
        existing = (
            await session.execute(
                select(TelegramOutbox).where(
                    TelegramOutbox.team_id == team_id,
                    TelegramOutbox.dedupe_key == dedupe_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            outbox_ids.append(existing.id)
            continue

        outbox = TelegramOutbox(
            id=uuid.uuid4(),
            team_id=team_id,
            installation_id=chat.installation_id,
            chat_id=chat.id,
            category="digest",
            target_chat_id=chat.external_chat_id,
            target_user_id=None,
            dedupe_key=dedupe_key,
            priority=120,
            status="pending",
            attempts=0,
            payload={
                "method": "sendMessage",
                "text": chunk,
                "metadata": {
                    "digest": "team_daily",
                    "local_date": local_date,
                    "part": index,
                    "parts": len(chunks),
                },
            },
        )
        session.add(outbox)
        outbox_ids.append(outbox.id)
    await session.flush()
    return outbox_ids


async def send_team_daily_digest(
    session: Any,
    *,
    team_id: str | uuid.UUID,
    now: datetime | None = None,
    client_factory: Callable[[], TrackerClient] = TrackerClient,
) -> dict[str, Any]:
    team_uuid = team_id if isinstance(team_id, uuid.UUID) else uuid.UUID(str(team_id))
    cfg = get_config().daily_digest
    local_date, _, _ = day_window_utc(now, timezone_name=cfg.timezone)
    chat = await _resolve_digest_chat(
        session,
        team_id=team_uuid,
        configured_chat_id=cfg.telegram_chat_id,
    )
    if chat is None:
        return {"status": "skipped", "reason": "telegram_chat_not_found", "date": local_date}

    report = await build_daily_digest_report(
        session,
        team_id=team_uuid,
        now=now,
        client_factory=client_factory,
    )
    text = format_daily_digest(report, max_issues_per_section=cfg.max_issues_per_section)
    outbox_ids = await _enqueue_digest_messages(
        session,
        team_id=team_uuid,
        chat=chat,
        dedupe_slot=report.local_hour,
        local_date=report.local_date,
        text=text,
    )
    await _mark_standup_polls_reported(
        session,
        team_id=team_uuid,
        local_hour=report.local_hour,
    )
    logger.info(
        "Daily digest enqueued for team %s chat %s (%d message parts)",
        team_uuid,
        chat.external_chat_id,
        len(outbox_ids),
    )
    return {
        "status": "enqueued",
        "date": report.local_date,
        "chat_id": chat.external_chat_id,
        "outbox_ids": [str(outbox_id) for outbox_id in outbox_ids],
    }


async def ensure_daily_digest_scheduled_job(session: Any, team_id: str | uuid.UUID) -> None:
    cfg = get_config().daily_digest
    team_uuid = team_id if isinstance(team_id, uuid.UUID) else uuid.UUID(str(team_id))

    from core.seed import ensure_agent_instances

    instance = (await ensure_agent_instances(session, str(team_uuid), ["pm_agent"]))["pm_agent"]
    digest_job = await _ensure_scheduled_job(
        session,
        agent_instance_id=instance.id,
        name=DAILY_DIGEST_JOB_NAME,
        cron_expr=cfg.cron_expr,
        payload={"type": DAILY_DIGEST_PAYLOAD_TYPE, "team_id": str(team_uuid)},
        enabled=cfg.enabled,
    )
    poll_cfg = get_config().standup_poll
    await _ensure_scheduled_job(
        session,
        agent_instance_id=instance.id,
        name=STANDUP_POLL_JOB_NAME,
        cron_expr=poll_cfg.cron_expr,
        payload={"type": STANDUP_POLL_PAYLOAD_TYPE, "team_id": str(team_uuid)},
        enabled=poll_cfg.enabled,
    )
    await _disable_legacy_digest_jobs(session, instance.id, keep_id=digest_job.id)
    await session.flush()


async def _ensure_scheduled_job(
    session: Any,
    *,
    agent_instance_id: uuid.UUID,
    name: str,
    cron_expr: str,
    payload: dict[str, Any],
    enabled: bool,
) -> ScheduledJob:
    from core.scheduler import compute_next_run

    stmt = select(ScheduledJob).where(
        ScheduledJob.agent_instance_id == agent_instance_id,
        ScheduledJob.name == name,
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    next_run = compute_next_run(cron_expr) if enabled else None

    if job is None:
        job = ScheduledJob(
            id=uuid.uuid4(),
            agent_instance_id=agent_instance_id,
            name=name,
            cron_expr=cron_expr,
            payload=payload,
            max_runs=None,
            run_count=0,
            next_run=next_run,
            enabled=enabled,
        )
        session.add(job)
        return job

    cron_changed = job.cron_expr != cron_expr
    job.cron_expr = cron_expr
    job.payload = payload
    job.enabled = enabled
    if enabled and (cron_changed or job.next_run is None):
        job.next_run = next_run
    if not enabled:
        job.next_run = None
    return job


async def _disable_legacy_digest_jobs(
    session: Any,
    agent_instance_id: uuid.UUID,
    *,
    keep_id: uuid.UUID | None = None,
) -> None:
    if not LEGACY_DAILY_DIGEST_JOB_NAMES:
        return
    stmt = select(ScheduledJob).where(
        ScheduledJob.agent_instance_id == agent_instance_id,
        ScheduledJob.name.in_(LEGACY_DAILY_DIGEST_JOB_NAMES),
    )
    rows = (await session.execute(stmt)).scalars().all()
    for row in rows:
        if keep_id is not None and row.id == keep_id:
            continue
        row.enabled = False


__all__ = [
    "DAILY_DIGEST_JOB_NAME",
    "DAILY_DIGEST_PAYLOAD_TYPE",
    "LEGACY_DAILY_DIGEST_JOB_NAMES",
    "STANDUP_POLL_JOB_NAME",
    "STANDUP_POLL_PAYLOAD_TYPE",
    "DigestIssue",
    "DigestMember",
    "DigestReport",
    "build_daily_digest_report",
    "build_done_today_yql",
    "build_in_progress_yql",
    "day_window_utc",
    "ensure_daily_digest_scheduled_job",
    "format_daily_digest",
    "local_hour_key",
    "send_team_daily_digest",
    "split_telegram_text",
]
