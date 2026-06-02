FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
COPY packages/core ./packages/core
COPY services/pm-orchestrator ./services/pm-orchestrator

RUN uv sync --frozen --no-dev --package pm-orchestrator

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv .venv

ENV PATH="/app/.venv/bin:$PATH"

COPY services/pm-orchestrator/src ./src

CMD ["python", "-m", "pm_orchestrator"]
