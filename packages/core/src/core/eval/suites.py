"""Eval suite definitions and fixture cases."""

from __future__ import annotations

from core.eval.schemas import EvalSuite, SyntheticScenario

SUITE_PROMPTS: dict[str, str] = {
    EvalSuite.CREATE_TASK.value: "Агент должен создать одну задачу с корректными полями.",
    EvalSuite.UPDATE_TASK.value: "Агент должен обновить существующую задачу, не создавая новую.",
    EvalSuite.MULTI_TASK.value: (
        "Агент должен создать несколько отдельных задач из одного сообщения."
    ),
    EvalSuite.HIERARCHY.value: "Агент должен создать родительскую и дочерние задачи со связями.",
    EvalSuite.DUPLICATE_SEARCH.value: "Агент должен найти дубль и не создавать новую задачу.",
    EvalSuite.NO_TASK.value: "Агент не должен создавать или обновлять задачи.",
}

FIXTURE_CASES: list[SyntheticScenario] = [
    SyntheticScenario(
        goal="Создать задачу по запросу",
        expected_behavior="Одна create_task с summary и description",
        suite=EvalSuite.CREATE_TASK.value,
        difficulty="easy",
        initial_state={"tasks": []},
        expected_operations=[{"operation": "create_task"}],
        forbidden_operations=[{"operation": "update_task"}],
    ),
    SyntheticScenario(
        goal="Обновить существующую задачу",
        expected_behavior="update_task для SUPPORT-10",
        suite=EvalSuite.UPDATE_TASK.value,
        difficulty="medium",
        initial_state={
            "tasks": [
                {
                    "key": "SUPPORT-10",
                    "summary": "Старый заголовок",
                    "description": "Описание",
                    "status": "open",
                }
            ]
        },
        expected_operations=[{"operation": "update_task", "match": {"task_key": "SUPPORT-10"}}],
        forbidden_operations=[{"operation": "create_task"}],
    ),
    SyntheticScenario(
        goal="Найти дубль",
        expected_behavior="search + comment, без create",
        suite=EvalSuite.DUPLICATE_SEARCH.value,
        difficulty="medium",
        initial_state={
            "tasks": [
                {
                    "key": "SUPPORT-101",
                    "summary": "Ошибка авторизации у клиента Альфа",
                    "description": "Клиент не может войти.",
                    "status": "open",
                }
            ]
        },
        expected_operations=[
            {"operation": "search_tasks"},
            {"operation": "comment_task", "match": {"task_key": "SUPPORT-101"}},
        ],
        forbidden_operations=[{"operation": "create_task"}],
    ),
    SyntheticScenario(
        goal="Нет actionable intent",
        expected_behavior="noop или ask_clarification",
        suite=EvalSuite.NO_TASK.value,
        difficulty="easy",
        initial_state={"tasks": []},
        expected_operations=[],
        forbidden_operations=[
            {"operation": "create_task"},
            {"operation": "update_task"},
            {"operation": "comment_task"},
        ],
    ),
    SyntheticScenario(
        goal="Создать две отдельные задачи из одного сообщения",
        expected_behavior="Две create_task: подготовить презентацию и согласовать бюджет",
        suite=EvalSuite.MULTI_TASK.value,
        difficulty="medium",
        initial_state={"tasks": []},
        expected_operations=[
            {"operation": "create_task"},
            {"operation": "create_task"},
        ],
        forbidden_operations=[{"operation": "update_task"}],
    ),
    SyntheticScenario(
        goal="Создать задачу «Интеграция API», изменить её приоритет, добавить подзадачи",
        expected_behavior="create_task, update_task родителя, create_task подзадачи с parent",
        suite=EvalSuite.HIERARCHY.value,
        difficulty="hard",
        initial_state={"tasks": []},
        expected_operations=[
            {"operation": "create_task"},
            {"operation": "update_task"},
            {"operation": "create_task", "match": {"parent": "*"}},
            {"operation": "create_task", "match": {"parent": "*"}},
        ],
        forbidden_operations=[{"operation": "delete_task"}],
    ),
]


def distribute_suites(n_cases: int, suites: list[str]) -> list[tuple[str, str]]:
    """Return list of (suite, difficulty) for n_cases."""
    if not suites:
        suites = [s.value for s in EvalSuite]
    difficulties = ["easy", "medium", "hard"]
    out: list[tuple[str, str]] = []
    for i in range(n_cases):
        suite = suites[i % len(suites)]
        diff = difficulties[i % len(difficulties)]
        out.append((suite, diff))
    return out
