"""Telegram onboarding and Tracker identity matching."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from core.assignee_resolver import TrackerUser, best_user_match, load_team_users
from core.models import (
    Team,
    TeamMembership,
    TelegramCallbackToken,
    TelegramInstallation,
    TelegramOnboardingSession,
    TelegramOutbox,
    TelegramUser,
    TelegramUserLink,
    User,
)
from core.tracker import TrackerClient
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

ONBOARDING_TTL = timedelta(hours=24)


async def get_confirmed_membership(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    telegram_user_id: uuid.UUID,
) -> tuple[TelegramUserLink, TeamMembership] | None:
    stmt = (
        select(TelegramUserLink, TeamMembership)
        .join(
            TeamMembership,
            (TeamMembership.team_id == TelegramUserLink.team_id)
            & (TeamMembership.user_id == TelegramUserLink.user_id),
        )
        .where(
            TelegramUserLink.team_id == team_id,
            TelegramUserLink.telegram_user_id == telegram_user_id,
            TelegramUserLink.status == "active",
            TeamMembership.tracker_match_status == "confirmed",
        )
    )
    row = (await session.execute(stmt)).one_or_none()
    return (row[0], row[1]) if row is not None else None


async def _pending_session(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
    telegram_user_id: uuid.UUID,
) -> TelegramOnboardingSession | None:
    stmt = (
        select(TelegramOnboardingSession)
        .where(
            TelegramOnboardingSession.team_id == team_id,
            TelegramOnboardingSession.telegram_user_id == telegram_user_id,
            TelegramOnboardingSession.status == "pending",
        )
        .order_by(desc(TelegramOnboardingSession.created_at))
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is not None and row.expires_at <= datetime.now(timezone.utc):
        row.status = "expired"
        await session.flush()
        return None
    return row


def _enqueue_message(
    session: AsyncSession,
    *,
    installation: TelegramInstallation,
    target_chat_id: str,
    text: str,
    category: str,
    dedupe_key: str,
    reply_to_message_id: str | None = None,
    reply_markup: dict | None = None,
) -> TelegramOutbox:
    outbox = TelegramOutbox(
        team_id=installation.team_id,
        installation_id=installation.id,
        category=category,
        target_chat_id=target_chat_id,
        target_user_id=target_chat_id if not target_chat_id.startswith("-") else None,
        dedupe_key=dedupe_key,
        priority=110,
        status="pending",
        attempts=0,
        payload={
            "method": "sendMessage",
            "text": text,
            **(
                {"reply_to_message_id": reply_to_message_id}
                if reply_to_message_id is not None
                else {}
            ),
            **({"reply_markup": reply_markup} if reply_markup is not None else {}),
        },
    )
    session.add(outbox)
    return outbox


async def start_onboarding(
    session: AsyncSession,
    *,
    installation: TelegramInstallation,
    telegram_user: TelegramUser,
    source_chat_id: str,
    source_message_id: str | None,
    source_is_private: bool,
) -> TelegramOnboardingSession:
    message_key = source_message_id or uuid.uuid4().hex
    onboarding = await _pending_session(
        session,
        team_id=installation.team_id,
        telegram_user_id=telegram_user.id,
    )
    if onboarding is None:
        onboarding = TelegramOnboardingSession(
            team_id=installation.team_id,
            installation_id=installation.id,
            telegram_user_id=telegram_user.id,
            status="pending",
            step_key="tracker_login",
            answers_json={},
            attempts=0,
            expires_at=datetime.now(timezone.utc) + ONBOARDING_TTL,
        )
        session.add(onboarding)
        await session.flush()

    if not source_is_private:
        bot_username = str((installation.settings or {}).get("bot_username") or "").lstrip("@")
        private_hint = (
            f" Откройте бота: https://t.me/{bot_username}?start=auth"
            if bot_username
            else " Откройте личный чат с ботом и нажмите Start."
        )
        _enqueue_message(
            session,
            installation=installation,
            target_chat_id=source_chat_id,
            text=(
                "Чтобы пользоваться ботом, вам нужно пройти авторизацию "
                "и привязать профиль Tracker."
                f"{private_hint}"
            ),
            category="authorization",
            dedupe_key=f"telegram:onboarding:group:{onboarding.id}:{message_key}",
            reply_to_message_id=source_message_id,
        )

    _enqueue_message(
        session,
        installation=installation,
        target_chat_id=telegram_user.external_user_id,
        text=(
            "Нужно привязать ваш профиль в Yandex Tracker. "
            "Отправьте логин, почту или имя в Tracker одним сообщением."
        ),
        category="authorization",
        dedupe_key=f"telegram:onboarding:private:{onboarding.id}:{message_key}",
    )
    await session.flush()
    return onboarding


def _find_tracker_user(login: str, users: list[TrackerUser]) -> TrackerUser | None:
    normalized = " ".join(login.strip().split()).casefold()
    login_value = normalized.lstrip("@")
    return next(
        (
            user
            for user in users
            if user.login.casefold() == login_value
            or user.email.casefold() == normalized
            or " ".join(user.display.split()).casefold() == normalized
        ),
        None,
    )


def _suggest_tracker_user(value: str, users: list[TrackerUser]) -> TrackerUser | None:
    exact = _find_tracker_user(value, users)
    if exact is not None:
        return exact
    match = best_user_match(value, users, threshold=0.45)
    if match is None:
        return None
    return next((user for user in users if user.login == match.login), None)


def _is_yes(value: str) -> bool:
    return value.strip().casefold() in {"да", "yes", "y", "верно", "это я"}


def _is_no(value: str) -> bool:
    return value.strip().casefold() in {"нет", "no", "n", "не я"}


async def confirm_tracker_identity(
    session: AsyncSession,
    *,
    installation: TelegramInstallation,
    telegram_user: TelegramUser,
    onboarding: TelegramOnboardingSession,
    approved: bool,
) -> None:
    if onboarding.status != "pending" or onboarding.step_key != "confirm_tracker":
        return

    if not approved:
        onboarding.step_key = "tracker_login"
        onboarding.answers_json = {}
        _enqueue_message(
            session,
            installation=installation,
            target_chat_id=telegram_user.external_user_id,
            text="Хорошо. Отправьте другой логин, почту или имя в Tracker.",
            category="authorization",
            dedupe_key=f"telegram:onboarding:confirm-no:{onboarding.id}:{onboarding.attempts}",
        )
        await session.flush()
        return

    onboarding.step_key = "default_board"
    _enqueue_message(
        session,
        installation=installation,
        target_chat_id=telegram_user.external_user_id,
        text="Теперь отправьте ID или точное название вашей основной доски Tracker.",
        category="authorization",
        dedupe_key=f"telegram:onboarding:board:{onboarding.id}",
    )
    await session.flush()


def _find_board(value: str, boards: list[dict]) -> dict | None:
    normalized = value.strip().casefold()
    return next(
        (
            board
            for board in boards
            if str(board.get("id", "")).casefold() == normalized
            or str(board.get("name", "")).casefold() == normalized
        ),
        None,
    )


async def complete_onboarding(
    session: AsyncSession,
    *,
    installation: TelegramInstallation,
    telegram_user: TelegramUser,
    answer: str,
) -> TeamMembership | None:
    onboarding = await _pending_session(
        session,
        team_id=installation.team_id,
        telegram_user_id=telegram_user.id,
    )
    if onboarding is None:
        await start_onboarding(
            session,
            installation=installation,
            telegram_user=telegram_user,
            source_chat_id=telegram_user.external_user_id,
            source_message_id=None,
            source_is_private=True,
        )
        return None

    onboarding.attempts += 1

    if onboarding.step_key == "tracker_login":
        team = await session.get(Team, installation.team_id)
        queue = team.tracker_queue if team is not None else ""
        async with TrackerClient() as client:
            users = await load_team_users(client, queue)
        tracker_user = _suggest_tracker_user(answer, users)

        if tracker_user is None:
            _enqueue_message(
                session,
                installation=installation,
                target_chat_id=telegram_user.external_user_id,
                text=(
                    "Не удалось найти похожего участника очереди Tracker. "
                    "Отправьте логин, почту или имя еще раз."
                ),
                category="authorization",
                dedupe_key=f"telegram:onboarding:retry:{onboarding.id}:{onboarding.attempts}",
            )
            await session.flush()
            return None

        existing_membership = (
            await session.execute(
                select(TeamMembership).where(
                    TeamMembership.team_id == installation.team_id,
                    TeamMembership.tracker_login == tracker_user.login,
                )
            )
        ).scalar_one_or_none()
        if existing_membership is not None:
            _enqueue_message(
                session,
                installation=installation,
                target_chat_id=telegram_user.external_user_id,
                text="Этот профиль Tracker уже привязан. Обратитесь к администратору команды.",
                category="authorization",
                dedupe_key=f"telegram:onboarding:conflict:{onboarding.id}",
            )
            await session.flush()
            return None

        onboarding.step_key = "confirm_tracker"
        onboarding.answers_json = {
            "tracker_login": tracker_user.login,
            "tracker_display_name": tracker_user.display,
            "tracker_email": tracker_user.email,
        }
        callback_tokens: list[tuple[str, bool]] = [
            (secrets.token_urlsafe(32), True),
            (secrets.token_urlsafe(32), False),
        ]
        for raw_token, approved in callback_tokens:
            session.add(
                TelegramCallbackToken(
                    team_id=installation.team_id,
                    installation_id=installation.id,
                    telegram_user_id=telegram_user.id,
                    confirm_id=None,
                    token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
                    target_chat_id=telegram_user.external_user_id,
                    target_user_id=telegram_user.external_user_id,
                    status="pending",
                    payload={
                        "action": "onboarding_identity",
                        "onboarding_id": str(onboarding.id),
                        "approved": approved,
                    },
                    expires_at=onboarding.expires_at,
                )
            )
        _enqueue_message(
            session,
            installation=installation,
            target_chat_id=telegram_user.external_user_id,
            text=(
                "Нашел профиль:\n"
                f"Имя: {tracker_user.display}\n"
                f"Логин: {tracker_user.login}\n"
                f"Почта: {tracker_user.email or 'не указана'}\n"
                "Это вы? Ответьте «да» или «нет»."
            ),
            category="authorization",
            dedupe_key=f"telegram:onboarding:confirm:{onboarding.id}:{onboarding.attempts}",
            reply_markup={
                "inline_keyboard": [
                    [
                        {
                            "text": "Да, это я",
                            "callback_data": callback_tokens[0][0],
                        },
                        {
                            "text": "Нет, другой профиль",
                            "callback_data": callback_tokens[1][0],
                        },
                    ]
                ]
            },
        )
        await session.flush()
        return None

    if onboarding.step_key == "confirm_tracker":
        if _is_no(answer):
            await confirm_tracker_identity(
                session,
                installation=installation,
                telegram_user=telegram_user,
                onboarding=onboarding,
                approved=False,
            )
            return None
        if not _is_yes(answer):
            _enqueue_message(
                session,
                installation=installation,
                target_chat_id=telegram_user.external_user_id,
                text="Пожалуйста, ответьте «да» или «нет».",
                category="authorization",
                dedupe_key=(
                    f"telegram:onboarding:confirm-retry:{onboarding.id}:{onboarding.attempts}"
                ),
            )
            await session.flush()
            return None

        await confirm_tracker_identity(
            session,
            installation=installation,
            telegram_user=telegram_user,
            onboarding=onboarding,
            approved=True,
        )
        return None

    if onboarding.step_key != "default_board":
        return None

    async with TrackerClient() as client:
        boards = await client.list_boards()
    board = _find_board(answer, boards)
    if board is None:
        _enqueue_message(
            session,
            installation=installation,
            target_chat_id=telegram_user.external_user_id,
            text="Доска не найдена. Отправьте ее точное название или числовой ID.",
            category="authorization",
            dedupe_key=f"telegram:onboarding:board-retry:{onboarding.id}:{onboarding.attempts}",
        )
        await session.flush()
        return None

    answers = onboarding.answers_json or {}
    tracker_login = str(answers["tracker_login"])
    tracker_display_name = str(answers.get("tracker_display_name") or tracker_login)
    tracker_email = str(answers.get("tracker_email") or "")
    board_id = str(board["id"])
    board_name = str(board.get("name") or board_id)

    email = tracker_email.strip().lower() or (
        f"telegram-{telegram_user.external_user_id}@telegram.local"
    )
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None:
        user = User(
            email=email,
            password_hash="!telegram-code-only",
            display_name=tracker_display_name,
            role="user",
            active=True,
        )
        session.add(user)
        await session.flush()
    else:
        user_membership = (
            await session.execute(
                select(TeamMembership).where(
                    TeamMembership.team_id == installation.team_id,
                    TeamMembership.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if user_membership is not None:
            _enqueue_message(
                session,
                installation=installation,
                target_chat_id=telegram_user.external_user_id,
                text="Для этой учетной записи уже настроен другой профиль Tracker.",
                category="authorization",
                dedupe_key=f"telegram:onboarding:user-conflict:{onboarding.id}",
            )
            await session.flush()
            return None

    link = (
        await session.execute(
            select(TelegramUserLink).where(
                TelegramUserLink.team_id == installation.team_id,
                TelegramUserLink.telegram_user_id == telegram_user.id,
            )
        )
    ).scalar_one_or_none()
    if link is None:
        link = TelegramUserLink(
            team_id=installation.team_id,
            installation_id=installation.id,
            telegram_user_id=telegram_user.id,
            user_id=user.id,
            status="active",
            metadata_json={},
        )
        session.add(link)
    else:
        link.user_id = user.id
        link.installation_id = installation.id
        link.status = "active"

    membership = TeamMembership(
        team_id=installation.team_id,
        user_id=user.id,
        tracker_login=tracker_login,
        tracker_display_name=tracker_display_name,
        tracker_match_status="confirmed",
        default_board_id=board_id,
        role="user",
        settings_json={"default_board_name": board_name},
    )
    session.add(membership)
    onboarding.status = "completed"
    onboarding.step_key = "completed"
    onboarding.answers_json = {
        **answers,
        "default_board_id": board_id,
        "default_board_name": board_name,
    }
    onboarding.completed_at = datetime.now(timezone.utc)
    _enqueue_message(
        session,
        installation=installation,
        target_chat_id=telegram_user.external_user_id,
        text=(
            f"Профиль привязан: {tracker_display_name} ({tracker_login}), "
            f"основная доска: {board_name}. "
            "Теперь можно обращаться к боту и входить в UI по коду из Telegram."
        ),
        category="authorization",
        dedupe_key=f"telegram:onboarding:completed:{onboarding.id}",
    )
    await session.flush()
    return membership


__all__ = [
    "complete_onboarding",
    "confirm_tracker_identity",
    "get_confirmed_membership",
    "start_onboarding",
]
