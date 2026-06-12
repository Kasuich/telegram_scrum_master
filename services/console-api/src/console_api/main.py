"""Console API — BFF/read-model service for the PM Agent Platform GUI."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qsl

import httpx
from core import board_metrics
from core import pet as pet_lib
from core.config import get_config
from core.cron_schedule import cron_to_schedule, describe_cron, schedule_to_cron
from core.db import create_all_tables, get_session
from core.models import (
    Action,
    ActionFeedback,
    AgentInstance,
    AgentSpec,
    Confirm,
    ConsoleSession,
    LoginChallenge,
    PetBattle,
    PetState,
    ScheduledJob,
    Team,
    TeamMembership,
    TelegramInstallation,
    TelegramOutbox,
    TelegramUser,
    TelegramUserLink,
    Trace,
    User,
    UserProfile,
)
from core.scheduler import compute_next_run
from core.seed import ensure_default_team
from core.tracker import TrackerClient, TrackerError
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from console_api.security import (
    hash_login_code,
    hash_password,
    hash_session_token,
    new_login_code,
    new_session_token,
    verify_password,
)

RiskLevel = Literal["low", "medium", "high"]
ActionStatus = Literal["pending", "completed", "failed"]
ConfirmStatus = Literal["pending", "approved", "rejected"]
ConsoleRole = Literal["dev", "admin", "user"]

SESSION_COOKIE = "console_session"
DEFAULT_MODEL = "google/gemini-3.1-flash-lite"
DEFAULT_TEAM_ID = "00000000-0000-0000-0000-000000000001"


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def _platform_api_url() -> str:
    return os.getenv("PLATFORM_API_URL", "http://platform-api:8000").rstrip("/")


def _default_team_id() -> uuid.UUID:
    cfg_team_id = get_config().app.default_team_id
    return uuid.UUID(os.getenv("DEFAULT_TEAM_ID") or cfg_team_id or DEFAULT_TEAM_ID)


def _session_ttl() -> timedelta:
    hours = int(os.getenv("CONSOLE_SESSION_TTL_HOURS", "24"))
    return timedelta(hours=hours)


def _login_code_secret() -> str:
    return os.getenv("CONSOLE_LOGIN_CODE_SECRET") or os.getenv(
        "TELEGRAM_BRIDGE_SECRET",
        "dev-login-code-secret-change-me",
    )


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        httponly=True,
        samesite="lax",
        secure=os.getenv("CONSOLE_SECURE_COOKIES", "false").lower() == "true",
        max_age=int(_session_ttl().total_seconds()),
    )


def _set_webapp_session_cookie(response: Response, raw_token: str) -> None:
    """Session cookie for the Telegram Mini App (loaded inside the TG webview).

    Production (HTTPS) needs ``SameSite=None; Secure`` so the cookie survives the
    webview's iframe context; local dev (HTTP) falls back to Lax.
    """
    secure = os.getenv("CONSOLE_SECURE_COOKIES", "false").lower() == "true"
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        httponly=True,
        samesite="none" if secure else "lax",
        secure=secure,
        max_age=int(_session_ttl().total_seconds()),
    )


def _telegram_bot_token() -> str | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    return token or None


def _webapp_dev_mode() -> bool:
    return os.getenv("TG_WEBAPP_DEV", "false").strip().lower() in ("1", "true", "yes")


def _cors_origins() -> list[str]:
    raw = os.getenv("CONSOLE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [item.strip() for item in raw.split(",") if item.strip()]


# Avatars are stored on a local volume (see CONSOLE_AVATAR_DIR / docker-compose).
AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
}


def _avatar_dir() -> Path:
    path = Path(os.getenv("CONSOLE_AVATAR_DIR", "/data/avatars"))
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _ensure_default_console_user(session: AsyncSession) -> None:
    email = os.getenv("CONSOLE_ADMIN_EMAIL", "admin@example.com").lower()
    password = os.getenv("CONSOLE_ADMIN_PASSWORD", "admin")
    display_name = os.getenv("CONSOLE_ADMIN_NAME", "Console Admin")

    row = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if row is None:
        session.add(
            User(
                id=uuid.uuid4(),
                email=email,
                password_hash=hash_password(password),
                display_name=display_name,
                role="admin",
                active=True,
            )
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await create_all_tables()
    async with get_session() as session:
        await ensure_default_team(session, str(_default_team_id()))
        await _ensure_default_console_user(session)
    yield


app = FastAPI(title="PM Agent Platform Console API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


UiRole = Literal["developer", "teamlead", "user"]


class UserDTO(BaseModel):
    id: str
    email: str
    display_name: str
    role: ConsoleRole
    ui_role: UiRole = "user"
    team_id: str | None = None
    team_role: str | None = None
    tracker_login: str | None = None
    default_board_id: str | None = None


class ContactDTO(BaseModel):
    type: str = Field(min_length=1, max_length=32)
    value: str = Field(min_length=1, max_length=255)
    label: str | None = Field(default=None, max_length=64)


class ProfileDTO(BaseModel):
    user_id: str
    display_name: str
    ui_role: UiRole
    title: str | None = None
    bio: str | None = None
    contacts: list[ContactDTO] = Field(default_factory=list)
    avatar_url: str | None = None
    is_self: bool
    # Owner-only fields — omitted in the public view.
    email: str | None = None
    team_role: str | None = None
    tracker_login: str | None = None
    private: dict[str, Any] | None = None


class PatchProfileRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    bio: str | None = Field(default=None, max_length=4000)
    contacts: list[ContactDTO] | None = Field(default=None, max_length=20)
    private: dict[str, Any] | None = None


class BoardIssueDTO(BaseModel):
    key: str
    summary: str
    status: str
    status_key: str
    deadline: str | None = None
    overdue: bool = False
    updated_at: str | None = None


class BoardColumnDTO(BaseModel):
    status: str
    issues: list[BoardIssueDTO]


class BoardDTO(BaseModel):
    available: bool
    queue: str | None = None
    tracker_login: str | None = None
    total: int = 0
    columns: list[BoardColumnDTO] = Field(default_factory=list)
    note: str | None = None


class StatsDTO(BaseModel):
    available: bool
    window_days: int
    tracker_login: str | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    throughput: list[dict[str, Any]] = Field(default_factory=list)
    status_distribution: list[dict[str, Any]] = Field(default_factory=list)
    lead_time: dict[str, Any] = Field(default_factory=dict)
    note: str | None = None


class PetSpeciesDTO(BaseModel):
    id: str
    name: str
    rarity: str
    rarity_rank: int = 0
    desc: str = ""


class PetDTO(BaseModel):
    available: bool
    level: int = 1
    xp: int = 0
    xp_into_level: int = 0
    xp_for_next: int = 1
    progress: float = 0.0
    mood: int = 100
    tier: int = 0
    tier_name: str = "Яйцо"
    species: PetSpeciesDTO | None = None
    stats: dict[str, int] = Field(default_factory=dict)
    stat_labels: dict[str, str] = Field(default_factory=dict)
    coins: int = 0
    equipped: dict[str, str] = Field(default_factory=dict)
    owner_name: str | None = None
    note: str | None = None


class ShopItemDTO(BaseModel):
    id: str
    name: str
    slot: str
    rarity: str
    price: int
    owned: bool = False
    equipped: bool = False
    affordable: bool = False


class ShopDTO(BaseModel):
    coins: int = 0
    earned: int = 0
    spent: int = 0
    equipped: dict[str, str] = Field(default_factory=dict)
    items: list[ShopItemDTO] = Field(default_factory=list)


class ScheduleDTO(BaseModel):
    preset: Literal["daily", "weekdays", "weekly"]
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    days: list[int] | None = None


class ScheduledJobDTO(BaseModel):
    id: str
    agent_name: str | None
    name: str
    cron_expr: str
    schedule: dict[str, Any]
    human: str
    payload_type: str | None = None
    enabled: bool
    run_count: int
    max_runs: int | None
    next_run: str | None
    created_at: str


class PatchScheduledJobRequest(BaseModel):
    enabled: bool | None = None
    schedule: ScheduleDTO | None = None


class TeamMemberDTO(BaseModel):
    user_id: str
    display_name: str
    tracker_login: str | None = None
    role: str
    avatar_url: str | None = None


class TeamHealthDTO(BaseModel):
    available: bool
    window_days: int
    health_index: int | None = None
    breakdown: list[dict[str, Any]] = Field(default_factory=list)
    drags: list[str] = Field(default_factory=list)
    totals: dict[str, int] = Field(default_factory=dict)
    throughput: list[dict[str, Any]] = Field(default_factory=list)
    members: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    user: UserDTO


class CodeLoginRequest(BaseModel):
    identifier: str = Field(min_length=1, max_length=255)


class CodeLoginChallengeResponse(BaseModel):
    challenge_id: str
    expires_in_seconds: int = 300


class CodeLoginVerifyRequest(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(pattern=r"^\d{6}$")


class AutonomyDTO(BaseModel):
    auto_risk: list[RiskLevel] = Field(default_factory=lambda: ["low"])
    confirm_risk: list[RiskLevel] = Field(default_factory=lambda: ["medium", "high"])
    always_confirm_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_risk_groups_do_not_overlap(self) -> AutonomyDTO:
        overlap = sorted(set(self.auto_risk) & set(self.confirm_risk))
        if overlap:
            raise ValueError(
                "auto_risk and confirm_risk must not contain the same risk levels: "
                + ", ".join(overlap)
            )
        return self


class AgentListItem(BaseModel):
    name: str
    description: str = ""
    enabled: bool
    has_spec: bool
    model: str
    updated_at: str | None = None


class AgentConfigDTO(BaseModel):
    name: str
    description: str = ""
    enabled: bool
    model: str
    prompt: str
    autonomy: AutonomyDTO
    spec_prompt: str
    overlay: dict[str, Any] = Field(default_factory=dict)
    has_spec: bool


class PatchSpecRequest(BaseModel):
    prompt: str | None = None
    model: str | None = None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str | None) -> str | None:
        if value is not None and len(value) > 40_000:
            raise ValueError("prompt is too long")
        return value


class PatchOverlayRequest(BaseModel):
    enabled: bool | None = None
    autonomy: AutonomyDTO | None = None


class AgentToolDTO(BaseModel):
    name: str
    description: str = ""
    risk: RiskLevel
    enabled: bool = True
    # Per-tool confirm override: True=always confirm, False=auto-run, None=by risk.
    confirm: bool | None = None


class ToolOverrideDTO(BaseModel):
    name: str
    enabled: bool = True
    confirm: bool | None = None


class PatchAgentToolsRequest(BaseModel):
    tools: list[ToolOverrideDTO] = Field(default_factory=list)


class UserSummaryDTO(BaseModel):
    user_id: str
    display_name: str
    email: str
    role: ConsoleRole
    ui_role: UiRole
    team_role: str | None = None
    tracker_login: str | None = None
    avatar_url: str | None = None


class ActionListItem(BaseModel):
    id: str
    created_at: str
    agent_name: str | None
    tool_name: str
    risk_level: RiskLevel
    status: ActionStatus
    trace_id: str | None
    error: str | None


class TraceDTO(BaseModel):
    id: str
    session_id: str
    steps: list[dict[str, Any]]
    metadata_json: dict[str, Any] | None
    created_at: str


class ConfirmDTO(BaseModel):
    id: str
    action_id: str
    prompt: str
    status: ConfirmStatus
    answer: str | None
    created_at: str
    responded_at: str | None


class FeedbackDTO(BaseModel):
    id: str
    action_id: str
    user_id: str | None
    rating: int
    comment: str | None
    created_at: str


class ActionDetailDTO(BaseModel):
    action: ActionListItem
    input: dict[str, Any]
    output: dict[str, Any] | None
    trace: TraceDTO | None
    confirms: list[ConfirmDTO]
    feedback: list[FeedbackDTO]


class FeedbackRequest(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class ConfirmDecisionRequest(BaseModel):
    approved: bool


class PlaygroundChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4096)
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


async def _primary_membership(
    session: AsyncSession, user: User
) -> TeamMembership | None:
    """Pick the user's primary confirmed team membership.

    Prefers the default team, otherwise the first confirmed membership.
    """
    rows = (
        await session.execute(
            select(TeamMembership)
            .where(
                TeamMembership.user_id == user.id,
                TeamMembership.tracker_match_status == "confirmed",
            )
            .order_by(TeamMembership.created_at)
        )
    ).scalars().all()
    if not rows:
        return None
    default_team = _default_team_id()
    for membership in rows:
        if membership.team_id == default_team:
            return membership
    return rows[0]


def _resolve_ui_role(user: User, membership: TeamMembership | None) -> UiRole:
    if user.role in ("dev", "admin"):
        return "developer"
    if membership is not None and membership.role == "lead":
        return "teamlead"
    return "user"


async def _build_user_dto(session: AsyncSession, user: User) -> UserDTO:
    membership = await _primary_membership(session, user)
    # Developers without a membership still operate on the default team.
    team_id: str | None = str(membership.team_id) if membership else None
    if team_id is None and user.role in ("dev", "admin"):
        team_id = str(_default_team_id())
    return UserDTO(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,  # type: ignore[arg-type]
        ui_role=_resolve_ui_role(user, membership),
        team_id=team_id,
        team_role=membership.role if membership else None,
        tracker_login=membership.tracker_login if membership else None,
        default_board_id=membership.default_board_id if membership else None,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def current_user(
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> User:
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    async with get_session() as session:
        stmt = (
            select(ConsoleSession, User)
            .join(User, ConsoleSession.user_id == User.id)
            .where(ConsoleSession.token_hash == hash_session_token(token))
        )
        row = (await session.execute(stmt)).one_or_none()
        if row is None:
            raise HTTPException(status_code=401, detail="Invalid session")

        console_session, user = row
        now = datetime.now(timezone.utc)
        if (
            console_session.revoked_at is not None
            or _aware(console_session.expires_at) <= now
            or not user.active
        ):
            raise HTTPException(status_code=401, detail="Expired session")

        return user


def require_roles(*roles: ConsoleRole):
    async def _dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return _dependency


async def current_teamlead(user: User = Depends(current_user)) -> User:
    """Allow team leads and developers (developers manage every team)."""
    async with get_session() as session:
        membership = await _primary_membership(session, user)
        if _resolve_ui_role(user, membership) not in ("developer", "teamlead"):
            raise HTTPException(status_code=403, detail="Insufficient role")
    return user


async def _assert_team_access(
    session: AsyncSession, user: User, team_id: uuid.UUID
) -> None:
    """Developers manage any team; a lead only their own."""
    if user.role in ("dev", "admin"):
        return
    membership = await _primary_membership(session, user)
    if membership is None or membership.team_id != team_id or membership.role != "lead":
        raise HTTPException(status_code=403, detail="Недостаточно прав для этой команды")


@app.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest, response: Response) -> LoginResponse:
    async with get_session() as session:
        user = (
            await session.execute(select(User).where(User.email == request.email.lower()))
        ).scalar_one_or_none()
        invalid_password = user is None or not verify_password(
            request.password,
            user.password_hash if user else "",
        )
        if user is None or not user.active or invalid_password:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        raw_token = new_session_token()
        session.add(
            ConsoleSession(
                id=uuid.uuid4(),
                user_id=user.id,
                token_hash=hash_session_token(raw_token),
                expires_at=datetime.now(timezone.utc) + _session_ttl(),
            )
        )
        user_dto = await _build_user_dto(session, user)

    _set_session_cookie(response, raw_token)
    return LoginResponse(user=user_dto)


@app.post("/auth/code/request", response_model=CodeLoginChallengeResponse)
async def request_login_code(
    payload: CodeLoginRequest,
    request: Request,
) -> CodeLoginChallengeResponse:
    challenge_id = uuid.uuid4()
    identifier = payload.identifier.strip().lstrip("@").lower()
    now = datetime.now(timezone.utc)

    async with get_session() as session:
        stmt = (
            select(User, TelegramUser, TelegramInstallation)
            .join(TelegramUserLink, TelegramUserLink.user_id == User.id)
            .join(TelegramUser, TelegramUser.id == TelegramUserLink.telegram_user_id)
            .join(
                TeamMembership,
                (TeamMembership.team_id == TelegramUserLink.team_id)
                & (TeamMembership.user_id == User.id),
            )
            .join(
                TelegramInstallation,
                TelegramInstallation.id == TelegramUserLink.installation_id,
            )
            .where(
                User.active.is_(True),
                TelegramUserLink.status == "active",
                TeamMembership.tracker_match_status == "confirmed",
                (User.email == identifier) | (TelegramUser.username.ilike(identifier)),
            )
        )
        row = (await session.execute(stmt)).first()
        if row is None:
            return CodeLoginChallengeResponse(challenge_id=str(challenge_id))

        user, telegram_user, installation = row
        recent = (
            (
                await session.execute(
                    select(LoginChallenge).where(
                        LoginChallenge.user_id == user.id,
                        LoginChallenge.created_at >= now - timedelta(minutes=10),
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(recent) >= 3:
            return CodeLoginChallengeResponse(challenge_id=str(challenge_id))

        pending = (
            await session.execute(
                select(LoginChallenge).where(
                    LoginChallenge.user_id == user.id,
                    LoginChallenge.status == "pending",
                )
            )
        ).scalars()
        for old_challenge in pending:
            old_challenge.status = "superseded"

        code = new_login_code()
        challenge = LoginChallenge(
            id=challenge_id,
            team_id=installation.team_id,
            user_id=user.id,
            telegram_user_id=telegram_user.id,
            installation_id=installation.id,
            code_hash=hash_login_code(str(challenge_id), code, _login_code_secret()),
            status="pending",
            attempts=0,
            expires_at=now + timedelta(minutes=5),
            request_ip=request.client.host if request.client else None,
        )
        session.add(challenge)
        session.add(
            TelegramOutbox(
                team_id=installation.team_id,
                installation_id=installation.id,
                category="login_code",
                target_chat_id=telegram_user.external_user_id,
                target_user_id=telegram_user.external_user_id,
                dedupe_key=f"telegram:login-code:{challenge_id}",
                priority=120,
                status="pending",
                attempts=0,
                payload={
                    "method": "sendMessage",
                    "text": (
                        f"Код для входа в UI: {code}\n"
                        "Код действует 5 минут. Никому его не сообщайте."
                    ),
                },
            )
        )

    return CodeLoginChallengeResponse(challenge_id=str(challenge_id))


@app.post("/auth/code/verify", response_model=LoginResponse)
async def verify_login_code(
    payload: CodeLoginVerifyRequest,
    response: Response,
) -> LoginResponse:
    now = datetime.now(timezone.utc)
    async with get_session() as session:
        challenge = (
            await session.execute(
                select(LoginChallenge)
                .where(LoginChallenge.id == payload.challenge_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if challenge is None or challenge.status != "pending":
            raise HTTPException(status_code=401, detail="Invalid or expired code")
        if challenge.expires_at <= now:
            challenge.status = "expired"
            await session.commit()
            raise HTTPException(status_code=401, detail="Invalid or expired code")
        if challenge.attempts >= 5:
            challenge.status = "locked"
            await session.commit()
            raise HTTPException(status_code=401, detail="Invalid or expired code")

        challenge.attempts += 1
        expected = hash_login_code(
            str(challenge.id),
            payload.code,
            _login_code_secret(),
        )
        if not hmac.compare_digest(expected, challenge.code_hash):
            if challenge.attempts >= 5:
                challenge.status = "locked"
            await session.commit()
            raise HTTPException(status_code=401, detail="Invalid or expired code")

        user = await session.get(User, challenge.user_id)
        if user is None or not user.active:
            raise HTTPException(status_code=401, detail="Invalid or expired code")

        challenge.status = "used"
        challenge.consumed_at = now
        raw_token = new_session_token()
        session.add(
            ConsoleSession(
                id=uuid.uuid4(),
                user_id=user.id,
                token_hash=hash_session_token(raw_token),
                expires_at=now + _session_ttl(),
            )
        )
        user_dto = await _build_user_dto(session, user)

    _set_session_cookie(response, raw_token)
    return LoginResponse(user=user_dto)


@app.post("/auth/logout")
async def logout(
    response: Response,
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, bool]:
    if token:
        async with get_session() as session:
            row = (
                await session.execute(
                    select(ConsoleSession).where(
                        ConsoleSession.token_hash == hash_session_token(token)
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                row.revoked_at = datetime.now(timezone.utc)

    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/auth/me", response_model=UserDTO)
async def me(user: User = Depends(current_user)) -> UserDTO:
    async with get_session() as session:
        return await _build_user_dto(session, user)


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


def _avatar_url(user_id: uuid.UUID, profile: UserProfile | None) -> str | None:
    if profile is None or not profile.avatar_path:
        return None
    return f"/users/{user_id}/avatar"


def _contacts_from_profile(profile: UserProfile | None) -> list[ContactDTO]:
    if profile is None:
        return []
    return [ContactDTO(**item) for item in (profile.contacts_json or [])]


async def _get_profile(session: AsyncSession, user_id: uuid.UUID) -> UserProfile | None:
    return await session.get(UserProfile, user_id)


async def _profile_dto(
    session: AsyncSession, user: User, *, is_self: bool
) -> ProfileDTO:
    profile = await _get_profile(session, user.id)
    membership = await _primary_membership(session, user)
    dto = ProfileDTO(
        user_id=str(user.id),
        display_name=user.display_name,
        ui_role=_resolve_ui_role(user, membership),
        title=profile.title if profile else None,
        bio=profile.bio if profile else None,
        contacts=_contacts_from_profile(profile),
        avatar_url=_avatar_url(user.id, profile),
        is_self=is_self,
    )
    if is_self:
        dto.email = user.email
        dto.team_role = membership.role if membership else None
        dto.tracker_login = membership.tracker_login if membership else None
        dto.private = (profile.private_json if profile else None) or {}
    return dto


@app.get("/me/profile", response_model=ProfileDTO)
async def get_my_profile(user: User = Depends(current_user)) -> ProfileDTO:
    async with get_session() as session:
        return await _profile_dto(session, user, is_self=True)


@app.patch("/me/profile", response_model=ProfileDTO)
async def patch_my_profile(
    payload: PatchProfileRequest, user: User = Depends(current_user)
) -> ProfileDTO:
    async with get_session() as session:
        profile = await _get_profile(session, user.id)
        if profile is None:
            profile = UserProfile(user_id=user.id, contacts_json=[], private_json={})
            session.add(profile)
        if payload.title is not None:
            profile.title = payload.title or None
        if payload.bio is not None:
            profile.bio = payload.bio or None
        if payload.contacts is not None:
            profile.contacts_json = [
                item.model_dump(exclude_none=True) for item in payload.contacts
            ]
        if payload.private is not None:
            profile.private_json = payload.private
        await session.flush()
        return await _profile_dto(session, user, is_self=True)


@app.get("/users/{user_id}/profile", response_model=ProfileDTO)
async def get_user_profile(
    user_id: uuid.UUID, viewer: User = Depends(current_user)
) -> ProfileDTO:
    async with get_session() as session:
        target = await session.get(User, user_id)
        if target is None or not target.active:
            raise HTTPException(status_code=404, detail="User not found")
        return await _profile_dto(session, target, is_self=target.id == viewer.id)


@app.post("/me/avatar", response_model=ProfileDTO)
async def upload_my_avatar(
    request: Request, user: User = Depends(current_user)
) -> ProfileDTO:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    ext = AVATAR_CONTENT_TYPES.get(content_type)
    if ext is None:
        raise HTTPException(status_code=415, detail="Unsupported image type")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(body) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    directory = _avatar_dir()
    filename = f"{user.id}.{ext}"
    # Remove stale variants with a different extension.
    for other_ext in AVATAR_CONTENT_TYPES.values():
        stale = directory / f"{user.id}.{other_ext}"
        if other_ext != ext and stale.exists():
            stale.unlink()
    (directory / filename).write_bytes(body)

    async with get_session() as session:
        profile = await _get_profile(session, user.id)
        if profile is None:
            profile = UserProfile(user_id=user.id, contacts_json=[], private_json={})
            session.add(profile)
        profile.avatar_path = filename
        await session.flush()
        return await _profile_dto(session, user, is_self=True)


@app.get("/users/{user_id}/avatar")
async def get_user_avatar(
    user_id: uuid.UUID, _: User = Depends(current_user)
) -> FileResponse:
    async with get_session() as session:
        profile = await _get_profile(session, user_id)
    if profile is None or not profile.avatar_path:
        raise HTTPException(status_code=404, detail="No avatar")
    file_path = _avatar_dir() / profile.avatar_path
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="No avatar")
    return FileResponse(file_path)


# ---------------------------------------------------------------------------
# Personal board & stats (Tracker-backed)
# ---------------------------------------------------------------------------


async def _team_queue(session: AsyncSession, team_id: uuid.UUID) -> str | None:
    team = await session.get(Team, team_id)
    return team.tracker_queue if team else None


def _yql_login(login: str) -> str:
    return login.replace('"', "")


async def _fetch_assignee_issues(
    login: str, queue: str | None, *, since_date: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    safe = _yql_login(login)
    async with TrackerClient() as client:
        open_issues = await client.search_all_issues(
            f'Assignee: "{safe}" AND Resolution: empty()', queue=queue
        )
        resolved: list[dict[str, Any]] = []
        if since_date is not None:
            resolved = await client.search_all_issues(
                f'Assignee: "{safe}" AND Resolved: >= "{since_date}"', queue=queue
            )
    return open_issues, resolved


@app.get("/me/board", response_model=BoardDTO)
async def my_board(user: User = Depends(current_user)) -> BoardDTO:
    async with get_session() as session:
        membership = await _primary_membership(session, user)
        if membership is None or not membership.tracker_login:
            return BoardDTO(available=False, note="Tracker-логин не привязан")
        queue = await _team_queue(session, membership.team_id)
        login = membership.tracker_login

    try:
        open_issues, _ = await _fetch_assignee_issues(login, queue)
    except TrackerError as exc:
        return BoardDTO(available=False, queue=queue, tracker_login=login, note=str(exc))
    except Exception:  # noqa: BLE001 — Tracker is best-effort here
        return BoardDTO(
            available=False, queue=queue, tracker_login=login, note="Tracker недоступен"
        )

    now = datetime.now(timezone.utc)
    order: list[str] = []
    groups: dict[str, list[BoardIssueDTO]] = {}
    for issue in open_issues:
        status = board_metrics.status_display(issue)
        if status not in groups:
            groups[status] = []
            order.append(status)
        groups[status].append(
            BoardIssueDTO(
                key=str(issue.get("key", "")),
                summary=str(issue.get("summary", "")),
                status=status,
                status_key=board_metrics.status_key(issue),
                deadline=issue.get("deadline"),
                overdue=board_metrics.is_overdue(issue, now=now),
                updated_at=issue.get("updatedAt"),
            )
        )
    columns = [BoardColumnDTO(status=status, issues=groups[status]) for status in order]
    return BoardDTO(
        available=True,
        queue=queue,
        tracker_login=login,
        total=len(open_issues),
        columns=columns,
    )


@app.get("/me/stats", response_model=StatsDTO)
async def my_stats(
    window: int = Query(14, ge=1, le=90), user: User = Depends(current_user)
) -> StatsDTO:
    async with get_session() as session:
        membership = await _primary_membership(session, user)
        if membership is None or not membership.tracker_login:
            return StatsDTO(available=False, window_days=window, note="Tracker-логин не привязан")
        queue = await _team_queue(session, membership.team_id)
        login = membership.tracker_login

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=window)).date().isoformat()
    try:
        open_issues, resolved = await _fetch_assignee_issues(login, queue, since_date=since)
    except TrackerError as exc:
        return StatsDTO(
            available=False, window_days=window, tracker_login=login, note=str(exc)
        )
    except Exception:  # noqa: BLE001 — Tracker is best-effort here
        return StatsDTO(
            available=False,
            window_days=window,
            tracker_login=login,
            note="Tracker недоступен",
        )

    stats = board_metrics.personal_stats(open_issues, resolved, window_days=window, now=now)
    return StatsDTO(
        available=True,
        window_days=window,
        tracker_login=login,
        counts=stats["counts"],
        throughput=stats["throughput"],
        status_distribution=stats["status_distribution"],
        lead_time=stats["lead_time"],
    )


def _pet_dev_tools_enabled() -> bool:
    return os.getenv("PET_DEV_TOOLS", "false").strip().lower() in ("1", "true", "yes")


def _pet_dto_from_state(state: PetState, *, owner_name: str | None) -> PetDTO:
    """Build a PetDTO from the persisted snapshot (no Tracker call)."""
    snap = dict(state.state_json or {})
    cos = snap.pop("_cosmetics", {}) or {}
    earned = int(cos.get("coins_earned", 0))
    spent = int(cos.get("coins_spent", 0))
    return PetDTO(
        available=True,
        owner_name=owner_name,
        stat_labels=pet_lib.stat_labels(),
        coins=max(0, earned - spent),
        equipped=cos.get("equipped", {}) or {},
        **snap,
    )


async def _recalc_pet(target: User) -> PetDTO:
    """«Скрамик» — recompute pet from the board and persist a snapshot.

    XP is the *lifetime* closed-issue count (monotonic). A dev-granted XP override
    stored on the row is honoured if it exceeds real progress. Species is rolled
    once (deterministically) and frozen. Cosmetics (owned/equipped/coins) are
    preserved across recalcs; only ``coins_earned`` is recomputed.
    """
    async with get_session() as session:
        membership = await _primary_membership(session, target)
        if membership is None or not membership.tracker_login:
            return PetDTO(available=False, note="Tracker-логин не привязан")
        queue = await _team_queue(session, membership.team_id)
        login = membership.tracker_login

    now = datetime.now(timezone.utc)
    try:
        # Far-back date → lifetime closed-issue count (idempotent XP source).
        open_issues, resolved = await _fetch_assignee_issues(
            login, queue, since_date="2000-01-01"
        )
    except Exception:  # noqa: BLE001 — Tracker is best-effort
        return PetDTO(available=False, note="Tracker недоступен")

    counts = board_metrics.personal_stats(open_issues, resolved, window_days=30, now=now)["counts"]
    lifetime_xp = counts["resolved"] * pet_lib.xp_per_resolved()

    async with get_session() as session:
        state = await session.get(PetState, target.id)
        if state is None:
            state = PetState(user_id=target.id)
            session.add(state)
        prev_cos = dict((state.state_json or {}).get("_cosmetics", {}) or {})
        species_id = state.species_id or pet_lib.roll_species(str(target.id))
        effective_xp = max(lifetime_xp, state.xp or 0)  # honour dev grants
        snapshot = pet_lib.snapshot_from_xp(
            xp=effective_xp,
            resolved=counts["resolved"],
            overdue=counts["overdue"],
            in_progress=counts["in_progress"],
            streak_days=state.streak_days,
            species_id=species_id,
        )
        prev_cos["coins_earned"] = pet_lib.coins_earned(
            lifetime_resolved=counts["resolved"], level=snapshot["level"]
        )
        prev_cos.setdefault("coins_spent", 0)
        prev_cos.setdefault("owned", [])
        prev_cos.setdefault("equipped", {})
        state.species_id = species_id
        state.xp = effective_xp
        state.level = snapshot["level"]
        state.mood = snapshot["mood"]
        state.evolution_tier = snapshot["tier"]
        state.state_json = {**snapshot, "_cosmetics": prev_cos}
        state.last_recalc_at = now
        return _pet_dto_from_state(state, owner_name=target.display_name)


@app.get("/me/pet", response_model=PetDTO)
async def my_pet(user: User = Depends(current_user)) -> PetDTO:
    return await _recalc_pet(user)


@app.get("/users/{user_id}/pet", response_model=PetDTO)
async def get_user_pet(
    user_id: uuid.UUID, _: User = Depends(current_user)
) -> PetDTO:
    """View another teammate's Скрамик."""
    async with get_session() as session:
        target = await session.get(User, user_id)
        if target is None or not target.active:
            raise HTTPException(status_code=404, detail="User not found")
    return await _recalc_pet(target)


