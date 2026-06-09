"""Hourly Telegram standup polls for registered Tracker participants."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from core.config import get_config
from core.models import (
    Team,
    TeamMembership,
    TelegramInstallation,
    TelegramOutbox,
    TelegramStandupPoll,
    TelegramUser,
    TelegramUserLink,
)
from core.tracker import TrackerClient, TrackerError

logger = logging.getLogger(__name__)

STANDUP_POLL_JOB_NAME = "team_hourly_standup_poll"
STANDUP_POLL_PAYLOAD_TYPE = "team_standup_poll"
STANDUP_POLL_CATEGORY = "standup_poll"


@dataclass(frozen=True)
class RegisteredParticipant:
    team_id: uuid.UUID
    installation_id: uuid.UUID
    telegram_user_id: uuid.UUID
    external_user_id: str
    user_id: uuid.UUID
    tracker_login: str
    display: str
    board_id: str
    board_name: str


@dataclass(frozen=True)
class PollIssue:
    number: int
    key: str
    summary: str
    status: str
    url: str


@dataclass(frozen=True)
class ParsedAction:
    kind: str
    text: str
    issue_number: int | None = None


def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown standup poll timezone %r, falling back to Europe/Moscow", name)
        return ZoneInfo("Europe/Moscow")


def poll_digest_hour_key(
    now: datetime | None = None,
    *,
    timezone_name: str = "Europe/Moscow",
    lead_minutes: int = 10,
) -> str:
    tz = _timezone(timezone_name)
    current = now or datetime.now(tz=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    digest_time = current + timedelta(minutes=max(0, lead_minutes))
    return digest_time.astimezone(tz).strftime("%Y-%m-%dT%H")


def _quote_yql(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _assignee_yql(login: str) -> str:
    return f"Assignee: {_quote_yql(login)}"


def _and_yql(*parts: str) -> str:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return ""
    wrapped = [
        part if part.startswith("(") and part.endswith(")") else f"({part})"
        for part in cleaned
    ]
    return " AND ".join(wrapped)


def _field_name(raw: str) -> str:
    aliases = {
        "queue": "Queue",
        "assignee": "Assignee",
        "status": "Status",
        "priority": "Priority",
        "type": "Type",
        "summary": "Summary",
    }
    key = str(raw).strip()
    return aliases.get(key.casefold(), key)


def _simple_filter_yql(filters: Any) -> str | None:
    if not isinstance(filters, dict):
        return None
    parts: list[str] = []
    for key, value in filters.items():
        field = _field_name(str(key))
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{field}: {_quote_yql(str(value))}")
            continue
        if isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) for v in value):
            if not value:
                continue
            variants = [f"{field}: {_quote_yql(str(v))}" for v in value]
            parts.append("(" + " OR ".join(variants) + ")")
            continue
        return None
    return " AND ".join(parts) if parts else None


def build_member_issues_yql(
    *,
    queue: str,
    tracker_login: str,
    board: dict[str, Any] | None = None,
) -> str:
    board = board or {}
    base = str(board.get("query") or "").strip()
    if not base:
        base = _simple_filter_yql(board.get("filter")) or ""
    if not base:
        base = f"Queue: {_quote_yql(queue)}"
    return _and_yql(base, _assignee_yql(tracker_login), "Resolution: empty()")


def _issue_status(issue: dict[str, Any]) -> str:
    status = issue.get("status")
    if isinstance(status, dict):
        return str(status.get("display") or status.get("key") or "").strip()
    return str(status or "").strip()


def _to_poll_issue(raw: dict[str, Any], number: int) -> PollIssue | None:
    key = str(raw.get("key") or "").strip()
    if not key:
        return None
    return PollIssue(
        number=number,
        key=key,
        summary=str(raw.get("summary") or "").strip(),
        status=_issue_status(raw),
        url=f"https://tracker.yandex.ru/{key}",
    )


def _issue_payload(issue: PollIssue) -> dict[str, Any]:
    return {
        "number": issue.number,
        "key": issue.key,
        "summary": issue.summary,
        "status": issue.status,
        "url": issue.url,
    }


def _participant_display(user: TelegramUser, membership: TeamMembership) -> str:
    if membership.tracker_display_name:
        return membership.tracker_display_name
    parts = [user.first_name, user.last_name]
    name = " ".join(part for part in parts if part).strip()
    return name or user.username or membership.tracker_login


async def load_registered_participants(
    session: Any,
    *,
    team_id: uuid.UUID,
) -> list[RegisteredParticipant]:
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
            TelegramInstallation.mode == "workspace_bot",
            TelegramUser.is_bot.is_(False),
            TelegramUser.is_blocked.is_(False),
        )
        .order_by(TeamMembership.tracker_login)
    )
    rows = (await session.execute(stmt)).all()
    participants: list[RegisteredParticipant] = []
    for link, membership, telegram_user, installation in rows:
        settings = membership.settings_json or {}
        participants.append(
            RegisteredParticipant(
                team_id=team_id,
                installation_id=installation.id,
                telegram_user_id=telegram_user.id,
                external_user_id=telegram_user.external_user_id,
                user_id=membership.user_id,
                tracker_login=membership.tracker_login,
                display=_participant_display(telegram_user, membership),
                board_id=str(membership.default_board_id or ""),
                board_name=str(
                    settings.get("default_board_name") or membership.default_board_id or ""
                ),
            )
        )
    return participants


async def _load_team_queue(session: Any, team_id: uuid.UUID) -> str:
    team = await session.get(Team, team_id)
    if team is not None and team.tracker_queue:
        return str(team.tracker_queue)
    return get_config().tracker.tracker_queue


async def fetch_participant_issues(
    client: TrackerClient,
    *,
    queue: str,
    participant: RegisteredParticipant,
    limit: int,
) -> list[PollIssue]:
    board: dict[str, Any] | None = None
    if participant.board_id:
        try:
            board = await client.get_board(participant.board_id)
        except TrackerError:
            logger.exception(
                "Standup poll: failed to load board %s for %s, using queue fallback",
                participant.board_id,
                participant.tracker_login,
            )
    yql = build_member_issues_yql(
        queue=queue,
        tracker_login=participant.tracker_login,
        board=board,
    )
    raw_issues = await client.search_issues(yql, limit=limit)
    issues: list[PollIssue] = []
    for raw in raw_issues:
        issue = _to_poll_issue(raw, len(issues) + 1)
        if issue is not None:
            issues.append(issue)
    return issues


def format_standup_poll_message(
    *,
    participant: RegisteredParticipant,
    issues: list[PollIssue],
    local_hour: str,
) -> str:
    board = f" ({participant.board_name})" if participant.board_name else ""
    lines = [
        (
            f"{participant.display}, короткий статус перед дайджестом "
            f"{local_hour}{board}:"
        ),
        "",
    ]
    if issues:
        lines.append("Ваши задачи:")
        for issue in issues:
            status = f" [{issue.status}]" if issue.status else ""
            title = issue.summary or "(без названия)"
            lines.append(f"{issue.number}. {issue.key}{status}: {title}")
    else:
        lines.append("Открытых задач на вашей доске не нашел.")
    lines.extend(
        [
            "",
            "Ответьте, например:",
            "задача 1 закрыта",
            "задача 2 задерживается: жду доступ",
            "новая задача: подготовить демо",
        ]
    )
    return "\n".join(lines)


async def _enqueue_private_message(
    session: Any,
    *,
    team_id: uuid.UUID,
    installation_id: uuid.UUID,
    target_user_id: str,
    text: str,
    category: str,
    dedupe_key: str,
) -> TelegramOutbox:
    existing = (
        await session.execute(
            select(TelegramOutbox).where(
                TelegramOutbox.team_id == team_id,
                TelegramOutbox.dedupe_key == dedupe_key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    outbox = TelegramOutbox(
        id=uuid.uuid4(),
        team_id=team_id,
        installation_id=installation_id,
        category=category,
        target_chat_id=target_user_id,
        target_user_id=target_user_id,
        dedupe_key=dedupe_key,
        priority=105,
        status="pending",
        attempts=0,
        payload={"method": "sendMessage", "text": text},
    )
    session.add(outbox)
    await session.flush()
    return outbox


async def send_team_standup_poll(
    session: Any,
    *,
    team_id: str | uuid.UUID,
    now: datetime | None = None,
    client_factory: Callable[[], TrackerClient] = TrackerClient,
) -> dict[str, Any]:
    team_uuid = team_id if isinstance(team_id, uuid.UUID) else uuid.UUID(str(team_id))
    cfg = get_config()
    poll_cfg = cfg.standup_poll
    if not poll_cfg.enabled:
        return {"status": "skipped", "reason": "disabled"}

    local_hour = poll_digest_hour_key(
        now,
        timezone_name=cfg.daily_digest.timezone,
        lead_minutes=poll_cfg.lead_minutes,
    )
    queue = await _load_team_queue(session, team_uuid)
    participants = await load_registered_participants(session, team_id=team_uuid)
    if not participants:
        return {
            "status": "skipped",
            "reason": "no_registered_participants",
            "local_hour": local_hour,
        }

    poll_ids: list[str] = []
    outbox_ids: list[str] = []
    async with client_factory() as client:
        for participant in participants:
            issues = await fetch_participant_issues(
                client,
                queue=queue,
                participant=participant,
                limit=poll_cfg.max_issues_per_member,
            )
            existing = (
                await session.execute(
                    select(TelegramStandupPoll).where(
                        TelegramStandupPoll.team_id == team_uuid,
                        TelegramStandupPoll.telegram_user_id == participant.telegram_user_id,
                        TelegramStandupPoll.local_hour == local_hour,
                    )
                )
            ).scalar_one_or_none()
            issue_payloads = [_issue_payload(issue) for issue in issues]
            if existing is None:
                poll = TelegramStandupPoll(
                    id=uuid.uuid4(),
                    team_id=team_uuid,
                    installation_id=participant.installation_id,
                    telegram_user_id=participant.telegram_user_id,
                    user_id=participant.user_id,
                    tracker_login=participant.tracker_login,
                    board_id=participant.board_id or None,
                    board_name=participant.board_name or None,
                    local_hour=local_hour,
                    issues_json=issue_payloads,
                    applied_json={},
                    status="pending",
                    sent_at=datetime.now(timezone.utc),
                )
                session.add(poll)
            else:
                poll = existing
                if poll.status == "pending":
                    poll.issues_json = issue_payloads
                    poll.sent_at = poll.sent_at or datetime.now(timezone.utc)
            text = format_standup_poll_message(
                participant=participant,
                issues=issues,
                local_hour=local_hour,
            )
            outbox = await _enqueue_private_message(
                session,
                team_id=team_uuid,
                installation_id=participant.installation_id,
                target_user_id=participant.external_user_id,
                text=text,
                category=STANDUP_POLL_CATEGORY,
                dedupe_key=(
                    f"standup-poll:{team_uuid}:{local_hour}:"
                    f"{participant.telegram_user_id}:question"
                ),
            )
            poll_ids.append(str(poll.id))
            outbox_ids.append(str(outbox.id))
    await session.flush()
    return {
        "status": "enqueued",
        "local_hour": local_hour,
        "poll_ids": poll_ids,
        "outbox_ids": outbox_ids,
    }


_TASK_RE = re.compile(r"(?:задач[аиу]?|task)\s*#?\s*(\d+)", re.IGNORECASE)
_NUMBERED_TASK_RE = re.compile(r"(?<!\S)(\d{1,3})\s*[\).:]", re.IGNORECASE)
_ACTION_MARKER_RE = re.compile(
    r"(?P<task>(?:задач[аиу]?|task)\s*#?\s*(?P<task_num>\d+))"
    r"|(?P<numbered>(?<!\S)(?P<numbered_num>\d{1,3})\s*[\).:])"
    r"|(?P<new>(?:новая\s+задача|new\s+task)\s*[:\-—]?\s*)",
    re.IGNORECASE,
)
_NEW_TASK_RE = re.compile(
    r"(?:новая\s+задача|new\s+task)\s*[:\-—]\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)


def is_standup_response(text: str) -> bool:
    lowered = text.casefold()
    return bool(
        _TASK_RE.search(text)
        or _NUMBERED_TASK_RE.search(text)
        or _NEW_TASK_RE.search(text)
        or "закры" in lowered
    )


def _action_kind(text: str) -> str:
    lowered = text.casefold()
    if any(
        token in lowered
        for token in ("закры", "готов", "сделан", "done", "closed")
    ):
        return "close"
    if any(token in lowered for token in ("отмен", "отмени", "cancel")):
        return "cancel"
    blocked_tokens = (
        "задерж",
        "блокер",
        "блокир",
        "не успева",
        "blocked",
    )
    if any(token in lowered for token in blocked_tokens):
        return "blocked"
    if any(
        token in lowered
        for token in ("в работе", "начал", "продолжа", "in progress")
    ):
        return "in_progress"
    return "comment"


def _iter_response_parts(text: str) -> list[tuple[str, int | None, str]]:
    matches = list(_ACTION_MARKER_RE.finditer(text))
    if not matches:
        return []
    parts: list[tuple[str, int | None, str]] = []
    for index, match in enumerate(matches):
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw = text[match.start() : next_start].strip(" \n\r\t.;,")
        body = text[match.end() : next_start].strip(" \n\r\t.;,")
        if not raw:
            continue
        if match.group("new") is not None:
            parts.append(("create", None, body))
            continue
        number = match.group("task_num") or match.group("numbered_num")
        if number is None:
            continue
        parts.append(("issue", int(number), raw))
    return parts


def parse_standup_response(text: str) -> list[ParsedAction]:
    actions: list[ParsedAction] = []
    for marker_kind, issue_number, text_part in _iter_response_parts(text):
        if marker_kind == "create":
            summary = text_part.strip()
            if summary:
                actions.append(ParsedAction(kind="create", text=summary))
            continue
        actions.append(
            ParsedAction(
                kind=_action_kind(text_part),
                issue_number=issue_number,
                text=text_part,
            )
        )
    return actions


async def find_pending_poll_for_response(
    session: Any,
    *,
    team_id: uuid.UUID,
    telegram_user_id: uuid.UUID,
) -> TelegramStandupPoll | None:
    stmt = (
        select(TelegramStandupPoll)
        .where(
            TelegramStandupPoll.team_id == team_id,
            TelegramStandupPoll.telegram_user_id == telegram_user_id,
            TelegramStandupPoll.status == "pending",
        )
        .order_by(TelegramStandupPoll.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _issue_by_number(poll: TelegramStandupPoll) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    for item in poll.issues_json or []:
        try:
            number = int(item.get("number"))
        except (TypeError, ValueError):
            continue
        result[number] = item
    return result


async def _try_transition(
    client: TrackerClient,
    issue_key: str,
    aliases: list[str],
    *,
    comment: str | None = None,
    resolution: str | None = None,
) -> tuple[bool, str | None]:
    last_error: str | None = None
    for alias in aliases:
        try:
            await client.transition_issue(
                issue_key,
                alias,
                comment=comment,
                resolution=resolution,
            )
            return True, None
        except TrackerError as exc:
            last_error = str(exc)
    return False, last_error


async def _apply_action(
    client: TrackerClient,
    *,
    action: ParsedAction,
    poll: TelegramStandupPoll,
    issue_map: dict[int, dict[str, Any]],
    queue: str,
) -> dict[str, Any]:
    if action.kind == "create":
        issue = await client.create_issue(
            queue,
            action.text,
            assignee=poll.tracker_login,
            description=f"Создано из standup-ответа за {poll.local_hour}.",
        )
        return {
            "kind": "create",
            "summary": action.text,
            "issue_key": str(issue.get("key") or ""),
            "ok": True,
        }

    if action.issue_number is None or action.issue_number not in issue_map:
        return {
            "kind": action.kind,
            "issue_number": action.issue_number,
            "ok": False,
            "error": "unknown_issue_number",
        }

    issue_key = str(issue_map[action.issue_number].get("key") or "")
    if action.kind == "close":
        ok, error = await _try_transition(
            client,
            issue_key,
            ["closed", "close"],
            comment=action.text,
            resolution="fixed",
        )
        if not ok:
            await client.comment_issue(issue_key, action.text)
        return {
            "kind": "close",
            "issue_number": action.issue_number,
            "issue_key": issue_key,
            "ok": ok,
            "error": error,
        }
    if action.kind == "cancel":
        await client.comment_issue(issue_key, action.text)
        ok, error = await _try_transition(
            client,
            issue_key,
            ["cancelled", "cancel", "Отменить", "Отменено", "Отмена"],
        )
        return {
            "kind": "cancel",
            "issue_number": action.issue_number,
            "issue_key": issue_key,
            "ok": True,
            "transitioned": ok,
            "error": error if not ok else None,
        }
    if action.kind == "in_progress":
        ok, error = await _try_transition(
            client,
            issue_key,
            ["in_progress", "В работе", "start"],
            comment=action.text,
        )
        if not ok:
            await client.comment_issue(issue_key, action.text)
        return {
            "kind": "in_progress",
            "issue_number": action.issue_number,
            "issue_key": issue_key,
            "ok": ok,
            "error": error,
        }
    if action.kind == "blocked":
        await client.comment_issue(issue_key, action.text)
        aliases = get_config().standup_poll.blocked_transition_alias_list()
        ok, error = await _try_transition(client, issue_key, aliases)
        return {
            "kind": "blocked",
            "issue_number": action.issue_number,
            "issue_key": issue_key,
            "ok": True,
            "transitioned": ok,
            "error": error if not ok else None,
        }

    await client.comment_issue(issue_key, action.text)
    return {
        "kind": "comment",
        "issue_number": action.issue_number,
        "issue_key": issue_key,
        "ok": True,
    }


def _format_apply_report(results: list[dict[str, Any]]) -> str:
    if not results:
        return (
            "Не понял, какие изменения применить. Примеры: "
            "`задача 1 закрыта`, "
            "`задача 2 задерживается: жду доступ`, "
            "`новая задача: демо`."
        )
    lines = ["Принял статус:"]
    for item in results:
        if item.get("ok"):
            key = item.get("issue_key") or item.get("summary")
            if item.get("kind") == "blocked" and not item.get("transitioned", True):
                lines.append(f"- {key}: комментарий, статус не изменил")
            elif item.get("kind") == "create":
                lines.append(f"- создал задачу {key}: {item.get('summary')}")
            elif item.get("kind") == "close":
                lines.append(f"- {key}: закрыл")
            elif item.get("kind") == "cancel":
                if item.get("transitioned"):
                    lines.append(f"- {key}: отменил")
                else:
                    lines.append(
                        f"- {key}: комментарий, статус не изменил"
                    )
            elif item.get("kind") == "in_progress":
                lines.append(f"- {key}: перевел в работу")
            else:
                lines.append(f"- {key}: добавил комментарий")
        elif item.get("error") == "unknown_issue_number":
            lines.append(f"- задача {item.get('issue_number')}: номера нет")
        else:
            key = item.get("issue_key") or item.get("issue_number")
            lines.append(f"- {key}: не применил")
    return "\n".join(lines)


async def handle_standup_response(
    session: Any,
    *,
    team_id: uuid.UUID,
    telegram_user_id: uuid.UUID,
    text: str,
    client_factory: Callable[[], TrackerClient] = TrackerClient,
) -> str | None:
    if not is_standup_response(text):
        return None

    poll = await find_pending_poll_for_response(
        session,
        team_id=team_id,
        telegram_user_id=telegram_user_id,
    )
    if poll is None:
        return None

    actions = parse_standup_response(text)
    queue = await _load_team_queue(session, team_id)
    issue_map = _issue_by_number(poll)
    results: list[dict[str, Any]] = []
    if actions:
        async with client_factory() as client:
            for action in actions:
                try:
                    results.append(
                        await _apply_action(
                            client,
                            action=action,
                            poll=poll,
                            issue_map=issue_map,
                            queue=queue,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - report per action to the user
                    logger.exception("Standup poll action failed")
                    results.append(
                        {
                            "kind": action.kind,
                            "issue_number": action.issue_number,
                            "ok": False,
                            "error": str(exc),
                        }
                    )

    poll.response_text = text
    poll.applied_json = {"actions": [action.__dict__ for action in actions], "results": results}
    poll.status = "answered" if actions else "ambiguous"
    poll.responded_at = datetime.now(timezone.utc)
    await session.flush()
    return _format_apply_report(results)


__all__ = [
    "RegisteredParticipant",
    "PollIssue",
    "ParsedAction",
    "STANDUP_POLL_CATEGORY",
    "STANDUP_POLL_JOB_NAME",
    "STANDUP_POLL_PAYLOAD_TYPE",
    "build_member_issues_yql",
    "fetch_participant_issues",
    "find_pending_poll_for_response",
    "format_standup_poll_message",
    "handle_standup_response",
    "is_standup_response",
    "load_registered_participants",
    "parse_standup_response",
    "poll_digest_hour_key",
    "send_team_standup_poll",
]
