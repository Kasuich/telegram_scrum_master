"""Daily Tracker digest delivery through Telegram outbox."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from core.assignee_resolver import load_team_users
from core.config import get_config
from core.models import ScheduledJob, Team, TelegramChat, TelegramInstallation, TelegramOutbox
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


def _tracker_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _quote_yql(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_done_today_yql(queue: str, start_utc: datetime, end_utc: datetime) -> str:
    return (
        f"Queue: {_quote_yql(queue)} "
        f"AND Resolution: !empty() "
        f"AND Updated: >= {_quote_yql(_tracker_datetime(start_utc))} "
        f"AND Updated: < {_quote_yql(_tracker_datetime(end_utc))}"
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


async def _load_team_queue(session: Any, team_id: uuid.UUID) -> str:
    team = await session.get(Team, team_id)
    if team is not None and team.tracker_queue:
        return str(team.tracker_queue)
    return get_config().tracker.tracker_queue


async def build_daily_digest_report(
    session: Any,
    *,
    team_id: uuid.UUID,
    now: datetime | None = None,
    client_factory: Callable[[], TrackerClient] = TrackerClient,
) -> DigestReport:
    cfg = get_config().daily_digest
    local_date, start_utc, end_utc = day_window_utc(now, timezone_name=cfg.timezone)
    queue = await _load_team_queue(session, team_id)

    async with client_factory() as client:
        members = await load_team_users(client, queue)
        in_progress_raw: list[dict[str, Any]] = []
        for status in cfg.in_progress_status_list():
            in_progress_raw.extend(
                await client.search_issues(build_in_progress_yql(queue, status), limit=200)
            )
        done_raw = await client.search_issues(
            build_done_today_yql(queue, start_utc, end_utc),
            limit=200,
        )

    in_progress = _dedupe_issues(
        [issue for raw in in_progress_raw if (issue := _to_digest_issue(raw)) is not None]
    )
    done_today = _dedupe_issues(
        [issue for raw in done_raw if (issue := _to_digest_issue(raw)) is not None]
    )
    in_progress_by_assignee = _group_by_assignee(in_progress)
    done_by_assignee = _group_by_assignee(done_today)

    digest_members: list[DigestMember] = []
    for member in members:
        login_key = member.login.casefold()
        digest_members.append(
            DigestMember(
                login=member.login,
                display=member.display,
                in_progress=in_progress_by_assignee.get(login_key, []),
                done_today=done_by_assignee.get(login_key, []),
            )
        )

    return DigestReport(
        team_id=team_id,
        queue=queue,
        local_date=local_date,
        local_hour=local_hour_key(now, timezone_name=cfg.timezone),
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
    cfg = get_config().daily_digest
    limit = max_issues_per_section or cfg.max_issues_per_section
    lines = [
        f"Ежедневный отчёт по задачам за {report.local_date}",
        f"Очередь: {report.queue}",
        "",
    ]
    if not report.members:
        lines.append("Участники команды очереди не найдены.")
        return "\n".join(lines)

    for member in report.members:
        lines.append(f"{member.display} (@{member.login})")
        lines.append("В работе:")
        lines.extend(_format_issue_section(member.in_progress, limit=limit))
        lines.append("Сделано сегодня:")
        lines.extend(_format_issue_section(member.done_today, limit=limit))
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

    from core.scheduler import compute_next_run
    from core.seed import ensure_agent_instances

    instance = (await ensure_agent_instances(session, str(team_uuid), ["pm_agent"]))["pm_agent"]
    stmt = select(ScheduledJob).where(
        ScheduledJob.agent_instance_id == instance.id,
        ScheduledJob.name == DAILY_DIGEST_JOB_NAME,
    )
    job = (await session.execute(stmt)).scalar_one_or_none()
    payload = {"type": DAILY_DIGEST_PAYLOAD_TYPE, "team_id": str(team_uuid)}
    next_run = compute_next_run(cfg.cron_expr) if cfg.enabled else None

    if job is None:
        session.add(
            ScheduledJob(
                id=uuid.uuid4(),
                agent_instance_id=instance.id,
                name=DAILY_DIGEST_JOB_NAME,
                cron_expr=cfg.cron_expr,
                payload=payload,
                max_runs=None,
                run_count=0,
                next_run=next_run,
                enabled=cfg.enabled,
            )
        )
        await _disable_legacy_digest_jobs(session, instance.id)
        await session.flush()
        return

    cron_changed = job.cron_expr != cfg.cron_expr
    job.cron_expr = cfg.cron_expr
    job.payload = payload
    job.enabled = cfg.enabled
    if cfg.enabled and (cron_changed or job.next_run is None):
        job.next_run = next_run
    await _disable_legacy_digest_jobs(session, instance.id, keep_id=job.id)
    await session.flush()


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
