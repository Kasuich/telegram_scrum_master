from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from telegram_gateway.runtime import GatewayRuntime, build_runtime
from telegram_gateway.settings import GatewaySettings


class WebhookResponse(BaseModel):
    accepted: bool
    duplicate: bool = False
    update_id: int


class InstallationInfo(BaseModel):
    installation_id: str
    team_id: str
    bot_username: str | None
    status: str


def _runtime(app: FastAPI) -> GatewayRuntime:
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        raise RuntimeError("Gateway runtime is not initialized")
    return runtime


router = APIRouter()


def create_app(
    *,
    settings: GatewaySettings | None = None,
    runtime: GatewayRuntime | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if runtime is not None:
            active_runtime = runtime
        elif settings is not None:
            active_runtime = build_runtime(settings)
        else:
            active_runtime = build_runtime()
        app.state.runtime = active_runtime
        stop_event = asyncio.Event()
        app.state.stop_event = stop_event
        task: asyncio.Task[None] | None = None
        if active_runtime.auto_start_workers:
            task = asyncio.create_task(active_runtime.run(stop_event))
            app.state.worker_task = task
        try:
            yield
        finally:
            stop_event.set()
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await active_runtime.close()

    app = FastAPI(title="Telegram Gateway", lifespan=lifespan)
    if runtime is not None:
        app.state.runtime = runtime
    else:
        app.state.runtime = None
    app.include_router(router)
    return app


@router.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready(request: Request) -> dict[str, int | str]:
    runtime = _runtime(request.app)
    if runtime.bridge is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="bridge missing",
        )
    return {"status": "ok", "queue_depth": runtime.spool.depth()}


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.post("/webhook", response_model=WebhookResponse)
async def webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(
        default=None,
        alias="X-Telegram-Bot-Api-Secret-Token",
    ),
) -> WebhookResponse:
    runtime = _runtime(request.app)
    settings = runtime.settings
    if x_telegram_bot_api_secret_token != settings.webhook_secret:
        runtime.record_webhook("rejected")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook secret",
        )

    payload = await request.json()
    update_id = payload.get("update_id")
    if not isinstance(update_id, int):
        runtime.record_webhook("rejected")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing update_id")

    stored = runtime.spool.store_update(update_id, payload, datetime.now(tz=timezone.utc))
    runtime.record_webhook("accepted" if stored else "duplicate")
    return WebhookResponse(accepted=True, duplicate=not stored, update_id=update_id)


@router.get("/internal/installations/resolve")
async def resolve_installation(
    request: Request,
    token: str = Query(..., description="One-time onboarding token"),
) -> InstallationInfo:
    """
    Resolve deep link token to installation info.
    Used for /start <token> onboarding flow.
    """
    runtime = _runtime(request.app)
    if runtime.bridge is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="bridge not available",
        )
    
    try:
        result = await runtime.bridge.resolve_installation(token)
        return InstallationInfo(
            installation_id=result["installation_id"],
            team_id=result["team_id"],
            bot_username=result.get("bot_username"),
            status=result.get("status", "active"),
        )
    except Exception as exc:
        if hasattr(exc, "status_code") and exc.status_code == 404:
            raise HTTPException(status_code=404, detail="Token not found or expired")
        raise HTTPException(status_code=502, detail="Failed to resolve token")


app = create_app()