class GrantXpRequest(BaseModel):
    amount: int | None = None
    level: int | None = None


@app.post("/me/pet/grant-xp", response_model=PetDTO)
async def dev_grant_xp(
    payload: GrantXpRequest, user: User = Depends(current_user)
) -> PetDTO:
    """DEV: bump XP / jump to a level for fast testing (gated by PET_DEV_TOOLS)."""
    if not _pet_dev_tools_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    async with get_session() as session:
        state = await session.get(PetState, user.id)
        if state is None:
            state = PetState(user_id=user.id, species_id=pet_lib.roll_species(str(user.id)))
            session.add(state)
        if payload.level is not None:
            state.xp = pet_lib.xp_for_level(max(1, payload.level))
        elif payload.amount is not None:
            state.xp = max(0, (state.xp or 0) + payload.amount)
    return await _recalc_pet(user)


@app.post("/me/pet/set-species", response_model=PetDTO)
async def dev_set_species(
    payload: PetSpeciesDTO, user: User = Depends(current_user)
) -> PetDTO:
    """DEV: try on any of the 10 species (gated by PET_DEV_TOOLS)."""
    if not _pet_dev_tools_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    if payload.id not in pet_lib.SPECIES_BY_ID:
        raise HTTPException(status_code=400, detail="Unknown species")
    async with get_session() as session:
        state = await session.get(PetState, user.id)
        if state is None:
            state = PetState(user_id=user.id)
            session.add(state)
        state.species_id = payload.id
    return await _recalc_pet(user)


