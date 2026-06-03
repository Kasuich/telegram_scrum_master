"""
Example: code-first agent framework.

Run (requires env vars from .env.example):

    uv run python examples/06_code_first_agents.py
"""

from __future__ import annotations

import asyncio
import os

# Minimal env stub so the example can be imported without a real .env
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("YC_API_KEY", "stub")
os.environ.setdefault("YC_FOLDER_ID", "stub")
os.environ.setdefault("TRACKER_TOKEN", "stub_token_000000000000")
os.environ.setdefault("TRACKER_ORG_ID", "stub")

from core.agent import BaseAgent, LLMSettings  # noqa: E402
from core.bot import BaseBot  # noqa: E402
from core.entry_point import EntryPoint  # noqa: E402
from core.registry import get_bot_registry  # noqa: E402
from core.tools import platform_tool  # noqa: E402

# ---------------------------------------------------------------------------
# 1. Define tools
# ---------------------------------------------------------------------------


@platform_tool(name="get_sprint_status", risk="low", scopes=["tracker:read"])
async def get_sprint_status(queue: str) -> dict:
    "Return current sprint status for a queue."
    return {"queue": queue, "open": 5, "in_progress": 3, "done": 12}


@platform_tool(name="create_issue", risk="medium", scopes=["tracker:write"])
async def create_issue(queue: str, summary: str, priority: str = "normal") -> dict:
    "Create a Yandex Tracker issue."
    return {"key": f"{queue}-42", "summary": summary, "priority": priority}


# ---------------------------------------------------------------------------
# 2. Define agents as classes
# ---------------------------------------------------------------------------


class PMReportAgent(BaseAgent):
    """Reports sprint status for a queue."""

    name = "pm_report_agent"
    description = "Prepares sprint reports from Tracker data"
    prompt = "You are a PM assistant. Today is {current_date}. Answer concisely in Russian."
    tools = ["get_sprint_status"]
    llm_configs = [
        LLMSettings(model="yandexgpt", temperature=0.3),
        LLMSettings(model="yandexgpt-lite", temperature=0.3),  # fallback
    ]


class TaskCreatorAgent(BaseAgent):
    """Creates issues in Yandex Tracker."""

    name = "task_creator_agent"
    description = "Creates tasks and issues in Yandex Tracker"
    prompt = "You are a task manager. Create issues as requested by the user."
    tools = ["create_issue"]
    llm_configs = [
        LLMSettings(model="yandexgpt", temperature=0.1),
    ]


# ---------------------------------------------------------------------------
# 3. Wrap in a bot with menu-based EntryPoint
# ---------------------------------------------------------------------------

PM_BOT = BaseBot(
    bot_id="pm_bot",
    name="PM Bot",
    description="Project management assistant",
    entry_point=EntryPoint(
        {
            "report": PMReportAgent(),
            "task": TaskCreatorAgent(),
        }
    ),
    platforms=["web", "telegram"],
)


# ---------------------------------------------------------------------------
# 4. Demo: show registry contents and invoke the bot
# ---------------------------------------------------------------------------


async def main() -> None:
    registry = get_bot_registry()

    print("=== Registered bots ===")
    for bot in registry.list_all():
        print(f"  [{bot.bot_id}] {bot.name} — platforms: {bot.platforms}")

    print("\n=== EntryPoint commands ===")
    for cmd, agent in PM_BOT.entry_point.commands.items():
        print(f"  /{cmd} → {agent.name}: {agent.description}")

    print("\n=== Invoking /report (mocked) ===")
    # NOTE: This will fail without a real YC_API_KEY. In a real env it would
    # call YandexGPT.  Here we just show the request is correctly routed.
    try:
        response = await PM_BOT.entry_point.invoke(
            "/report Покажи статус спринта в очереди BACKEND",
            prompt_vars={"current_date": "2026-06-03"},
        )
        print(f"  content: {response.content}")
        print(f"  model used: {response.model_used}")
    except Exception as exc:
        print(f"  (expected in stub env) {type(exc).__name__}: {exc}")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
