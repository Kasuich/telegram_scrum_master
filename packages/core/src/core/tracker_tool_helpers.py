"""Shared helpers for tracker agent tools."""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any

# Terminal workflow statuses excluded from search by default (closed / cancelled).
_DEFAULT_TERMINAL_STATUS_KEYS = frozenset({"closed", "cancelled", "canceled"})
_DEFAULT_TERMINAL_STATUS_NAMES: tuple[str, ...] = (
    "Closed",
    "Закрыт",
    "Закрыта",
    "Cancelled",
    "Canceled",
    "Отменена",
    "Отменён",
    "Отменен",
    "Отменено",
)


def _norm_status(text: str) -> str:
    return text.lower().replace("ё", "е").strip()


def search_open_only_default() -> bool:
    """False when TRACKER_SEARCH_ALL_STATUSES=true (include closed/cancelled)."""
    return os.getenv("TRACKER_SEARCH_ALL_STATUSES", "").lower() not in ("1", "true", "yes")


def terminal_status_names() -> tuple[str, ...]:
    raw = os.getenv("TRACKER_TERMINAL_STATUSES", "").strip()
    if raw:
        return tuple(s.strip() for s in raw.replace(";", ",").split(",") if s.strip())
    return _DEFAULT_TERMINAL_STATUS_NAMES


def build_status_filter(*, status: str = "") -> list[str]:
    """YQL status clauses: explicit match, or exclusions for terminal statuses."""
    if status.strip():
        return [f'Status: "{status.strip()}"']
    if not search_open_only_default():
        return []
    return [f'Status: !"{name}"' for name in terminal_status_names()]


def combine_yql(*parts: str) -> str:
    cleaned = [p.strip() for p in parts if p and p.strip()]
    return " AND ".join(cleaned)


def yql_quote(value: str) -> str:
    """Escape a string for YQL double-quoted literals."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def query_has_status_filter(yql: str) -> bool:
    return bool(re.search(r"\bStatus\s*:", yql, re.IGNORECASE))


def apply_open_status_filter_to_yql(yql: str) -> str:
    """Prepend terminal-status exclusions unless the query already filters Status."""
    if not search_open_only_default() or query_has_status_filter(yql):
        return yql
    exclusions = combine_yql(*build_status_filter())
    if not exclusions:
        return yql
    body = yql.strip() or "Sort: Updated DESC"
    return f"{exclusions} AND ({body})"


def filter_terminal_issues(
    issues: list[dict[str, Any]], *, explicit_status: str = ""
) -> list[dict[str, Any]]:
    """Drop closed/cancelled issues when searching open work by default."""
    if explicit_status.strip() or not search_open_only_default():
        return issues
    terminal_keys = _DEFAULT_TERMINAL_STATUS_KEYS
    terminal_names = {_norm_status(n) for n in terminal_status_names()}
    out: list[dict[str, Any]] = []
    for issue in issues:
        st = issue.get("status") or {}
        key = (st.get("key") or "").lower()
        display = _norm_status(st.get("display") or "")
        if key in terminal_keys or display in terminal_names:
            continue
        out.append(issue)
    return out


_RU_MONTHS: dict[str, int] = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def normalize_deadline(value: str) -> str | dict[str, str]:
    """
    Normalize deadline for Tracker API (YYYY-MM-DD only).

    Accepts ISO date with time, DD.MM.YYYY, and Russian «7 июня 2026».
    """
    s = (value or "").strip()
    if not s:
        return ""

    iso = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if iso:
        return iso.group(1)

    dotted = re.match(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})", s)
    if dotted:
        d, m, y = int(dotted.group(1)), int(dotted.group(2)), int(dotted.group(3))
        return date(y, m, d).isoformat()

    ru = re.search(
        r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})",
        s.lower().replace("ё", "е"),
    )
    if ru:
        day = int(ru.group(1))
        month_name = ru.group(2)
        year = int(ru.group(3))
        month = _RU_MONTHS.get(month_name)
        if month:
            return date(year, month, day).isoformat()

    return {"error": f"Invalid deadline format (use YYYY-MM-DD): {value!r}"}


def parse_csv_logins(value: str) -> list[str]:
    """Parse comma/space-separated Yandex logins."""
    if not value or not value.strip():
        return []
    parts = [p.strip() for p in value.replace(";", ",").split(",")]
    return [p for p in parts if p]


def parse_tags(value: str) -> list[str]:
    if not value or not value.strip():
        return []
    return [t.strip() for t in value.replace(";", ",").split(",") if t.strip()]


def format_assignee_yql(login: str) -> str:
    login = login.strip()
    if not login:
        return ""
    if re.fullmatch(r"[a-z0-9._-]+", login, re.IGNORECASE):
        return f"Assignee: {login}"
    return f'Assignee: "{login}"'


def build_find_yql(
    *,
    summary_hint: str = "",
    assignee_login: str = "",
    status: str = "",
) -> str:
    """Build YQL fragment for issue search by context hints."""
    parts: list[str] = []
    if summary_hint.strip():
        parts.append(f'Summary: "{summary_hint.strip()}"')
    if assignee_login.strip():
        parts.append(format_assignee_yql(assignee_login.strip()))
    parts.extend(build_status_filter(status=status))
    if parts:
        return combine_yql(*parts)
    if search_open_only_default():
        return combine_yql(*build_status_filter(), "Sort: Updated DESC")
    return "Sort: Updated DESC"


def build_find_fallback_queries(
    *,
    summary_hint: str = "",
    assignee_login: str = "",
    status: str = "",
) -> list[str]:
    """Additional YQL variants when exact Summary: \"...\" match returns nothing."""
    queries: list[str] = []
    seen: set[str] = set()

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    hint = summary_hint.strip()
    status_clause = combine_yql(*build_status_filter(status=status))

    if hint:
        add(f"Summary: {hint}")
        add(f'"{hint}"')
        for word in hint.split():
            if len(word) < 2:
                continue
            line = f"Summary: {word}"
            if assignee_login:
                line = f"{line} AND {format_assignee_yql(assignee_login)}"
            add(line)
            if status_clause:
                add(f"{line} AND {status_clause}")

    if assignee_login:
        base = format_assignee_yql(assignee_login)
        add(base)
        if status_clause:
            add(f"{base} AND {status_clause}")

    return queries