@app.post("/me/pet/reset", response_model=PetDTO)
async def dev_reset_pet(user: User = Depends(current_user)) -> PetDTO:
    """DEV: reset pet state (gated by PET_DEV_TOOLS)."""
    if not _pet_dev_tools_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    async with get_session() as session:
        state = await session.get(PetState, user.id)
        if state is not None:
            await session.delete(state)
    return await _recalc_pet(user)


# ---------------------------------------------------------------------------
# Скрамик shop — buy cosmetics with скрамкоины earned from closed tasks
# ---------------------------------------------------------------------------


def _cosmetics_of(state: PetState | None) -> dict[str, Any]:
    return dict((state.state_json or {}).get("_cosmetics", {})) if state else {}


def _save_cosmetics(state: PetState, cos: dict[str, Any]) -> None:
    sj = dict(state.state_json or {})
    sj["_cosmetics"] = cos
    state.state_json = sj  # reassign so SQLAlchemy detects the JSONB change


class BuyRequest(BaseModel):
    item_id: str


class EquipRequest(BaseModel):
    slot: str
    item_id: str | None = None


@app.get("/me/pet/shop", response_model=ShopDTO)
async def pet_shop(user: User = Depends(current_user)) -> ShopDTO:
    pet = await _recalc_pet(user)  # refresh coins_earned from the board
    if not pet.available:
        raise HTTPException(status_code=400, detail=pet.note or "Питомец недоступен")
    async with get_session() as session:
        cos = _cosmetics_of(await session.get(PetState, user.id))
    owned = set(cos.get("owned", []))
    equipped = cos.get("equipped", {}) or {}
    earned, spent = int(cos.get("coins_earned", 0)), int(cos.get("coins_spent", 0))
    balance = max(0, earned - spent)
    items = [
        ShopItemDTO(
            **c,
            owned=c["id"] in owned,
            equipped=equipped.get(c["slot"]) == c["id"],
            affordable=balance >= c["price"],
        )
        for c in pet_lib.COSMETICS
    ]
    return ShopDTO(coins=balance, earned=earned, spent=spent, equipped=equipped, items=items)


