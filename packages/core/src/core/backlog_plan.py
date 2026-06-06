"""Backlog planning models and LLM extraction for meeting summaries."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator

BACKLOG_PLAN_SYSTEM_PROMPT = """Ты — планировщик бэклога для Яндекс Трекера.
Из текста саммари/лекции/созвона извлеки структурированный план задач.

Верни ТОЛЬКО валидный JSON (без markdown, без пояснений) по схеме:
{
  "create_epic": true,
  "rationale": "почему эпик да или нет",
  "epic": {"local_id": "epic-1", "issue_type": "epic", "summary": "...", "description": "...", ...},
  "stories": [ {...}, ... ],
  "tasks": [ {...}, ... ]
}

Поля каждой сущности (PlannedIssue):
- local_id: уникальный id внутри плана (epic-1, story-mvp, task-3)
- issue_type: ключ из списка типов очереди (epic, story, task, bug)
- summary: короткое название на русском
- description: 1-3 предложения
- parent_local_id: local_id родителя или null
- priority: critical | normal | minor
- story_points: число Fibonacci 1-13 или null для epic
- tags: массив строк (exam-critical, mvp, integration, nice-to-have)
- assignee_hint: имя/логин только если явно в тексте, иначе ""
- order: порядок внутри родителя (0, 1, 2...)
- exam_critical: true для блоков «ВАЖНО ДЛЯ ЭКЗАМЕНА» / MVP

Правила:
1. Эпик — если ≥2 крупных потока работ или явное название проекта.
   Иначе create_epic=false, epic=null.
2. Stories — крупные разделы (MVP, интеграции, доп. функции).
3. Tasks — проверяемые действия (глагол + результат), 10-25 штук для большого саммари.
4. priority=critical для MVP и «важно для экзамена»; minor для nice-to-have.
5. Не выдумывай исполнителей.
6. Используй ключи типов и приоритетов из метаданных очереди ниже.
   Если списки пустые — epic, story, task, bug и critical, normal, minor.
7. Всегда заполняй tasks (10–25 для большого саммари). Пустой tasks[] недопустим.
"""

DEFAULT_ISSUE_TYPES = [
    {"key": "epic", "name": "Epic"},
    {"key": "story", "name": "Story"},
    {"key": "task", "name": "Task"},
    {"key": "bug", "name": "Bug"},
]
DEFAULT_PRIORITIES = [
    {"key": "critical", "name": "Critical"},
    {"key": "normal", "name": "Normal"},
    {"key": "minor", "name": "Minor"},
]


class PlannedIssue(BaseModel):
    local_id: str
    issue_type: str = "task"
    summary: str
    description: str = ""
    parent_local_id: str | None = None
    priority: str = "normal"
    story_points: int | None = None
    tags: list[str] = Field(default_factory=list)
    assignee_hint: str = ""
    order: int = 0
    exam_critical: bool = False

    @field_validator("story_points", mode="before")
    @classmethod
    def _coerce_sp(cls, v: Any) -> int | None:
        if v is None or v == "":
            return None
        return int(v)


class BacklogPlan(BaseModel):
    create_epic: bool = False
    epic: PlannedIssue | None = None
    stories: list[PlannedIssue] = Field(default_factory=list)
    tasks: list[PlannedIssue] = Field(default_factory=list)
    rationale: str = ""

    def all_issues(self) -> list[PlannedIssue]:
        out: list[PlannedIssue] = []
        if self.create_epic and self.epic:
            out.append(self.epic)
        out.extend(self.stories)
        out.extend(self.tasks)
        return out

    def preview_lines(self) -> list[str]:
        lines = [f"Эпик: {'да' if self.create_epic else 'нет'} — {self.rationale}"]
        if self.epic:
            lines.append(f"  {self.epic.local_id}: {self.epic.summary}")
        for s in self.stories:
            lines.append(f"  story {s.local_id}: {s.summary} (SP={s.story_points})")
        lines.append(f"  задач: {len(self.tasks)}")
        critical = [t for t in self.tasks if t.exam_critical or t.priority == "critical"]
        if critical:
            lines.append(f"  critical: {len(critical)}")
        return lines


def extract_json_from_text(text: str) -> dict[str, Any]:
    """Parse JSON from LLM output (raw or fenced code block)."""
    text = text.strip()
    if not text:
        raise ValueError("Empty LLM response")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Plan JSON must be an object")
    return data


def parse_backlog_plan(data: dict[str, Any]) -> BacklogPlan:
    return BacklogPlan.model_validate(data)


def ensure_queue_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """Fill default issue types/priorities when Tracker meta is empty."""
    out = dict(meta)
    if not out.get("issue_types"):
        out["issue_types"] = list(DEFAULT_ISSUE_TYPES)
    if not out.get("priorities"):
        out["priorities"] = list(DEFAULT_PRIORITIES)
    return out


def plan_has_issues(plan: BacklogPlan) -> bool:
    if plan.tasks:
        return True
    if plan.stories:
        return True
    return bool(plan.create_epic and plan.epic)


def build_plan_user_message(
    text: str,
    *,
    queue_meta: dict[str, Any],
    project_title: str = "",
) -> str:
    meta = ensure_queue_meta(queue_meta)
    types = meta.get("issue_types") or []
    priorities = meta.get("priorities") or []
    meta_block = json.dumps(
        {"issue_types": types, "priorities": priorities},
        ensure_ascii=False,
        indent=2,
    )
    title_line = f"Название проекта: {project_title}\n\n" if project_title else ""
    return (
        f"{title_line}"
        f"Метаданные очереди {queue_meta.get('queue_key', '')}:\n{meta_block}\n\n"
        f"Текст саммари:\n{text}"
    )


async def generate_backlog_plan(
    text: str,
    *,
    queue_meta: dict[str, Any],
    project_title: str = "",
) -> BacklogPlan:
    from core.llm import LLMClient, Message

    client = LLMClient(temperature=0.1, max_tokens=8000)
    try:
        response = await client.complete(
            [
                Message(role="system", content=BACKLOG_PLAN_SYSTEM_PROMPT),
                Message(
                    role="user",
                    content=build_plan_user_message(
                        text, queue_meta=queue_meta, project_title=project_title
                    ),
                ),
            ]
        )
    finally:
        await client.close()

    raw = response.content or ""
    data = extract_json_from_text(raw)
    return parse_backlog_plan(data)


def resolve_issue_type_key(requested: str, available: set[str]) -> tuple[str, list[str]]:
    """Map planned type to queue key; extra tags when falling back to task."""
    req = requested.strip().lower()
    extra_tags: list[str] = []
    if req in available:
        return req, extra_tags
    if req == "epic" and "task" in available:
        return "task", ["epic"]
    if req == "story" and "task" in available:
        return "task", ["story"]
    if "task" in available:
        return "task", extra_tags
    if available:
        return next(iter(available)), extra_tags
    return "task", extra_tags


def resolve_priority_key(requested: str, available: set[str]) -> str:
    req = requested.strip().lower()
    if not req:
        req = "normal"
    if req in available:
        return req
    aliases = {
        "critical": ("critical", "blocker", "major"),
        "normal": ("normal", "medium"),
        "minor": ("minor", "trivial", "low"),
    }
    for key, variants in aliases.items():
        if req in variants and key in available:
            return key
    if "normal" in available:
        return "normal"
    if available:
        return next(iter(available))
    return req or "normal"
