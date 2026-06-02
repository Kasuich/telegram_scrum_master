FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install uv

COPY pyproject.toml ./
COPY packages/core ./packages/core
COPY services/platform-api ./services/platform-api

RUN uv sync --frozen --no-dev --package platform-api

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv .venv

ENV PATH="/app/.venv/bin:$PATH"

COPY services/platform-api/src ./src

CMD ["uvicorn", "platform_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