@app.post("/me/pet/buy", response_model=PetDTO)
async def pet_buy(payload: BuyRequest, user: User = Depends(current_user)) -> PetDTO:
    item = pet_lib.COSMETICS_BY_ID.get(payload.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Предмет не найден")
    pet = await _recalc_pet(user)  # ensure state + fresh coins
    if not pet.available:
        raise HTTPException(status_code=400, detail=pet.note or "Питомец недоступен")
    async with get_session() as session:
        state = await session.get(PetState, user.id)
        cos = _cosmetics_of(state)
        owned = list(cos.get("owned", []))
        if item["id"] in owned:
            raise HTTPException(status_code=400, detail="Уже куплено")
        earned, spent = int(cos.get("coins_earned", 0)), int(cos.get("coins_spent", 0))
        if earned - spent < item["price"]:
            raise HTTPException(status_code=400, detail="Не хватает скрамкоинов")
        owned.append(item["id"])
        equipped = dict(cos.get("equipped", {}))
        equipped[item["slot"]] = item["id"]  # auto-equip on purchase
        cos.update(owned=owned, coins_spent=spent + item["price"], equipped=equipped)
        _save_cosmetics(state, cos)
        return _pet_dto_from_state(state, owner_name=user.display_name)


@app.put("/me/pet/equip", response_model=PetDTO)
async def pet_equip(payload: EquipRequest, user: User = Depends(current_user)) -> PetDTO:
    pet = await _recalc_pet(user)  # ensure the pet is initialised
    if not pet.available:
        raise HTTPException(status_code=400, detail=pet.note or "Питомец недоступен")
    async with get_session() as session:
        state = await session.get(PetState, user.id)
        if state is None:
            raise HTTPException(status_code=400, detail="Питомец недоступен")
        cos = _cosmetics_of(state)
        equipped = dict(cos.get("equipped", {}))
        if payload.item_id is None:
            equipped.pop(payload.slot, None)
        else:
            item = pet_lib.COSMETICS_BY_ID.get(payload.item_id)
            if item is None:
                raise HTTPException(status_code=404, detail="Предмет не найден")
            if item["slot"] != payload.slot:
                raise HTTPException(status_code=400, detail="Предмет не для этого слота")
            if payload.item_id not in cos.get("owned", []):
                raise HTTPException(status_code=400, detail="Предмет не куплен")
            equipped[payload.slot] = payload.item_id
        cos["equipped"] = equipped
        _save_cosmetics(state, cos)
        return _pet_dto_from_state(state, owner_name=user.display_name)


# ---------------------------------------------------------------------------
# Telegram Mini App — auth via initData + «Битва скрамиков»
# ---------------------------------------------------------------------------


class WebAppAuthRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)


