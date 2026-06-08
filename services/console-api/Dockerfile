FROM python:3.12-slim AS builder

WORKDIR /app

ENV UV_HTTP_TIMEOUT=120 \
    UV_HTTP_RETRIES=10

RUN pip install uv

COPY pyproject.toml uv.lock ./
COPY packages/core ./packages/core
COPY services/console-api ./services/console-api

RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --package console-api

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv .venv

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "console_api.main:app", "--host", "0.0.0.0", "--port", "8002"]
