"""Resolve display names / nicknames to Tracker logins via queue team API."""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from core.tracker import TrackerClient

logger = logging.getLogger(__name__)

_LOGIN_RE = re.compile(r"^[a-z0-9._-]+$", re.IGNORECASE)
_CACHE_TTL_SEC = 300.0
_team_cache: dict[str, tuple[float, list[TrackerUser]]] = {}


@dataclass(frozen=True)
class TrackerUser:
    login: str
    display: str
    first_name: str = ""
    last_name: str = ""
    email: str = ""


@dataclass(frozen=True)
class AssigneeMatch:
    login: str
    display: str
    score: float
    query: str


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def _parse_user(raw: dict[str, Any]) -> TrackerUser | None:
    if not isinstance(raw, dict):
        return None
    login = str(raw.get("login") or "").strip()
    if not login:
        return None
    return TrackerUser(
        login=login,
        display=str(raw.get("display") or raw.get("name") or login),
        first_name=str(raw.get("firstName") or ""),
        last_name=str(raw.get("lastName") or ""),
        email=str(raw.get("email") or ""),
    )


def _users_from_queue_payload(queue_data: dict[str, Any]) -> list[TrackerUser]:
    users: list[TrackerUser] = []
    for raw in queue_data.get("teamUsers") or []:
        u = _parse_user(raw)
        if u:
            users.append(u)
    lead = queue_data.get("lead")
    if isinstance(lead, dict):
        u = _parse_user(lead)
        if u and u.login not in {x.login for x in users}:
            users.append(u)
    return users


def _env_alias_map() -> dict[str, str]:
    raw = os.getenv("TRACKER_ASSIGNEE_ALIASES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return {_norm(str(k)): str(v) for k, v in data.items()}
    except json.JSONDecodeError:
        pass
    return {}


def _match_score(query: str, user: TrackerUser) -> float:
    q = _norm(query)
    if not q:
        return 0.0
    parts = [
        _norm(user.login),
        _norm(user.display),
        _norm(f"{user.first_name} {user.last_name}"),
        _norm(user.first_name),
        _norm(user.last_name),
    ]
    best = 0.0
    prefix = q[: min(3, len(q))]
    for part in parts:
        if not part:
            continue
        if q == part:
            return 1.0
        if q in part or part in q:
            best = max(best, 0.88)
        if len(prefix) >= 2 and prefix in part:
            best = max(best, 0.8)
        best = max(best, difflib.SequenceMatcher(None, q, part).ratio())
    for word in _norm(user.display).split():
        if not word:
            continue
        if q == word or (len(prefix) >= 2 and word.startswith(prefix)):
            best = max(best, 0.82)
        best = max(best, difflib.SequenceMatcher(None, q, word).ratio())
    return best


def best_user_match(query: str, users: list[TrackerUser], *, threshold: float = 0.42) -> AssigneeMatch | None:
    if not query.strip() or not users:
        return None
    scored = [( _match_score(query, u), u) for u in users]
    scored.sort(key=lambda x: x[0], reverse=True)
    score, user = scored[0]
    if score < threshold:
        return None
    return AssigneeMatch(login=user.login, display=user.display, score=score, query=query.strip())


async def load_team_users(client: TrackerClient, queue_key: str) -> list[TrackerUser]:
    now = time.monotonic()
    cached = _team_cache.get(queue_key)
    if cached and now - cached[0] < _CACHE_TTL_SEC:
        return cached[1]

    users: list[TrackerUser] = []
    try:
        queue_data = await client.get_queue(queue_key, expand="team")
        users = _users_from_queue_payload(queue_data)
    except Exception as exc:
        logger.warning("get_queue expand=team failed for %s: %s", queue_key, exc)

    if not users:
        try:
            org_users = await client.list_users(per_page=100)
            users = [u for raw in org_users if (u := _parse_user(raw))]
        except Exception as exc:
            logger.warning("list_users fallback failed: %s", exc)

    _team_cache[queue_key] = (now, users)
    return users


async def resolve_assignee(
    name_or_login: str,
    client: TrackerClient,
    queue_key: str,
    *,
    threshold: float = 0.42,
) -> AssigneeMatch:
    """Map a name or login to the closest queue team member."""
    raw = name_or_login.strip()
    if not raw:
        return AssigneeMatch(login="", display="", score=0.0, query=raw)

    if _LOGIN_RE.fullmatch(raw):
        return AssigneeMatch(login=raw, display=raw, score=1.0, query=raw)

    alias = _env_alias_map().get(_norm(raw))
    if alias:
        return AssigneeMatch(login=alias, display=alias, score=1.0, query=raw)

    users = await load_team_users(client, queue_key)
    match = best_user_match(raw, users, threshold=threshold)
    if match:
        return match

    logger.info("No team match for assignee %r in queue %s", raw, queue_key)
    return AssigneeMatch(login=raw, display=raw, score=0.0, query=raw)


_ASSIGNEE_STOPWORDS = frozenset(
    {
        "новая",
        "новую",
        "новой",
        "новые",
        "новый",
        "задача",
        "задачу",
        "задачи",
        "задачей",
        "команду",
        "команды",
        "команде",
        "инструкцию",
        "инструкции",
        "трекер",
        "трекера",
        "ответственным",
    }
)


def _clean_assignee_token(raw: str) -> str | None:
    name = raw.strip().strip(".,?!:;\"'«»")
    if not name or len(name) < 2:
        return None
    if _norm(name) in _ASSIGNEE_STOPWORDS:
        return None
    return name


def extract_assignee_mention(message: str) -> str | None:
    """Pull assignee name from Russian chat / PM phrasing."""
    text = message.strip()
    # Higher priority first (chat transcripts, explicit assignment)
    patterns = [
        r"ответственн\w*\s+назначим\s+([А-Яа-яA-Za-z][А-Яа-яA-Za-z.\-]*)",
        r"назначим\s+([А-Яа-яA-Za-z][А-Яа-яA-Za-z.\-]*)",
        r"задача:\s*([А-Яа-яA-Za-z][А-Яа-яA-Za-z.\-]*)",
        r"(?:исполнител[ьяюе]|assignee)\s+([А-Яа-яA-Za-z][А-Яа-яA-Za-z.\-]*)",
        # «на/для» only as separate words (not «нужна новая»)
        r"(?:^|\s)(?:на|для)\s+([А-Яа-яA-Za-z][А-Яа-яA-Za-z.\-]*)",
        r"задач[ауеи]?\s+([А-Яа-я][А-Яа-я.\-]*)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.MULTILINE)
        if m:
            cleaned = _clean_assignee_token(m.group(1))
            if cleaned:
                return cleaned
    return None


__all__ = [
    "AssigneeMatch",
    "TrackerUser",
    "best_user_match",
    "extract_assignee_mention",
    "load_team_users",
    "resolve_assignee",
]
