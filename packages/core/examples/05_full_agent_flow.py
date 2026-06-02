"""
Example 05: Full agent flow — config → LLM → tool → logging.

Demonstrates the complete request lifecycle:
1. Load team config
2. Get available tools
3. Call LLM with tool schemas
4. Parse tool call from response
5. Execute the tool
6. Log the action with trace ID

Run: python -m examples.05_full_agent_flow  (from packages/core/)
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/pm_agent")
os.environ.setdefault("YC_API_KEY", "example_api_key_12345678901234567890")
os.environ.setdefault("YC_FOLDER_ID", "b1g1234567890abcdef")
os.environ.setdefault("TRACKER_TOKEN", "example_oauth_token_12345678901234567890")
os.environ.setdefault("TRACKER_ORG_ID", "12345678901234567890")

from core.logging import configure_logging, get_logger, set_trace_id
from core.tools import get_registry, platform_tool

configure_logging("INFO")
logger = get_logger(__name__)

get_registry().clear()

MOCK_TOOL_CALL_RESPONSE = {
    "result": {
        "message": {
            "functionCall": {
                "name": "create_tracker_issue",
                "args": {
                    "queue": "BACKEND",
                    "summary": "Optimize database queries",
                    "priority": "high",
                },
            }
        },
        "usage": {"inputTokensCount": 80, "outputTokensCount": 25, "totalTokensCount": 105},
        "status": "COMPLETED",
    }
}


# --- Register tools for this agent ---


@platform_tool(name="create_tracker_issue", risk="medium", scopes=["tracker:write"])
async def create_tracker_issue(
    queue: str,
    summary: str,
    priority: str = "normal",
) -> dict[str, Any]:
    """Create a Yandex Tracker issue."""
    logger.info(f"Creating issue: {summary}", extra={"queue": queue, "priority": priority})
    return {
        "key": f"{queue}-101",
        "summary": summary,
        "priority": priority,
        "url": f"https://tracker.yandex.ru/{queue}-101",
    }


@platform_tool(name="notify_team", risk="low", scopes=["slack:write"])
async def notify_team(channel: str, message: str) -> dict[str, Any]:
    """Notify team in Slack."""
    return {"ok": True, "channel": channel}


async def run_agent_turn(user_message: str, team_id: str) -> dict[str, Any]:
    """Simulate a single agent turn: receive message → decide → act."""
    from core.config import Config
    from core.llm import LLMClient, Message
    from core.prompts import PM_AGENT_SYSTEM_PROMPT, format_tool_descriptions

    # Step 1: Set trace ID for this request
    trace_id = str(uuid.uuid4())
    set_trace_id(trace_id)
    logger.info("Agent turn started", extra={"team_id": team_id, "user_message": user_message})

    # Step 2: Load team config
    config = Config.for_team(team_id, auto_risk=["low", "medium"])
    logger.info(f"Config loaded for team: {team_id}")

    # Step 3: Get available tools
    registry = get_registry()
    tool_schemas = registry.get_schemas()
    logger.info(f"Available tools: {[s['name'] for s in tool_schemas]}")

    # Step 4: Build messages with system prompt
    tools_description = format_tool_descriptions(tool_schemas)
    messages = [
        Message(role="system", content=f"{PM_AGENT_SYSTEM_PROMPT}\n\n{tools_description}"),
        Message(role="user", content=user_message),
    ]

    # Step 5: Call LLM (mocked for this example)
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_TOOL_CALL_RESPONSE
    mock_resp.raise_for_status = MagicMock()
    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_resp)

    with patch("core.llm.LLMClient.client") as prop:
        prop.__get__ = MagicMock(return_value=mock_http)

        client = LLMClient(
            temperature=config.llm.yandexgpt_temperature,
            max_tokens=config.llm.yandexgpt_max_tokens,
        )
        response = await client.complete(messages, tools=tool_schemas)
        await client.close()

    logger.info(
        "LLM response received",
        extra={
            "has_tool_call": response.tool_calls is not None,
            "tokens": response.usage.total_tokens if response.usage else 0,
        },
    )

    # Step 6: Execute tool call if present
    if response.tool_calls:
        tool_call = response.tool_calls[0]
        tool = registry.get(tool_call.name)

        # Autonomy check: is this risk level auto-approved?
        if tool.risk in config.runtime.auto_risk:
            logger.info(f"Auto-executing tool: {tool.name} (risk={tool.risk})")
            validated_args = tool.validate_arguments(tool_call.arguments)
            result = await tool.execute(**validated_args)
            logger.info("Tool executed successfully", extra={"result": result})
            return {"status": "completed", "tool": tool.name, "result": result}
        else:
            logger.info(f"Tool requires confirmation: {tool.name} (risk={tool.risk})")
            return {
                "status": "pending_confirmation",
                "tool": tool.name,
                "args": tool_call.arguments,
            }

    return {"status": "text_response", "content": response.content}


async def main() -> None:
    print("=== Full Agent Flow Demo ===\n")

    result = await run_agent_turn(
        user_message="Create a task to optimize database queries with high priority",
        team_id="team_backend",
    )

    print("\nFinal result:")
    for key, value in result.items():
        print(f"  {key}: {value}")

    get_registry().clear()


if __name__ == "__main__":
    asyncio.run(main())
