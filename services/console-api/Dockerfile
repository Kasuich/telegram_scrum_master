FROM python:3.12-slim AS builder

WORKDIR /app

RUN pip install uv

COPY pyproject.toml uv.lock ./
COPY packages/core ./packages/core
COPY services/console-api ./services/console-api

RUN uv sync --frozen --no-dev --no-editable --package console-api

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv .venv

ENV PATH="/app/.venv/bin:$PATH"

CMD ["uvicorn", "console_api.main:app", "--host", "0.0.0.0", "--port", "8002"]
