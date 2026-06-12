"""Redact secrets from eval stored outputs."""

from __future__ import annotations

import copy
import re
from typing import Any

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "token",
        "tracker_token",
        "api_key",
        "password",
        "secret",
    }
)
_TOKEN_RE = re.compile(r"(Bearer\s+|OAuth\s+)[^\s\"']+", re.I)


def redact_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return redact_dict(value)
    if isinstance(value, list):
        return [redact_value(key, v) for v in value]
    if isinstance(value, str):
        lowered = key.lower()
        if any(s in lowered for s in _SENSITIVE_KEYS):
            return "[REDACTED]"
        return _TOKEN_RE.sub("[REDACTED]", value)
    return value


def redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        out[key] = redact_value(str(key), value)
    return out


def redact_output(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if data is None:
        return None
    return redact_dict(copy.deepcopy(data))
