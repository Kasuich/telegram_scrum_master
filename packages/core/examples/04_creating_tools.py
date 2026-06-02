"""
Example 04: Creating and registering platform tools.

Shows @platform_tool decorator, ToolRegistry, schema generation, and execution.
Run: python -m examples.04_creating_tools  (from packages/core/)
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/pm_agent")
os.environ.setdefault("YC_API_KEY", "example_api_key_12345678901234567890")
os.environ.setdefault("YC_FOLDER_ID", "b1g1234567890abcdef")
os.environ.setdefault("TRACKER_TOKEN", "example_oauth_token_12345678901234567890")
os.environ.setdefault("TRACKER_ORG_ID", "12345678901234567890")

from core.exceptions import ToolNotFoundError, ToolValidationError
from core.tools import get_registry, platform_tool

get_registry().clear()


# --- Define tools ---


@platform_tool(name="tracker_create_issue", risk="medium", scopes=["tracker:write"])
async def create_issue(queue: str, summary: str, description: str = "") -> dict[str, Any]:
    """Create a new issue in Yandex Tracker."""
    # In reality: call Tracker API
    return {
        "key": f"{queue}-42",
        "summary": summary,
        "description": description,
        "status": "open",
    }


@platform_tool(name="tracker_get_issue", risk="low", scopes=["tracker:read"])
async def get_issue(issue_key: str) -> dict[str, Any]:
    """Get issue details from Yandex Tracker."""
    return {
        "key": issue_key,
        "summary": "Fix login bug",
        "status": "in_progress",
        "assignee": "alice",
    }


@platform_tool(name="tracker_close_issue", risk="high", scopes=["tracker:write"])
async def close_issue(issue_key: str, resolution: str = "fixed") -> dict[str, Any]:
    """Close an issue in Yandex Tracker."""
    return {"key": issue_key, "status": "closed", "resolution": resolution}


@platform_tool(name="notify_slack", risk="medium", scopes=["slack:write"])
def send_slack_message(channel: str, text: str) -> dict[str, Any]:
    """Send a message to a Slack channel."""
    return {"ok": True, "channel": channel, "ts": "1234567890.000"}


def demo_registry() -> None:
    print("=== Registry overview ===")
    registry = get_registry()
    all_tools = registry.list()
    print(f"Registered tools: {len(all_tools)}")
    for tool in all_tools:
        print(f"  [{tool.risk:6}] {tool.name} — {tool.description}")


def demo_scope_filtering() -> None:
    print("\n=== Scope filtering ===")
    registry = get_registry()

    read_tools = registry.list(scopes=["tracker:read"])
    write_tools = registry.list(scopes=["tracker:write"])
    print(f"tracker:read  → {[t.name for t in read_tools]}")
    print(f"tracker:write → {[t.name for t in write_tools]}")


def demo_schema_generation() -> None:
    print("\n=== OpenAPI schemas ===")
    registry = get_registry()
    schemas = registry.get_schemas()
    for schema in schemas:
        params = schema["parameters"]
        required = params.get("required", [])
        props = list(params["properties"].keys())
        print(f"{schema['name']}: required={required}, all_params={props}")


async def demo_execution() -> None:
    print("\n=== Tool execution ===")
    registry = get_registry()

    # Execute via registry
    tool = registry.get("tracker_create_issue")
    result = await tool.execute(queue="TEST", summary="Add dark mode")
    print(f"Created: {result}")

    # Execute sync tool
    slack_tool = registry.get("notify_slack")
    result = slack_tool.execute(channel="#alerts", text="Issue created!")
    print(f"Slack: {result}")


def demo_validation() -> None:
    print("\n=== Validation errors ===")
    registry = get_registry()
    tool = registry.get("tracker_create_issue")

    try:
        tool.validate_arguments({"queue": "TEST"})  # missing required 'summary'
    except ToolValidationError as e:
        print(f"Missing required: {e}")

    try:
        registry.get("nonexistent_tool")
    except ToolNotFoundError as e:
        print(f"Not found: {e}")


def main() -> None:
    demo_registry()
    demo_scope_filtering()
    demo_schema_generation()
    asyncio.run(demo_execution())
    demo_validation()


if __name__ == "__main__":
    main()
