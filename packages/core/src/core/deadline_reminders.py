"""Per-assignee deadline reminder DMs + lead summary via Telegram outbox."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from core.config import get_config
from core.daily_digest import local_hour_key
from core.models import (
    ScheduledJob,
    Team,
    TeamMembership,
    TelegramInstallation,
    TelegramUser,
    TelegramUserLink,
)
from core.standup_poll import _enqueue_private_message
from core.tracker import TrackerClient

logger = logging.getLogger(__name__)

DEADLINE_REMINDER_JOB_NAME = "team_deadline_reminder"
DEADLINE_REMINDER_PAYLOAD_TYPE = "team_deadline_reminder"
DEADLINE_REMINDER_CATEGORY = "deadline_reminder"


@dataclass(frozen=True)
class ReminderRecipient:
    team_id: uuid.UUID
    installation_id: uuid.UUID
    telegram_user_id: uuid.UUID
    external_user_id: str
    user_id: uuid.UUID
    tracker_login: str
    display: str
    membership_role: str


@dataclass(frozen=True)
class DeadlineIssue:
    key: str
    summary: str
    deadline: str
    status: str
    url: str
    bucket: str  # "overdue" | "soon"
    assignee_login: str
    assignee_display: str


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Unknown deadline reminder timezone %r, falling back to Europe/Moscow", name
        )
        return ZoneInfo("Europe/Moscow")


def _quote_yql(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_deadline_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except (ValueError, TypeError):
        return None


def _status_display(issue: dict[str, Any]) -> str:
    status = issue.get("status")
    if isinstance(status, dict):
        return str(status.get("display") or status.get("key") or "").strip()
    return str(status or "").strip()


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
            or ""
        ).strip()
    return ""


def _member_display(user: TelegramUser, membership: TeamMembership) -> str:
    if membership.tracker_display_name:
        return membership.tracker_display_name
    parts = [user.first_name, user.last_name]
    name = " ".join(part for part in parts if part).strip()
    return name or user.username or membership.tracker_login


async def load_reminder_recipients(
    session: Any,
    *,
    team_id: uuid.UUID,
) -> list[ReminderRecipient]:
    """Load all Telegram-DM-able, Tracker-confirmed team members."""
    stmt = (
        select(TelegramUserLink, TeamMembership, TelegramUser, TelegramInstallation)
        .join(
            TeamMembership,
            (TeamMembership.team_id == TelegramUserLink.team_id)
            & (TeamMembership.user_id == TelegramUserLink.user_id),
        )
        .join(TelegramUser, TelegramUser.id == TelegramUserLink.telegram_user_id)
        .join(TelegramInstallation, TelegramInstallation.id == TelegramUserLink.installation_id)
        .where(
            TelegramUserLink.team_id == team_id,
            TelegramUserLink.status == "active",
            TeamMembership.tracker_match_status == "confirmed",
            TelegramInstallation.status == "active",
            TelegramUser.is_bot.is_(False),
            TelegramUser.is_blocked.is_(False),
        )
        .order_by(TeamMembership.tracker_login)
    )
    rows = (await session.execute(stmt)).all()
    recipients: list[ReminderRecipient] = []
    for link, membership, telegram_user, installation in rows:
        recipients.append(
            ReminderRecipient(
                team_id=team_id,
                installation_id=installation.id,
                telegram_user_id=telegram_user.id,
                external_user_id=telegram_user.external_user_id,
                user_id=membership.user_id,
                tracker_login=membership.tracker_login,
                display=_member_display(telegram_user, membership),
                membership_role=str(membership.role or "user"),
            )
        )
    return recipients


async def _load_team_queue(session: Any, team_id: uuid.UUID) -> str:
    team = await session.get(Team, team_id)
    if team is not None and team.tracker_queue:
        return str(team.tracker_queue)
    return get_config().tracker.tracker_queue


def build_deadline_issues_yql(
    *,
    queue: str,
    tracker_login: str,
    cutoff_date: date,
) -> str:
    """Build a YQL query for open issues assigned to *tracker_login* with deadline ≤ *cutoff_date*."""
    cutoff_iso = cutoff_date.isoformat()
    return (
        f"Queue: {_quote_yql(queue)} "
        f"AND Assignee: {_quote_yql(tracker_login)} "
        f"AND Resolution: empty() "
        f"AND Deadline: notEmpty() "
        f"AND Deadline: <= {_quote_yql(cutoff_iso)}"
    )


async def fetch_member_deadline_issues(
    client: TrackerClient,
    *,
    queue: str,
    tracker_login: str,
    today: date,
    cutoff_date: date,
    limit: int,
) -> tuple[list[DeadlineIssue], list[DeadlineIssue]]:
    """Return *(overdue, soon)* issue lists for *tracker_login*.

    - overdue: deadline < today
    - soon: today ≤ deadline ≤ cutoff_date
    """
    yql = build_deadline_issues_yql(
        queue=queue,
        tracker_login=tracker_login,
        cutoff_date=cutoff_date,
    )
    raw_issues = await client.search_issues(yql, limit=limit)
    overdue: list[DeadlineIssue] = []
    soon: list[DeadlineIssue] = []
    for raw in raw_issues:
        key = str(raw.get("key") or "").strip()
        if not key:
            continue
        dl = _parse_deadline_date(raw.get("deadline"))
        if dl is None:
            continue
        bucket = "overdue" if dl < today else "soon"
        issue = DeadlineIssue(
            key=key,
            summary=str(raw.get("summary") or "").strip(),
            deadline=dl.isoformat(),
            status=_status_display(raw),
            url=f"https://tracker.yandex.ru/{key}",
            bucket=bucket,
            assignee_login=_assignee_login(raw) or tracker_login,
            assignee_display=_assignee_display(raw),
        )
        if bucket == "overdue":
            overdue.append(issue)
        else:
            soon.append(issue)
    return overdue, soon


def format_member_reminder(
    *,
    recipient: ReminderRecipient,
    overdue: list[DeadlineIssue],
    soon: list[DeadlineIssue],
    local_date: str,
) -> str | None:
    """Format a per-assignee reminder DM. Returns None when the member has no at-risk tasks."""
    if not overdue and not soon:
        return None
    lines = [f"🔔 Дедлайны на {local_date} — {recipient.display}", ""]
    if overdue:
        lines.append(f"🔴 Просрочено ({len(overdue)}):")
        for issue in overdue:
            lines.append(
                f"- {issue.key} [{issue.deadline}]: {issue.summary or '(без названия)'}"
            )
            lines.append(f"  {issue.url}")
    if overdue and soon:
        lines.append("")
    if soon:
        lines.append(f"🟡 Скоро дедлайн ({len(soon)}):")
        for issue in soon:
            lines.append(
                f"- {issue.key} [{issue.deadline}]: {issue.summary or '(без названия)'}"
            )
            lines.append(f"  {issue.url}")
    return "\n".join(lines)


def format_lead_summary(
    *,
    member_data: list[tuple[ReminderRecipient, list[DeadlineIssue], list[DeadlineIssue]]],
    local_date: str,
) -> str | None:
    """Format the consolidated lead summary DM. Returns None when no member has at-risk tasks."""
    active = [(r, ov, sn) for r, ov, sn in member_data if ov or sn]
    if not active:
        return None
    lines = [f"📋 Сводка по дедлайнам команды на {local_date}", ""]
    for recipient, overdue, soon in active:
        lines.append(f"👤 {recipient.display} (@{recipient.tracker_login})")
        if overdue:
            lines.append(f"  🔴 Просрочено ({len(overdue)}):")
            for issue in overdue:
                lines.append(
                    f"    • {issue.key} [{issue.deadline}]: {issue.summary or '(без названия)'}"
                )
        if soon:
            lines.append(f"  🟡 Скоро ({len(soon)}):")
            for issue in soon:
                lines.append(
                    f"    • {issue.key} [{issue.deadline}]: {issue.summary or '(без названия)'}"
                )
        lines.append("")
    return "\n".join(lines).rstrip()


def _resolve_lead(
    recipients: list[ReminderRecipient],
    lead_roles: list[str],
    lead_login: str,
) -> ReminderRecipient | None:
    """Find lead recipient: first by role, then by fallback login."""
    lead_role_set = {r.casefold() for r in lead_roles}
    for r in recipients:
        if r.membership_role.casefold() in lead_role_set:
            return r
    if lead_login:
        for r in recipients:
            if r.tracker_login.casefold() == lead_login.casefold():
                return r
    return None


async def send_team_deadline_reminders(
    session: Any,
    *,
    team_id: str | uuid.UUID,
    now: datetime | None = None,
    client_factory: Callable[[], TrackerClient] = TrackerClient,
) -> dict[str, Any]:
    """Enqueue deadline reminder DMs for a team.

    Per-assignee DMs list only their own at-risk tasks; the lead receives a
    consolidated summary of the whole team. Idempotent within the same hourly
    slot via dedupe_key.
    """
    team_uuid = team_id if isinstance(team_id, uuid.UUID) else uuid.UUID(str(team_id))
    cfg = get_config()
    reminder_cfg = cfg.deadline_reminder
    if not reminder_cfg.enabled:
        return {"status": "skipped", "reason": "disabled"}

    tz = _timezone(reminder_cfg.timezone)
    current = (now or datetime.now(tz=timezone.utc)).replace(
        tzinfo=timezone.utc
    ) if (now or datetime.now(tz=timezone.utc)).tzinfo is None else (now or datetime.now(tz=timezone.utc))
    local_now = current.astimezone(tz)
    today = local_now.date()
    cutoff_date = today + timedelta(days=reminder_cfg.soon_days)
    local_date = today.isoformat()
    hour_slot = local_hour_key(now, timezone_name=reminder_cfg.timezone)

    queue = await _load_team_queue(session, team_uuid)
    recipients = await load_reminder_recipients(session, team_id=team_uuid)
    if not recipients:
        return {
            "status": "skipped",
            "reason": "no_registered_participants",
            "local_date": local_date,
        }

    member_data: list[tuple[ReminderRecipient, list[DeadlineIssue], list[DeadlineIssue]]] = []
    assignee_outbox_ids: list[str] = []

    async with client_factory() as client:
        for recipient in recipients:
            try:
                overdue, soon = await fetch_member_deadline_issues(
                    client,
                    queue=queue,
                    tracker_login=recipient.tracker_login,
                    today=today,
                    cutoff_date=cutoff_date,
                    limit=reminder_cfg.max_issues_per_member,
                )
            except Exception:
                logger.exception(
                    "Deadline reminder: failed to fetch issues for %s", recipient.tracker_login
                )
                overdue, soon = [], []

            member_data.append((recipient, overdue, soon))

            if reminder_cfg.notify_assignees and (overdue or soon):
                text = format_member_reminder(
                    recipient=recipient,
                    overdue=overdue,
                    soon=soon,
                    local_date=local_date,
                )
                if text is not None:
                    outbox = await _enqueue_private_message(
                        session,
                        team_id=team_uuid,
                        installation_id=recipient.installation_id,
                        target_user_id=recipient.external_user_id,
                        text=text,
                        category=DEADLINE_REMINDER_CATEGORY,
                        dedupe_key=(
                            f"deadline-reminder:{team_uuid}:{hour_slot}:"
                            f"{recipient.telegram_user_id}"
                        ),
                    )
                    assignee_outbox_ids.append(str(outbox.id))

    lead_outbox_id: str | None = None
    if reminder_cfg.notify_lead:
        lead_recipient = _resolve_lead(
            recipients,
            reminder_cfg.lead_role_list(),
            reminder_cfg.lead_login,
        )
        if lead_recipient is not None:
            summary_text = format_lead_summary(
                member_data=member_data, local_date=local_date
            )
            if summary_text is not None:
                lead_outbox = await _enqueue_private_message(
                    session,
                    team_id=team_uuid,
                    installation_id=lead_recipient.installation_id,
                    target_user_id=lead_recipient.external_user_id,
                    text=summary_text,
                    category=DEADLINE_REMINDER_CATEGORY,
                    dedupe_key=f"deadline-reminder:{team_uuid}:{hour_slot}:lead",
                )
                lead_outbox_id = str(lead_outbox.id)
        else:
            logger.warning(
                "Deadline reminder: no DM-able lead for team %s "
                "(lead_roles=%r, lead_login=%r) — skipping lead summary",
                team_uuid,
                reminder_cfg.lead_role_list(),
                reminder_cfg.lead_login,
            )

    await session.flush()
    logger.info(
        "Deadline reminders enqueued for team %s (%d assignee DMs, lead=%s)",
        team_uuid,
        len(assignee_outbox_ids),
        lead_outbox_id,
    )
    return {
        "status": "enqueued",
        "local_date": local_date,
        "hour_slot": hour_slot,
        "assignee_outbox_ids": assignee_outbox_ids,
        "lead_outbox_id": lead_outbox_id,
    }


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


async def ensure_deadline_reminder_scheduled_job(
    session: Any,
    team_id: str | uuid.UUID,
) -> None:
    """Upsert the deadline reminder ScheduledJob, syncing cron on restart if changed."""
    cfg = get_config().deadline_reminder
    team_uuid = team_id if isinstance(team_id, uuid.UUID) else uuid.UUID(str(team_id))

    from core.seed import ensure_agent_instances

    instance = (await ensure_agent_instances(session, str(team_uuid), ["pm_agent"]))["pm_agent"]
    await _ensure_scheduled_job(
        session,
        agent_instance_id=instance.id,
        name=DEADLINE_REMINDER_JOB_NAME,
        cron_expr=cfg.cron_expr,
        payload={"type": DEADLINE_REMINDER_PAYLOAD_TYPE, "team_id": str(team_uuid)},
        enabled=cfg.enabled,
    )
    await session.flush()


__all__ = [
    "DEADLINE_REMINDER_CATEGORY",
    "DEADLINE_REMINDER_JOB_NAME",
    "DEADLINE_REMINDER_PAYLOAD_TYPE",
    "DeadlineIssue",
    "ReminderRecipient",
    "build_deadline_issues_yql",
    "ensure_deadline_reminder_scheduled_job",
    "fetch_member_deadline_issues",
    "format_lead_summary",
    "format_member_reminder",
    "load_reminder_recipients",
    "send_team_deadline_reminders",
]