def _validate_webapp_init_data(init_data: str) -> dict[str, Any]:
    """Verify Telegram WebApp ``initData`` and return the parsed ``user`` object.

    Spec: ``secret = HMAC_SHA256("WebAppData", bot_token)`` then compare
    ``HMAC_SHA256(secret, data_check_string)`` to the supplied ``hash``. In dev mode
    (``TG_WEBAPP_DEV=true``) the signature check is skipped so the page can be opened
    in a plain browser with a hand-made ``user=...`` query string.
    """
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    user_raw = pairs.get("user")
    if not user_raw:
        raise HTTPException(status_code=401, detail="initData: no user")

    if not _webapp_dev_mode():
        token = _telegram_bot_token()
        if not token:
            raise HTTPException(status_code=503, detail="Telegram bot token not configured")
        received_hash = pairs.get("hash", "")
        check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(pairs.items()) if k != "hash"
        )
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise HTTPException(status_code=401, detail="initData: bad signature")

    try:
        user_obj = json.loads(user_raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="initData: bad user json") from exc
    if not user_obj.get("id"):
        raise HTTPException(status_code=401, detail="initData: no user id")
    return user_obj


async def _resolve_linked_user(session: AsyncSession, telegram_external_id: str) -> User:
    """Map a Telegram user id → internal User via the active team link."""
    row = (
        await session.execute(
            select(User)
            .join(TelegramUserLink, TelegramUserLink.user_id == User.id)
            .join(TelegramUser, TelegramUser.id == TelegramUserLink.telegram_user_id)
            .where(
                TelegramUser.external_user_id == str(telegram_external_id),
                TelegramUserLink.status == "active",
                User.active.is_(True),
            )
            .order_by(TelegramUserLink.created_at)
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=401,
            detail="Этот Telegram не привязан к участнику. Пройдите онбординг у бота (/start).",
        )
    return row


@app.post("/auth/telegram/webapp", response_model=LoginResponse)
async def auth_telegram_webapp(payload: WebAppAuthRequest, response: Response) -> LoginResponse:
    """Authenticate a Telegram Mini App user from ``initData`` → console session."""
    tg_user = _validate_webapp_init_data(payload.init_data)
    async with get_session() as session:
        user = await _resolve_linked_user(session, str(tg_user["id"]))
        raw_token = new_session_token()
        session.add(
            ConsoleSession(
                id=uuid.uuid4(),
                user_id=user.id,
                token_hash=hash_session_token(raw_token),
                expires_at=datetime.now(timezone.utc) + _session_ttl(),
            )
        )
        user_dto = await _build_user_dto(session, user)
    _set_webapp_session_cookie(response, raw_token)
    return LoginResponse(user=user_dto)


# ---- Battle ---------------------------------------------------------------


class BattleCombatantDTO(BaseModel):
    rank: int | None = None
    user_id: str | None = None
    name: str
    species_id: str | None = None
    species_name: str = ""
    level: int = 1
    power: int = 0
    equipped: dict[str, str] = Field(default_factory=dict)


class BattleRoyaleResponse(BaseModel):
    team_name: str
    ranked: list[BattleCombatantDTO]
    winner: BattleCombatantDTO | None = None
    status_frames: list[str] = Field(default_factory=list)
    image_base64: str


class DuelResponse(BaseModel):
    winner: BattleCombatantDTO
    loser: BattleCombatantDTO
    log: list[str]
    status_frames: list[str] = Field(default_factory=list)
    image_base64: str


class DuelLeaderboardRow(BaseModel):
    user_id: str
    name: str
    wins: int = 0
    losses: int = 0
    battles: int = 0


def _combatant_dto(row: dict[str, Any]) -> BattleCombatantDTO:
    return BattleCombatantDTO(
        rank=row.get("rank"),
        user_id=row.get("user_id"),
        name=row.get("name", "—"),
        species_id=row.get("species_id"),
        species_name=row.get("species_name", ""),
        level=int(row.get("level", 1)),
        power=int(row.get("power", 0)),
        equipped=row.get("equipped", {}) or {},
    )


async def _combatant_for_user(session: AsyncSession, target: User):
    from core import pet_battle

    state = await session.get(PetState, target.id)
    return pet_battle.combatant_from_state(
        name=target.display_name or "Скрамик",
        user_id=str(target.id),
        state_json=state.state_json if state is not None else None,
        level=state.level if state is not None else None,
        species_id=state.species_id if state is not None else None,
    )


async def _team_combatants(session: AsyncSession, team_id: uuid.UUID) -> list:
    from core import pet_battle

    rows = (
        await session.execute(
            select(TeamMembership, User)
            .join(User, TeamMembership.user_id == User.id)
            .where(
                TeamMembership.team_id == team_id,
                TeamMembership.tracker_match_status == "confirmed",
                User.active.is_(True),
            )
            .order_by(User.display_name)
        )
    ).all()
    out = []
    for membership, user_row in rows:
        state = await session.get(PetState, user_row.id)
        out.append(
            pet_battle.combatant_from_state(
                name=user_row.display_name or membership.tracker_login or "Скрамик",
                user_id=str(user_row.id),
                state_json=state.state_json if state is not None else None,
                level=state.level if state is not None else None,
                species_id=state.species_id if state is not None else None,
            )
        )
    return out


async def _enqueue_battle_photo(
    session: AsyncSession, user: User, png: bytes, caption: str
) -> None:
    """Best-effort: drop the leaderboard picture into the user's private Telegram chat."""
    link_row = (
        await session.execute(
            select(TelegramUserLink, TelegramUser)
            .join(TelegramUser, TelegramUser.id == TelegramUserLink.telegram_user_id)
            .where(
                TelegramUserLink.user_id == user.id,
                TelegramUserLink.status == "active",
                TelegramUserLink.installation_id.is_not(None),
            )
            .order_by(TelegramUserLink.created_at)
        )
    ).first()
    if link_row is None:
        return
    link, tg_user = link_row
    session.add(
        TelegramOutbox(
            team_id=link.team_id,
            installation_id=link.installation_id,
            category="agent_reply",
            target_chat_id=tg_user.external_user_id,  # private chat id == user id
            target_user_id=tg_user.external_user_id,
            dedupe_key=f"telegram:battle-app:{uuid.uuid4()}",
            priority=100,
            status="pending",
            attempts=0,
            payload={
                "method": "sendPhoto",
                "photo_b64": base64.b64encode(png).decode("ascii"),
                "caption": caption,
                "metadata": {},
            },
        )
    )


@app.post("/me/battle/team", response_model=BattleRoyaleResponse)
async def battle_team(user: User = Depends(current_user)) -> BattleRoyaleResponse:
    """Run a team royale, return the leaderboard image, and post it to the chat."""
    from core import battle_image, pet_battle

    async with get_session() as session:
        membership = await _primary_membership(session, user)
        team_id = membership.team_id if membership else _default_team_id()
        combatants = await _team_combatants(session, team_id)
        if len(combatants) < 2:
            raise HTTPException(status_code=400, detail="Нужно хотя бы двое скрамиков в команде")
        team = await session.get(Team, team_id)
        team_name = (team.name if team else None) or "команда"

        royale = pet_battle.run_royale(combatants)
        png = battle_image.render_leaderboard_png(team_name, royale["ranked"])
        caption = _royale_caption(team_name, royale["ranked"])
        await _enqueue_battle_photo(session, user, png, caption)

    return BattleRoyaleResponse(
        team_name=team_name,
        ranked=[_combatant_dto(r) for r in royale["ranked"]],
        winner=_combatant_dto(royale["winner"]) if royale["winner"] else None,
        status_frames=royale["status_frames"],
        image_base64=base64.b64encode(png).decode("ascii"),
    )


@app.get("/me/battle/leaderboard", response_model=list[BattleCombatantDTO])
async def battle_leaderboard(user: User = Depends(current_user)) -> list[BattleCombatantDTO]:
    """Current power ranking of the team (deterministic, no randomness)."""
    from core import pet_battle

    async with get_session() as session:
        membership = await _primary_membership(session, user)
        team_id = membership.team_id if membership else _default_team_id()
        combatants = await _team_combatants(session, team_id)
    ranked = sorted(combatants, key=pet_battle.combatant_power, reverse=True)
    out: list[BattleCombatantDTO] = []
    for i, c in enumerate(ranked):
        out.append(
            BattleCombatantDTO(
                rank=i + 1,
                user_id=c.user_id,
                name=c.name,
                species_id=c.species_id,
                species_name=c.species_name,
                level=c.level,
                power=pet_battle.combatant_power(c),
                equipped=c.equipped,
            )
        )
    return out


@app.post("/me/battle/duel/{opponent_user_id}", response_model=DuelResponse)
async def battle_duel(
    opponent_user_id: uuid.UUID, user: User = Depends(current_user)
) -> DuelResponse:
    """1-on-1 duel vs a teammate; persists the result for the duel leaderboard."""
    from core import battle_image, pet_battle

    if opponent_user_id == user.id:
        raise HTTPException(status_code=400, detail="Нельзя вызвать самого себя")
    async with get_session() as session:
        opponent = await session.get(User, opponent_user_id)
        if opponent is None or not opponent.active:
            raise HTTPException(status_code=404, detail="Соперник не найден")
        me_c = await _combatant_for_user(session, user)
        opp_c = await _combatant_for_user(session, opponent)
        duel = pet_battle.run_duel(me_c, opp_c)
        png = battle_image.render_duel_png(duel)

        winner_id = duel["winner"].get("user_id")
        membership = await _primary_membership(session, user)
        team_id = membership.team_id if membership else _default_team_id()
        session.add(
            PetBattle(
                team_id=team_id,
                mode="duel",
                attacker_user_id=user.id,
                defender_user_id=opponent_user_id,
                winner_user_id=uuid.UUID(winner_id) if winner_id else None,
                log_json={"log": duel["log"], "hp": duel["hp"]},
            )
        )

    return DuelResponse(
        winner=_combatant_dto(duel["winner"]),
        loser=_combatant_dto(duel["loser"]),
        log=duel["log"],
        status_frames=duel["status_frames"],
        image_base64=base64.b64encode(png).decode("ascii"),
    )


@app.get("/me/battle/duels", response_model=list[DuelLeaderboardRow])
async def duel_leaderboard(user: User = Depends(current_user)) -> list[DuelLeaderboardRow]:
    """Win/loss standings from recorded duels for the user's team."""
    async with get_session() as session:
        membership = await _primary_membership(session, user)
        team_id = membership.team_id if membership else _default_team_id()
        members = (
            await session.execute(
                select(User.id, User.display_name)
                .join(TeamMembership, TeamMembership.user_id == User.id)
                .where(TeamMembership.team_id == team_id)
            )
        ).all()
        names = {str(uid): name for uid, name in members}
        battles = (
            await session.execute(
                select(PetBattle).where(
                    PetBattle.team_id == team_id, PetBattle.mode == "duel"
                )
            )
        ).scalars().all()

    stats: dict[str, dict[str, int]] = {
        uid: {"wins": 0, "losses": 0, "battles": 0} for uid in names
    }
    for b in battles:
        for uid in (b.attacker_user_id, b.defender_user_id):
            if uid is None:
                continue
            s = stats.setdefault(str(uid), {"wins": 0, "losses": 0, "battles": 0})
            s["battles"] += 1
            if b.winner_user_id == uid:
                s["wins"] += 1
            else:
                s["losses"] += 1
    rows = [
        DuelLeaderboardRow(
            user_id=uid, name=names.get(uid, "—"),
            wins=s["wins"], losses=s["losses"], battles=s["battles"],
        )
        for uid, s in stats.items()
        if s["battles"] > 0
    ]
    rows.sort(key=lambda r: (r.wins, -r.losses), reverse=True)
    return rows


def _royale_caption(team_name: str, ranked: list[dict[str, Any]]) -> str:
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🏆 Битва скрамиков — {team_name}", ""]
    for r in ranked[:3]:
        m = medals[r["rank"] - 1] if r["rank"] <= 3 else f"{r['rank']}."
        lines.append(f"{m} {r['name']} ({r['species_name']}, ур.{r['level']}) — ⚡{r['power']}")
    extra = len(ranked) - 3
    if extra > 0:
        lines.append(f"…и ещё {extra} на арене")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Shared read helpers
# ---------------------------------------------------------------------------


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _runtime_agents() -> dict[str, str]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{_platform_api_url()}/agents")
        response.raise_for_status()
        return {item["name"]: item.get("description", "") for item in response.json()}
    except Exception:
        return {}


async def _runtime_agent_prompts() -> dict[str, str]:
    """name → base class prompt, used as a fallback for class-based agents."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{_platform_api_url()}/agents")
        response.raise_for_status()
        return {item["name"]: item.get("prompt", "") or "" for item in response.json()}
    except Exception:
        return {}


async def _runtime_agent_tools(name: str) -> list[dict[str, Any]]:
    """Declared tools (+ registry risk metadata) for an agent, via platform-api."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{_platform_api_url()}/agents/{name}/tools")
        response.raise_for_status()
        result = response.json()
        return result if isinstance(result, list) else []
    except Exception:
        return []


