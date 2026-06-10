from pm_orchestrator.agents.meeting_summarizer import PROMPT


def test_prompt_uses_lists_instead_of_tables() -> None:
    assert "Не используй markdown-таблицы" in PROMPT
    assert "Никогда не используй таблицы" in PROMPT
    assert "| # |" not in PROMPT
    assert "- Владелец:" in PROMPT
