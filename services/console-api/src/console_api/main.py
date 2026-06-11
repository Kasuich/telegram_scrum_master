"""Console API — BFF/read-model service for the PM Agent Platform GUI."""

from __future__ import annotations

import hmac
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
from core.config import get_config
from core.db import create_all_tables, get_session
from core.models import (
    Action,
    ActionFeedback,
    AgentInstance,
    AgentSpec,
    Confirm,
    ConsoleSession,
    LoginChallenge,
    ScheduledJob,
    TeamMembership,
    TelegramInstallation,
    TelegramOutbox,
    TelegramUser,
    TelegramUserLink,
    Trace,
    User,
)
from core.seed import ensure_default_team
from fastapi import Cookie, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
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


def _cors_origins() -> list[str]:
    raw = os.getenv("CONSOLE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [item.strip() for item in raw.split(",") if item.strip()]


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


class UserDTO(BaseModel):
    id: str
    email: str
    display_name: str
    role: ConsoleRole


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


def _user_dto(user: User) -> UserDTO:
    return UserDTO(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        role=user.role,  # type: ignore[arg-type]
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

    _set_session_cookie(response, raw_token)
    return LoginResponse(user=_user_dto(user))


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

    _set_session_cookie(response, raw_token)
    return LoginResponse(user=_user_dto(user))


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
    return _user_dto(user)


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


def _effective_prompt(spec: AgentSpec | None, instance: AgentInstance | None) -> str:
    overlay = instance.overlay if instance else {}
    if overlay.get("prompt"):
        return str(overlay["prompt"])
    return spec.prompt if spec else ""


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
    async with get_session() as session:
        instances, specs = await _agent_rows(session)
    instance = instances.get(name)
    spec = specs.get(name)

    return AgentConfigDTO(
        name=name,
        description=runtime.get(name, ""),
        enabled=instance.enabled if instance else True,
        model=_effective_model(spec, instance),
        prompt=_effective_prompt(spec, instance),
        autonomy=_merged_autonomy(spec, instance),
        spec_prompt=spec.prompt if spec else "",
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


@app.get("/scheduled-jobs")
async def list_scheduled_jobs(user: User = Depends(require_roles("dev", "admin"))) -> list[dict]:
    del user
    async with get_session() as session:
        rows = (
            await session.execute(
                select(ScheduledJob, AgentInstance.name)
                .join(AgentInstance, ScheduledJob.agent_instance_id == AgentInstance.id)
                .where(AgentInstance.team_id == _default_team_id())
                .order_by(desc(ScheduledJob.created_at))
            )
        ).all()
    return [
        {
            "id": str(job.id),
            "agent_name": agent_name,
            "name": job.name,
            "cron_expr": job.cron_expr,
            "enabled": job.enabled,
            "run_count": job.run_count,
            "max_runs": job.max_runs,
            "next_run": _iso(job.next_run),
            "created_at": job.created_at.isoformat(),
        }
        for job, agent_name in rows
    ]