async def _agent_rows(
    session: AsyncSession,
) -> tuple[dict[str, AgentInstance], dict[str, AgentSpec]]:
    team_id = _default_team_id()
    instances = (
        await session.execute(select(AgentInstance).where(AgentInstance.team_id == team_id))
    ).scalars()
    specs = (await session.execute(select(AgentSpec))).scalars()
    return {row.name: row for row in instances}, {row.name: row for row in specs}


async def _get_or_create_instance(session: AsyncSession, name: str) -> AgentInstance:
    team_id = _default_team_id()
    row = (
        await session.execute(
            select(AgentInstance).where(
                AgentInstance.team_id == team_id,
                AgentInstance.name == name,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row

    row = AgentInstance(
        id=uuid.uuid4(),
        team_id=team_id,
        name=name,
        overlay={},
        enabled=True,
    )
    session.add(row)
    await session.flush()
    return row


async def _get_or_create_spec(session: AsyncSession, name: str) -> AgentSpec:
    row = (
        await session.execute(select(AgentSpec).where(AgentSpec.name == name))
    ).scalar_one_or_none()
    if row is not None:
        return row

    row = AgentSpec(
        id=uuid.uuid4(),
        name=name,
        model=DEFAULT_MODEL,
        prompt="",
        tools=[],
        autonomy={},
    )
    session.add(row)
    await session.flush()
    return row


def _merged_autonomy(spec: AgentSpec | None, instance: AgentInstance | None) -> AutonomyDTO:
    data: dict[str, Any] = {}
    if spec and spec.autonomy:
        data.update(spec.autonomy)
    overlay = instance.overlay if instance else {}
    if overlay.get("autonomy"):
        data.update(overlay["autonomy"])
    return AutonomyDTO(**data)


def _effective_model(spec: AgentSpec | None, instance: AgentInstance | None) -> str:
    overlay = instance.overlay if instance else {}
    if overlay.get("model"):
        return str(overlay["model"])
    return spec.model if spec else DEFAULT_MODEL


def _effective_prompt(
    spec: AgentSpec | None,
    instance: AgentInstance | None,
    fallback: str = "",
) -> str:
    overlay = instance.overlay if instance else {}
    if overlay.get("prompt"):
        return str(overlay["prompt"])
    if spec and spec.prompt:
        return spec.prompt
    # Class-based agents (no DB spec) — surface the base class prompt so the
    # Разработка UI shows the live prompt instead of a blank field.
    return fallback


def _action_item(action: Action, agent_name: str | None = None) -> ActionListItem:
    return ActionListItem(
        id=str(action.id),
        created_at=action.created_at.isoformat(),
        agent_name=agent_name,
        tool_name=action.tool_name,
        risk_level=action.risk_level,  # type: ignore[arg-type]
        status=action.status,  # type: ignore[arg-type]
        trace_id=str(action.trace_id) if action.trace_id else None,
        error=action.error,
    )


def _confirm_dto(row: Confirm) -> ConfirmDTO:
    return ConfirmDTO(
        id=str(row.id),
        action_id=str(row.action_id),
        prompt=row.prompt,
        status=row.status,  # type: ignore[arg-type]
        answer=row.answer,
        created_at=row.created_at.isoformat(),
        responded_at=_iso(row.responded_at),
    )


def _feedback_dto(row: ActionFeedback) -> FeedbackDTO:
    return FeedbackDTO(
        id=str(row.id),
        action_id=str(row.action_id),
        user_id=str(row.user_id) if row.user_id else None,
        rating=row.rating,
        comment=row.comment,
        created_at=row.created_at.isoformat(),
    )


def _apply_action_filters(
    stmt: Select[tuple[Action, str | None]],
    *,
    status: ActionStatus | None,
    risk: RiskLevel | None,
    agent: str | None,
    cursor: str | None,
) -> Select[tuple[Action, str | None]]:
    if status:
        stmt = stmt.where(Action.status == status)
    if risk:
        stmt = stmt.where(Action.risk_level == risk)
    if agent:
        stmt = stmt.where(AgentInstance.name == agent)
    if cursor:
        try:
            created_before = datetime.fromisoformat(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="cursor must be an ISO datetime") from exc
        stmt = stmt.where(Action.created_at < created_before)
    return stmt


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/agents", response_model=list[AgentListItem])
async def list_agents(user: User = Depends(current_user)) -> list[AgentListItem]:
    del user
    runtime = await _runtime_agents()
    async with get_session() as session:
        instances, specs = await _agent_rows(session)

    names = sorted(set(runtime) | set(instances) | set(specs))
    return [
        AgentListItem(
            name=name,
            description=runtime.get(name, ""),
            enabled=instances[name].enabled if name in instances else True,
            has_spec=name in specs,
            model=_effective_model(specs.get(name), instances.get(name)),
            updated_at=_iso(instances[name].updated_at if name in instances else None),
        )
        for name in names
    ]


@app.get("/agents/{name}/config", response_model=AgentConfigDTO)
async def get_agent_config(name: str, user: User = Depends(current_user)) -> AgentConfigDTO:
    del user
    runtime = await _runtime_agents()
    runtime_prompts = await _runtime_agent_prompts()
    async with get_session() as session:
        instances, specs = await _agent_rows(session)
    instance = instances.get(name)
    spec = specs.get(name)
    class_prompt = runtime_prompts.get(name, "")

    return AgentConfigDTO(
        name=name,
        description=runtime.get(name, ""),
        enabled=instance.enabled if instance else True,
        model=_effective_model(spec, instance),
        prompt=_effective_prompt(spec, instance, fallback=class_prompt),
        autonomy=_merged_autonomy(spec, instance),
        spec_prompt=spec.prompt if spec else class_prompt,
        overlay=instance.overlay if instance else {},
        has_spec=spec is not None,
    )


@app.patch("/agents/{name}/spec", response_model=AgentConfigDTO)
async def patch_agent_spec(
    name: str,
    request: PatchSpecRequest,
    user: User = Depends(require_roles("dev", "admin")),
) -> AgentConfigDTO:
    del user
    if request.prompt is None and request.model is None:
        raise HTTPException(status_code=400, detail="Provide prompt or model")

    async with get_session() as session:
        spec = await _get_or_create_spec(session, name)
        instance = await _get_or_create_instance(session, name)
        if request.prompt is not None:
            spec.prompt = request.prompt
        if request.model is not None:
            spec.model = request.model
        if instance.spec_id is None:
            instance.spec_id = spec.id

    return await get_agent_config(name)


@app.patch("/agents/{name}/overlay", response_model=AgentConfigDTO)
async def patch_agent_overlay(
    name: str,
    request: PatchOverlayRequest,
    user: User = Depends(require_roles("dev", "admin")),
) -> AgentConfigDTO:
    del user
    if request.enabled is None and request.autonomy is None:
        raise HTTPException(status_code=400, detail="Provide enabled or autonomy")

    async with get_session() as session:
        instance = await _get_or_create_instance(session, name)
        if request.enabled is not None:
            instance.enabled = request.enabled
        if request.autonomy is not None:
            overlay = dict(instance.overlay or {})
            overlay["autonomy"] = request.autonomy.model_dump()
            instance.overlay = overlay

    return await get_agent_config(name)


def _merge_agent_tools(
    declared: list[dict[str, Any]], overlay: dict[str, Any]
) -> list[AgentToolDTO]:
    tool_overrides = overlay.get("tools") if isinstance(overlay, dict) else None
    tool_overrides = tool_overrides if isinstance(tool_overrides, dict) else {}
    out: list[AgentToolDTO] = []
    for tool in declared:
        name = tool.get("name", "")
        override = tool_overrides.get(name) or {}
        out.append(
            AgentToolDTO(
                name=name,
                description=tool.get("description", ""),
                risk=tool.get("risk", "medium"),
                enabled=override.get("enabled", True),
                confirm=override.get("confirm"),
            )
        )
    return out


@app.get("/agents/{name}/tools", response_model=list[AgentToolDTO])
async def get_agent_tools(
    name: str, user: User = Depends(require_roles("dev", "admin"))
) -> list[AgentToolDTO]:
    del user
    declared = await _runtime_agent_tools(name)
    async with get_session() as session:
        instances, _ = await _agent_rows(session)
        instance = instances.get(name)
        overlay = dict(instance.overlay) if instance and instance.overlay else {}
    return _merge_agent_tools(declared, overlay)


@app.patch("/agents/{name}/tools", response_model=list[AgentToolDTO])
async def patch_agent_tools(
    name: str,
    request: PatchAgentToolsRequest,
    user: User = Depends(require_roles("dev", "admin")),
) -> list[AgentToolDTO]:
    del user
    # Store only non-default entries to keep the overlay minimal.
    tools_map: dict[str, dict[str, Any]] = {}
    for tool in request.tools:
        entry: dict[str, Any] = {}
        if tool.enabled is False:
            entry["enabled"] = False
        if tool.confirm is not None:
            entry["confirm"] = tool.confirm
        if entry:
            tools_map[tool.name] = entry

    async with get_session() as session:
        instance = await _get_or_create_instance(session, name)
        overlay = dict(instance.overlay or {})
        if tools_map:
            overlay["tools"] = tools_map
        else:
            overlay.pop("tools", None)
        instance.overlay = overlay

    declared = await _runtime_agent_tools(name)
    return _merge_agent_tools(declared, {"tools": tools_map})


@app.get("/users", response_model=list[UserSummaryDTO])
async def list_users(user: User = Depends(require_roles("dev", "admin"))) -> list[UserSummaryDTO]:
    del user
    team_id = _default_team_id()
    async with get_session() as session:
        rows = (
            await session.execute(
                select(User, TeamMembership, UserProfile)
                .join(
                    TeamMembership,
                    (TeamMembership.user_id == User.id) & (TeamMembership.team_id == team_id),
                    isouter=True,
                )
                .join(UserProfile, UserProfile.user_id == User.id, isouter=True)
                .where(User.active.is_(True))
                .order_by(User.display_name)
            )
        ).all()
    return [
        UserSummaryDTO(
            user_id=str(user_row.id),
            display_name=user_row.display_name,
            email=user_row.email,
            role=user_row.role,  # type: ignore[arg-type]
            ui_role=_resolve_ui_role(user_row, membership),
            team_role=membership.role if membership else None,
            tracker_login=membership.tracker_login if membership else None,
            avatar_url=_avatar_url(user_row.id, profile),
        )
        for user_row, membership, profile in rows
    ]


@app.get("/actions", response_model=list[ActionListItem])
async def list_actions(
    status: ActionStatus | None = None,
    risk: RiskLevel | None = None,
    agent: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
    user: User = Depends(current_user),
) -> list[ActionListItem]:
    del user
    stmt: Select[tuple[Action, str | None]] = (
        select(Action, AgentInstance.name)
        .join(AgentInstance, Action.agent_instance_id == AgentInstance.id, isouter=True)
        .where(Action.team_id == _default_team_id())
        .order_by(desc(Action.created_at))
        .limit(limit)
    )
    stmt = _apply_action_filters(stmt, status=status, risk=risk, agent=agent, cursor=cursor)

    async with get_session() as session:
        rows = (await session.execute(stmt)).all()

    return [_action_item(action, agent_name) for action, agent_name in rows]


@app.get("/actions/{action_id}", response_model=ActionDetailDTO)
async def get_action_detail(
    action_id: str,
    user: User = Depends(current_user),
) -> ActionDetailDTO:
    del user
    try:
        aid = uuid.UUID(action_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid action id") from exc

    async with get_session() as session:
        row = (
            await session.execute(
                select(Action, AgentInstance.name)
                .join(AgentInstance, Action.agent_instance_id == AgentInstance.id, isouter=True)
                .where(Action.id == aid, Action.team_id == _default_team_id())
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Action not found")

        action, agent_name = row
        trace = await session.get(Trace, action.trace_id) if action.trace_id else None
        confirms = (
            await session.execute(select(Confirm).where(Confirm.action_id == action.id))
        ).scalars()
        feedback = (
            await session.execute(
                select(ActionFeedback).where(ActionFeedback.action_id == action.id)
            )
        ).scalars()

    trace_dto = (
        TraceDTO(
            id=str(trace.id),
            session_id=str(trace.session_id),
            steps=list(trace.steps or []),
            metadata_json=trace.metadata_json,
            created_at=trace.created_at.isoformat(),
        )
        if trace
        else None
    )
    return ActionDetailDTO(
        action=_action_item(action, agent_name),
        input=dict(action.input or {}),
        output=action.output,
        trace=trace_dto,
        confirms=[_confirm_dto(row) for row in confirms],
        feedback=[_feedback_dto(row) for row in feedback],
    )


@app.post("/actions/{action_id}/feedback", response_model=FeedbackDTO)
async def create_action_feedback(
    action_id: str,
    request: FeedbackRequest,
    user: User = Depends(current_user),
) -> FeedbackDTO:
    try:
        aid = uuid.UUID(action_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid action id") from exc

    async with get_session() as session:
        action = await session.get(Action, aid)
        if action is None or action.team_id != _default_team_id():
            raise HTTPException(status_code=404, detail="Action not found")
        feedback = ActionFeedback(
            id=uuid.uuid4(),
            action_id=aid,
            user_id=user.id,
            rating=request.rating,
            comment=request.comment,
            created_at=datetime.now(timezone.utc),
        )
        session.add(feedback)
        await session.flush()

    return _feedback_dto(feedback)


@app.get("/confirms", response_model=list[ConfirmDTO])
async def list_confirms(
    status: ConfirmStatus | None = "pending",
    user: User = Depends(current_user),
) -> list[ConfirmDTO]:
    del user
    stmt = (
        select(Confirm)
        .join(Action, Confirm.action_id == Action.id)
        .where(Action.team_id == _default_team_id())
        .order_by(desc(Confirm.created_at))
    )
    if status:
        stmt = stmt.where(Confirm.status == status)

    async with get_session() as session:
        rows = (await session.execute(stmt)).scalars()

    return [_confirm_dto(row) for row in rows]


async def _post_platform(path: str, payload: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(f"{_platform_api_url()}{path}", json=payload)
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()


@app.post("/confirms/{confirm_id}/decision")
async def decide_confirm(
    confirm_id: str,
    request: ConfirmDecisionRequest,
    user: User = Depends(require_roles("dev", "admin")),
) -> Any:
    del user
    return await _post_platform(f"/confirm/{confirm_id}", {"approved": request.approved})


@app.post("/playground/{agent}/chat")
async def playground_chat(
    agent: str,
    request: PlaygroundChatRequest,
    user: User = Depends(require_roles("dev", "admin")),
) -> Any:
    del user
    return await _post_platform(
        f"/agents/{agent}/chat",
        {"message": request.message, "session_id": request.session_id},
    )


def _scheduled_job_dto(job: ScheduledJob, agent_name: str | None) -> ScheduledJobDTO:
    payload = job.payload if isinstance(job.payload, dict) else {}
    return ScheduledJobDTO(
        id=str(job.id),
        agent_name=agent_name,
        name=job.name,
        cron_expr=job.cron_expr,
        schedule=cron_to_schedule(job.cron_expr),
        human=describe_cron(job.cron_expr),
        payload_type=payload.get("type"),
        enabled=job.enabled,
        run_count=job.run_count,
        max_runs=job.max_runs,
        next_run=_iso(job.next_run),
        created_at=job.created_at.isoformat(),
    )


@app.get("/scheduled-jobs", response_model=list[ScheduledJobDTO])
async def list_scheduled_jobs(
    user: User = Depends(current_teamlead),
) -> list[ScheduledJobDTO]:
    async with get_session() as session:
        team_id = _default_team_id()
        await _assert_team_access(session, user, team_id)
        rows = (
            await session.execute(
                select(ScheduledJob, AgentInstance.name)
                .join(AgentInstance, ScheduledJob.agent_instance_id == AgentInstance.id)
                .where(AgentInstance.team_id == team_id)
                .order_by(desc(ScheduledJob.created_at))
            )
        ).all()
    return [_scheduled_job_dto(job, agent_name) for job, agent_name in rows]


@app.patch("/scheduled-jobs/{job_id}", response_model=ScheduledJobDTO)
async def patch_scheduled_job(
    job_id: uuid.UUID,
    request: PatchScheduledJobRequest,
    user: User = Depends(current_teamlead),
) -> ScheduledJobDTO:
    if request.enabled is None and request.schedule is None:
        raise HTTPException(status_code=400, detail="Provide enabled or schedule")

    async with get_session() as session:
        row = (
            await session.execute(
                select(ScheduledJob, AgentInstance.name, AgentInstance.team_id)
                .join(AgentInstance, ScheduledJob.agent_instance_id == AgentInstance.id)
                .where(ScheduledJob.id == job_id)
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        job, agent_name, team_id = row
        await _assert_team_access(session, user, team_id)

        if request.schedule is not None:
            try:
                cron_expr = schedule_to_cron(request.schedule.model_dump())
                job.next_run = compute_next_run(cron_expr)
            except Exception as exc:  # noqa: BLE001 — surface any cron build error as 400
                raise HTTPException(status_code=400, detail=f"Invalid schedule: {exc}") from exc
            job.cron_expr = cron_expr
        if request.enabled is not None:
            job.enabled = request.enabled

        await session.flush()
        return _scheduled_job_dto(job, agent_name)


# ---------------------------------------------------------------------------
# Team (teamlead dashboards)
# ---------------------------------------------------------------------------


@app.get("/teams/{team_id}/members", response_model=list[TeamMemberDTO])
async def list_team_members(
    team_id: uuid.UUID, user: User = Depends(current_teamlead)
) -> list[TeamMemberDTO]:
    async with get_session() as session:
        await _assert_team_access(session, user, team_id)
        rows = (
            await session.execute(
                select(TeamMembership, User, UserProfile)
                .join(User, TeamMembership.user_id == User.id)
                .join(UserProfile, UserProfile.user_id == User.id, isouter=True)
                .where(TeamMembership.team_id == team_id)
                .order_by(TeamMembership.role, User.display_name)
            )
        ).all()
    return [
        TeamMemberDTO(
            user_id=str(membership.user_id),
            display_name=user_row.display_name,
            tracker_login=membership.tracker_login,
            role=membership.role,
            avatar_url=_avatar_url(user_row.id, profile),
        )
        for membership, user_row, profile in rows
    ]


@app.get("/teams/{team_id}/health", response_model=TeamHealthDTO)
async def team_health(
    team_id: uuid.UUID,
    window: int = Query(14, ge=1, le=90),
    user: User = Depends(current_teamlead),
) -> TeamHealthDTO:
    async with get_session() as session:
        await _assert_team_access(session, user, team_id)
        queue = await _team_queue(session, team_id)
        rows = (
            await session.execute(
                select(TeamMembership, User)
                .join(User, TeamMembership.user_id == User.id)
                .where(
                    TeamMembership.team_id == team_id,
                    TeamMembership.tracker_match_status == "confirmed",
                )
            )
        ).all()

    roster = [
        (str(membership.user_id), user_row.display_name, membership.tracker_login)
        for membership, user_row in rows
        if membership.tracker_login
    ][:30]

    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=window)).date().isoformat()

    async def _member_issues(login: str) -> tuple[list, list] | None:
        try:
            return await _fetch_assignee_issues(login, queue, since_date=since)
        except Exception:  # noqa: BLE001 — Tracker best-effort
            return None

    results = await asyncio.gather(*[_member_issues(login) for _, _, login in roster])
    if roster and all(result is None for result in results):
        return TeamHealthDTO(available=False, window_days=window, note="Tracker недоступен")

    members: list[dict[str, Any]] = []
    for (user_id, display_name, login), result in zip(roster, results, strict=True):
        open_issues, resolved = result if result is not None else ([], [])
        members.append(
            {
                "user_id": user_id,
                "display_name": display_name,
                "tracker_login": login,
                "open": open_issues,
                "resolved": resolved,
            }
        )

    health = board_metrics.team_health(members, window_days=window, now=now)
    return TeamHealthDTO(available=True, **health)


@app.post("/teams/{team_id}/audit")
async def team_audit(
    team_id: uuid.UUID,
    window: int = Query(14, ge=1, le=90),
    user: User = Depends(current_teamlead),
) -> Any:
    """Run the board-audit agent for a team. Teamlead/developer only.

    Returns the agent ``ChatResponse`` (markdown ``reply`` + trace ``steps``),
    which the UI renders with mock streaming and a status timeline.
    """
    async with get_session() as session:
        await _assert_team_access(session, user, team_id)
        queue = await _team_queue(session, team_id)

    queue_hint = f" queue={queue}" if queue else ""
    message = (
        "Проведи полный аудит доски: сильные стороны, что улучшить, "
        f"и рекомендации по каждому участнику за {window} дней.{queue_hint}"
    )
    return await _post_platform(
        "/agents/audit_agent/chat",
        {"message": message, "session_id": f"audit:{team_id}:{uuid.uuid4()}"},
    )