def normalize_tracker_yql(query: str) -> str:
    """Fix common invalid YQL from LLM (SQL-style assignee=, wrong field case)."""
    q = query.strip()
    if not q:
        return q

    q = re.sub(
        r"assignee\s*=\s*['\"]([^'\"]+)['\"]",
        lambda m: format_assignee_yql(m.group(1).strip()),
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(r"\bsummary\s*:", "Summary:", q, flags=re.IGNORECASE)
    q = re.sub(r"\bstatus\s*:", "Status:", q, flags=re.IGNORECASE)
    q = re.sub(r"\bqueue\s*:", "Queue:", q, flags=re.IGNORECASE)
    q = re.sub(r'\bsummary:"([^"]*)"', r'Summary: "\1"', q, flags=re.IGNORECASE)
    q = re.sub(r"\bsummary:'([^']*)'", r'Summary: "\1"', q, flags=re.IGNORECASE)
    return q


def filter_issues_by_hint(issues: list[dict[str, Any]], summary_hint: str) -> list[dict[str, Any]]:
    """Prefer issues whose summary contains hint words (after broad assignee search)."""
    hint = summary_hint.strip().lower()
    if not hint or not issues:
        return issues
    words = [w for w in hint.split() if len(w) >= 2]
    if not words:
        return issues

    def matches(issue: dict[str, Any]) -> bool:
        text = (issue.get("summary") or "").lower()
        return any(w in text for w in words)

    matched = [i for i in issues if matches(i)]
    return matched if matched else issues


def parse_custom_fields_json(value: str) -> dict[str, Any]:
    if not value or not value.strip():
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        return {"error": f"Invalid custom_fields JSON: {exc}"}
    if not isinstance(data, dict):
        return {"error": "custom_fields must be a JSON object"}
    return data


def issue_summary(issue: dict[str, Any], *, detailed: bool = False) -> dict[str, Any]:
    """Normalize issue API response for agent consumption."""
    followers = issue.get("followers") or []
    follower_names = [
        (f.get("display") or f.get("login") or str(f)) if isinstance(f, dict) else str(f)
        for f in followers
    ]
    base = {
        "key": issue.get("key"),
        "summary": issue.get("summary"),
        "status": (issue.get("status") or {}).get("display"),
        "priority": (issue.get("priority") or {}).get("display"),
        "assignee": (issue.get("assignee") or {}).get("display")
        or (issue.get("assignee") or {}).get("login"),
        "type": (issue.get("type") or {}).get("display"),
        "deadline": issue.get("deadline"),
        "story_points": issue.get("storyPoints"),
        "followers": follower_names,
        "tags": issue.get("tags") or [],
        "url": f"https://tracker.yandex.ru/{issue.get('key')}",
    }
    if detailed:
        base["description"] = issue.get("description")
        base["parent"] = (issue.get("parent") or {}).get("key") if issue.get("parent") else None
        base["sprint"] = issue.get("sprint")
        base["components"] = issue.get("components")
        base["project"] = issue.get("project")
    return base


def build_patch_body(
    *,
    summary: str = "",
    description: str = "",
    priority: str = "",
    assignee: str = "",
    issue_type: str = "",
    tags: str = "",
    deadline: str = "",
    story_points: str = "",
    sprint: str = "",
    parent: str = "",
    project: str = "",
    components: str = "",
    custom_fields: str = "",
) -> dict[str, Any] | dict[str, str]:
    fields: dict[str, Any] = {}
    if summary:
        fields["summary"] = summary
    if description:
        fields["description"] = description
    if priority:
        fields["priority"] = priority
    if assignee:
        fields["assignee"] = assignee
    if issue_type:
        fields["type"] = issue_type
    tag_list = parse_tags(tags)
    if tag_list:
        fields["tags"] = tag_list
    if deadline:
        normalized = normalize_deadline(deadline)
        if isinstance(normalized, dict):
            return normalized
        fields["deadline"] = normalized
    if story_points:
        try:
            sp = float(story_points)
            fields["storyPoints"] = int(sp) if sp == int(sp) else sp
        except ValueError:
            return {"error": f"Invalid story_points: {story_points!r}"}
    if sprint:
        fields["sprint"] = sprint
    if parent:
        fields["parent"] = parent
    if project:
        fields["project"] = project
    if components:
        comp_list = parse_tags(components)
        if comp_list:
            fields["components"] = comp_list
    if custom_fields:
        extra = parse_custom_fields_json(custom_fields)
        if "error" in extra:
            return extra
        fields.update(extra)
    return fields
