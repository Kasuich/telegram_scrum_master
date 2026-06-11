from pm_orchestrator.agents.pm_agent import PROMPT, PMAgent


def test_pm_agent_exposes_epic_and_sprint_lifecycle_tools():
    expected = {
        "tracker_create_epic",
        "tracker_open_epic",
        "tracker_close_epic",
        "tracker_create_sprint",
        "tracker_open_sprint",
        "tracker_close_sprint",
        "tracker_rollover_sprint",
        "tracker_add_issues_to_sprint",
    }
    assert expected <= set(PMAgent.tools)


def test_prompt_describes_react_harness_and_observation_semantics():
    assert "ReAct harness" in PROMPT
    assert "Результат чтения — наблюдение" in PROMPT
    assert "не автоматическое завершение цели" in PROMPT
    assert "ОДИН tool call" in PROMPT


def test_prompt_covers_composite_update_and_analytics_use_cases():
    assert "Оцени мои задачи в SP" in PROMPT
    assert "BulkUpdate или UpdateIssue" in PROMPT
    assert "У кого больше задач" in PROMPT
    assert "Не выводи пустые строки вместо пользователей" in PROMPT
    assert "Не используй таблицы" in PROMPT


def test_prompt_explains_tool_schema_and_bulk_completion():
    assert "JSON-схему инструмента" in PROMPT
    assert "WaitForBulkChange" in PROMPT
    assert "Запуск bulk ещё не" in PROMPT
    assert "означает готовый результат" in PROMPT


def test_prompt_covers_sprint_lifecycle_and_auto_naming():
    assert "## Спринты" in PROMPT
    assert "tracker_create_sprint" in PROMPT
    # The agent must leave the name empty for auto-numbering, not invent one.
    assert "оставь" in PROMPT and "name" in PROMPT
    assert "Sprint N+1" in PROMPT
    assert "tracker_open_sprint" in PROMPT
    assert "tracker_close_sprint" in PROMPT
    assert "tracker_rollover_sprint" in PROMPT
