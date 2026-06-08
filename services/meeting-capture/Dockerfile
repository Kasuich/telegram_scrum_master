FROM python:3.12-slim AS builder

WORKDIR /app

ENV UV_HTTP_TIMEOUT=120 \
    UV_HTTP_RETRIES=10

RUN pip install uv

COPY pyproject.toml uv.lock ./
COPY packages/core ./packages/core
COPY services/meeting-capture ./services/meeting-capture

RUN --mount=type=cache,id=uv-cache,target=/root/.cache/uv,sharing=locked \
    uv sync --frozen --no-dev --no-editable --package meeting-capture

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        pulseaudio \
        xvfb \
        fonts-dejavu \
        libglib2.0-0 \
        libnss3 \
        libnspr4 \
        libatk1.0-0 \
        libatk-bridge2.0-0 \
        libcups2 \
        libdrm2 \
        libdbus-1-3 \
        libxcb1 \
        libxkbcommon0 \
        libx11-6 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxrandr2 \
        libgbm1 \
        libpango-1.0-0 \
        libcairo2 \
        libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/.venv .venv

ENV PATH="/app/.venv/bin:$PATH"
ENV DISPLAY=":99.0"
ENV CAPTURE_DISPLAY=":99.0"

RUN python -m playwright install chromium

CMD ["sh", "-c", "pulseaudio --start --exit-idle-time=-1 || true; Xvfb :99 -screen 0 ${CAPTURE_VIDEO_SIZE:-1280x720}x24 & uvicorn meeting_capture.main:app --host 0.0.0.0 --port 8003"]
