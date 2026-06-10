from pm_orchestrator.agents.pm_agent import PROMPT


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


def test_prompt_explains_tool_schema_and_bulk_completion():
    assert "JSON-схему инструмента" in PROMPT
    assert "WaitForBulkChange" in PROMPT
    assert "Запуск bulk ещё не" in PROMPT
    assert "означает готовый результат" in PROMPT
