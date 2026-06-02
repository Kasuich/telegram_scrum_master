from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

_F = TypeVar("_F", bound=Callable[..., Any])


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON strings."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        trace_id = _trace_id_var.get()
        if trace_id is not None:
            payload["trace_id"] = trace_id

        # Merge any extra fields the caller passed via extra={}
        for key, value in record.__dict__.items():
            if key not in logging.LogRecord.__dict__ and not key.startswith("_"):
                # Skip standard LogRecord attributes that aren't relevant
                if key not in {
                    "name",
                    "msg",
                    "args",
                    "created",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "stack_info",
                    "thread",
                    "threadName",
                    "exc_info",
                    "exc_text",
                    "message",
                    "taskName",
                }:
                    payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured with JSONFormatter on a StreamHandler."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        # Propagation off so parent handlers don't duplicate records in JSON format
        logger.propagate = False
    return logger


def set_trace_id(trace_id: str) -> None:
    """Set the trace ID for the current async context."""
    _trace_id_var.set(trace_id)


def get_trace_id() -> str | None:
    """Get the trace ID for the current async context."""
    return _trace_id_var.get()


def configure_logging(level: str = "INFO") -> None:
    """
    Configure root logging level and attach JSONFormatter to the root logger.

    Call once at application startup. Subsequent calls update the level.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(numeric_level)

    has_json_handler = any(isinstance(h.formatter, JSONFormatter) for h in root.handlers)
    if not has_json_handler:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        root.addHandler(handler)


def timed(func: _F) -> _F:
    """
    Decorator that logs execution time of sync and async callables.

    Adds ``duration_ms`` to the log record extra fields.
    """
    logger = get_logger(func.__module__)

    if asyncio.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.perf_counter()
            try:
                return await func(*args, **kwargs)
            finally:
                duration_ms = int((time.perf_counter() - start) * 1000)
                logger.debug(
                    "%s finished",
                    func.__qualname__,
                    extra={"duration_ms": duration_ms, "function": func.__qualname__},
                )

        return async_wrapper  # type: ignore[return-value]

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.debug(
                "%s finished",
                func.__qualname__,
                extra={"duration_ms": duration_ms, "function": func.__qualname__},
            )

    return sync_wrapper  # type: ignore[return-value]


__all__ = [
    "JSONFormatter",
    "get_logger",
    "set_trace_id",
    "get_trace_id",
    "configure_logging",
    "timed",
]
