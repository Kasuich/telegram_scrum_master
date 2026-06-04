"""
Tests for the code-first agent framework:
  core.agent  — LLMSettings, BaseAgent, AgentResponse
  core.bot    — BaseBot
  core.entry_point — EntryPoint
  core.registry   — BotRegistry
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from core.agent import AgentResponse, BaseAgent, LLMSettings
from core.bot import BaseBot
from core.entry_point import EntryPoint
from core.exceptions import AgentError, RegistryError
from core.llm import Message
from core.registry import BotRegistry, get_bot_registry
from core.tools import ToolRegistry, platform_tool

# ---------------------------------------------------------------------------
# ENV stub (mirrors test_llm.py)
# ---------------------------------------------------------------------------

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "YC_API_KEY": "test_api_key_12345678901234567890",
    "YC_FOLDER_ID": "b1g1234567890abcdef",
    "TRACKER_TOKEN": "test_tracker_token_12345678901234567890",
    "TRACKER_ORG_ID": "12345678901234567890",
}

# ---------------------------------------------------------------------------
# Mock YandexGPT responses
# ---------------------------------------------------------------------------

MOCK_TEXT_RESPONSE = {
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "All good!"}],
        }
    ],
    "output_text": "All good!",
    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    "status": "completed",
}

MOCK_TOOL_CALL_RESPONSE = {
    "output": [
        {
            "type": "function_call",
            "call_id": "fc_1",
            "name": "my_tool",
            "arguments": '{"param": "value"}',
        }
    ],
    "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
    "status": "completed",
}


def _http_ok(data: dict[str, Any]) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = 200
    resp.json.return_value = data
    resp.text = json.dumps(data)
    resp.raise_for_status = MagicMock()
    return resp


def _http_error(status: int) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = {}
    resp.text = "error"

    def _raise() -> None:
        raise httpx.HTTPStatusError("err", request=MagicMock(), response=resp)

    resp.raise_for_status = _raise
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registries():
    """Reset singleton registries between tests."""
    BotRegistry().clear()
    ToolRegistry().clear()
    yield
    BotRegistry().clear()
    ToolRegistry().clear()


@pytest.fixture
def simple_agent_cls():
    """Return a minimal concrete BaseAgent subclass."""

    class _Agent(BaseAgent):
        name = "test_agent"
        description = "A test agent"
        prompt = "You are a test assistant."
        tools = []
        llm_configs = [LLMSettings(model="yandexgpt-lite", temperature=0.5)]

    return _Agent


@pytest.fixture
def agent(simple_agent_cls):
    return simple_agent_cls()


@pytest.fixture
def entry_point(agent):
    return EntryPoint(agent)


# ---------------------------------------------------------------------------
# LLMSettings
# ---------------------------------------------------------------------------


class TestLLMSettings:
    def test_defaults(self):
        s = LLMSettings()
        assert s.model == "gpt-oss-120b"
        assert s.temperature is None
        assert s.max_tokens is None

    def test_to_client_kwargs_full(self):
        s = LLMSettings(model="yandexgpt-lite", temperature=0.3, max_tokens=1000, timeout=30)
        kw = s.to_client_kwargs()
        assert kw["model"] == "yandexgpt-lite"
        assert kw["temperature"] == 0.3
        assert kw["max_tokens"] == 1000
        assert kw["timeout"] == 30

    def test_to_client_kwargs_skips_none(self):
        s = LLMSettings(model="yandexgpt-pro")
        kw = s.to_client_kwargs()
        assert "temperature" not in kw
        assert "max_tokens" not in kw


# ---------------------------------------------------------------------------
# BaseAgent — class validation
# ---------------------------------------------------------------------------


class TestBaseAgentClassValidation:
    def test_missing_name_raises(self):
        with pytest.raises(AgentError, match="must define a non-empty 'name'"):

            class _Bad(BaseAgent):
                name = ""
                description = "x"
                prompt = "x"

    def test_valid_subclass(self, simple_agent_cls):
        agent = simple_agent_cls()
        assert agent.name == "test_agent"

    def test_default_llm_configs_used_when_empty(self):
        class _Agent(BaseAgent):
            name = "no_cfg_agent"
            description = "x"
            prompt = "x"
            llm_configs = []

        a = _Agent()
        cfgs = a._effective_llm_configs()
        assert len(cfgs) == 1
        assert cfgs[0].model == "gpt-oss-120b"


# ---------------------------------------------------------------------------
# BaseAgent — prompt rendering
# ---------------------------------------------------------------------------


class TestBaseAgentPrompt:
    def test_prompt_no_vars(self, agent):
        msg = agent._build_system_message(None)
        assert msg.role == "system"
        assert msg.content == "You are a test assistant."

    def test_prompt_with_vars(self):
        class _A(BaseAgent):
            name = "var_agent"
            description = "x"
            prompt = "Hello {user}, today is {date}."

        a = _A()
        msg = a._build_system_message({"user": "Alice", "date": "2026-06-03"})
        assert "Alice" in msg.content
        assert "2026-06-03" in msg.content

    def test_prompt_missing_var_raises(self):
        class _A(BaseAgent):
            name = "missing_var_agent"
            description = "x"
            prompt = "Hello {user}."

        with pytest.raises(AgentError, match="Missing prompt variable"):
            _A()._build_system_message({})


# ---------------------------------------------------------------------------
# BaseAgent — tool schema resolution
# ---------------------------------------------------------------------------


class TestBaseAgentToolResolution:
    def test_no_tools_returns_empty(self, agent):
        schemas = agent._resolve_tool_schemas()
        assert schemas == []

    @patch.dict("os.environ", ENV)
    def test_registered_tool_resolved(self):
        @platform_tool(name="ping", risk="low")
        def ping() -> str:
            "Ping the system."
            return "pong"

        class _A(BaseAgent):
            name = "ping_agent"
            description = "x"
            prompt = "x"
            tools = ["ping"]

        schemas = _A()._resolve_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "ping"

    def test_missing_tool_skipped_with_warning(self, caplog):
        import logging

        class _A(BaseAgent):
            name = "ghost_agent"
            description = "x"
            prompt = "x"
            tools = ["nonexistent_tool"]

        with caplog.at_level(logging.WARNING):
            schemas = _A()._resolve_tool_schemas()
        assert schemas == []
        assert "nonexistent_tool" in caplog.text


# ---------------------------------------------------------------------------
# BaseAgent — run() with mocked LLM
# ---------------------------------------------------------------------------


class TestBaseAgentRun:
    @patch.dict("os.environ", ENV)
    async def test_run_text_response(self, agent):
        mock_post = AsyncMock(return_value=_http_ok(MOCK_TEXT_RESPONSE))
        with patch("httpx.AsyncClient.post", mock_post):
            resp = await agent.run([Message(role="user", content="Hello")])

        assert isinstance(resp, AgentResponse)
        assert resp.content == "All good!"
        assert resp.tool_calls is None
        assert resp.llm_attempts == 1

    @patch.dict("os.environ", ENV)
    async def test_run_tool_call_response(self, agent):
        mock_post = AsyncMock(return_value=_http_ok(MOCK_TOOL_CALL_RESPONSE))
        with patch("httpx.AsyncClient.post", mock_post):
            resp = await agent.run([Message(role="user", content="Do something")])

        assert resp.content is None
        assert resp.tool_calls is not None
        assert resp.tool_calls[0]["name"] == "my_tool"
        assert resp.tool_calls[0]["arguments"] == {"param": "value"}

    @patch.dict("os.environ", ENV)
    async def test_run_missing_prompt_raises(self):
        class _A(BaseAgent):
            name = "no_prompt_agent"
            description = "x"
            prompt = ""

        with pytest.raises(AgentError, match="no prompt"):
            await _A().run([Message(role="user", content="hi")])

    @patch.dict("os.environ", ENV)
    async def test_run_with_prompt_vars(self):
        class _A(BaseAgent):
            name = "var_run_agent"
            description = "x"
            prompt = "You are {role}."
            llm_configs = [LLMSettings(model="yandexgpt-lite")]

        mock_post = AsyncMock(return_value=_http_ok(MOCK_TEXT_RESPONSE))
        with patch("httpx.AsyncClient.post", mock_post):
            resp = await _A().run(
                [Message(role="user", content="hi")],
                prompt_vars={"role": "a tester"},
            )
        assert resp.content == "All good!"


# ---------------------------------------------------------------------------
# BaseAgent — LLM fallback
# ---------------------------------------------------------------------------


class TestBaseAgentFallback:
    @patch.dict("os.environ", ENV)
    async def test_fallback_to_second_model(self):
        # max_retries=0 so the first model fails immediately without internal retries,
        # triggering BaseAgent's fallback chain to the second model.
        class _A(BaseAgent):
            name = "fallback_agent"
            description = "x"
            prompt = "x"
            llm_configs = [
                LLMSettings(model="yandexgpt-pro", max_retries=0),
                LLMSettings(model="yandexgpt-lite", max_retries=0),
            ]

        first_call = True

        async def _post_side_effect(*args, **kwargs):
            nonlocal first_call
            if first_call:
                first_call = False
                return _http_error(503)
            return _http_ok(MOCK_TEXT_RESPONSE)

        with patch("httpx.AsyncClient.post", side_effect=_post_side_effect):
            resp = await _A().run([Message(role="user", content="hi")])

        assert resp.content == "All good!"
        assert resp.llm_attempts == 2

    @patch.dict("os.environ", ENV)
    async def test_all_models_fail_raises_agent_error(self):
        class _A(BaseAgent):
            name = "all_fail_agent"
            description = "x"
            prompt = "x"
            llm_configs = [
                LLMSettings(model="yandexgpt-pro"),
                LLMSettings(model="yandexgpt-lite"),
            ]

        with patch("httpx.AsyncClient.post", return_value=_http_error(503)):
            with pytest.raises(AgentError, match="all.*LLM config"):
                await _A().run([Message(role="user", content="hi")])


# ---------------------------------------------------------------------------
# BotRegistry
# ---------------------------------------------------------------------------


class TestBotRegistry:
    def test_empty_at_start(self):
        assert get_bot_registry().list_all() == []

    def test_register_and_get(self, simple_agent_cls):
        bot = BaseBot(
            bot_id="reg_test_bot",
            name="Reg Test",
            entry_point=EntryPoint(simple_agent_cls()),
        )
        reg = get_bot_registry()
        assert reg.get("reg_test_bot") is bot

    def test_duplicate_raises(self, simple_agent_cls):
        BaseBot(
            bot_id="dup_bot",
            name="First",
            entry_point=EntryPoint(simple_agent_cls()),
        )
        with pytest.raises(RegistryError, match="already registered"):
            BaseBot(
                bot_id="dup_bot",
                name="Second",
                entry_point=EntryPoint(simple_agent_cls()),
            )

    def test_get_missing_raises(self):
        with pytest.raises(RegistryError, match="not found"):
            get_bot_registry().get("no_such_bot")

    def test_list_for_platform(self, simple_agent_cls):
        BaseBot(
            bot_id="web_bot",
            name="Web",
            entry_point=EntryPoint(simple_agent_cls()),
            platforms=["web"],
        )
        BaseBot(
            bot_id="tg_bot",
            name="Telegram",
            entry_point=EntryPoint(simple_agent_cls()),
            platforms=["telegram"],
        )
        reg = get_bot_registry()
        web_bots = reg.list_for_platform("web")
        assert len(web_bots) == 1
        assert web_bots[0].bot_id == "web_bot"

    def test_exists(self, simple_agent_cls):
        reg = get_bot_registry()
        assert not reg.exists("exists_bot")
        BaseBot(
            bot_id="exists_bot",
            name="x",
            entry_point=EntryPoint(simple_agent_cls()),
        )
        assert reg.exists("exists_bot")

    def test_unregister(self, simple_agent_cls):
        BaseBot(
            bot_id="del_bot",
            name="x",
            entry_point=EntryPoint(simple_agent_cls()),
        )
        reg = get_bot_registry()
        reg.unregister("del_bot")
        assert not reg.exists("del_bot")


# ---------------------------------------------------------------------------
# BaseBot
# ---------------------------------------------------------------------------


class TestBaseBot:
    def test_auto_registers_on_init(self, simple_agent_cls):
        bot = BaseBot(
            bot_id="auto_reg_bot",
            name="Auto",
            entry_point=EntryPoint(simple_agent_cls()),
            platforms=["web"],
            description="Test bot",
        )
        assert get_bot_registry().get("auto_reg_bot") is bot

    def test_repr(self, simple_agent_cls):
        bot = BaseBot(
            bot_id="repr_bot",
            name="Repr",
            entry_point=EntryPoint(simple_agent_cls()),
            platforms=["web"],
        )
        assert "repr_bot" in repr(bot)
        assert "Repr" in repr(bot)

    def test_default_platforms_empty(self, simple_agent_cls):
        bot = BaseBot(
            bot_id="no_plat_bot",
            name="NP",
            entry_point=EntryPoint(simple_agent_cls()),
        )
        assert bot.platforms == []


# ---------------------------------------------------------------------------
# EntryPoint — agent mode
# ---------------------------------------------------------------------------


class TestEntryPointAgentMode:
    def test_mode_is_agent(self, agent, entry_point):
        assert entry_point.mode == "agent"

    def test_commands_empty_in_agent_mode(self, entry_point):
        assert entry_point.commands == {}

    @patch.dict("os.environ", ENV)
    async def test_invoke_routes_to_agent(self, agent):
        ep = EntryPoint(agent)
        mock_post = AsyncMock(return_value=_http_ok(MOCK_TEXT_RESPONSE))
        with patch("httpx.AsyncClient.post", mock_post):
            resp = await ep.invoke("Hello agent")
        assert resp.content == "All good!"

    async def test_invoke_empty_message_empty_history_raises(self, entry_point):
        with pytest.raises(AgentError):
            await entry_point.invoke("")

    @patch.dict("os.environ", ENV)
    async def test_invoke_with_history(self, agent):
        ep = EntryPoint(agent)
        history = [
            Message(role="user", content="prev msg"),
            Message(role="assistant", content="ok"),
        ]
        mock_post = AsyncMock(return_value=_http_ok(MOCK_TEXT_RESPONSE))
        with patch("httpx.AsyncClient.post", mock_post):
            resp = await ep.invoke("new message", history=history)
        assert resp.content == "All good!"

    def test_invalid_config_raises(self):
        with pytest.raises(AgentError, match="BaseAgent or dict"):
            EntryPoint("not_an_agent")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# EntryPoint — menu mode
# ---------------------------------------------------------------------------


class TestEntryPointMenuMode:
    @pytest.fixture
    def second_agent(self):
        class _B(BaseAgent):
            name = "second_agent"
            description = "Second agent"
            prompt = "You are second."
            llm_configs = [LLMSettings(model="yandexgpt-lite")]

        return _B()

    @pytest.fixture
    def menu_ep(self, agent, second_agent):
        return EntryPoint({"first": agent, "second": second_agent})

    def test_mode_is_menu(self, menu_ep):
        assert menu_ep.mode == "menu"

    def test_commands_populated(self, menu_ep):
        assert "first" in menu_ep.commands
        assert "second" in menu_ep.commands

    async def test_help_command(self, menu_ep):
        resp = await menu_ep.invoke("/help")
        assert "first" in (resp.content or "")
        assert "second" in (resp.content or "")

    async def test_start_command(self, menu_ep):
        resp = await menu_ep.invoke("/start")
        assert resp.content is not None

    async def test_unknown_command(self, menu_ep):
        resp = await menu_ep.invoke("/unknown hello")
        assert "Unknown command" in (resp.content or "")

    @patch.dict("os.environ", ENV)
    async def test_known_command_routes_correctly(self, menu_ep):
        mock_post = AsyncMock(return_value=_http_ok(MOCK_TEXT_RESPONSE))
        with patch("httpx.AsyncClient.post", mock_post):
            resp = await menu_ep.invoke("/first do something")
        assert resp.content == "All good!"

    @patch.dict("os.environ", ENV)
    async def test_no_command_prefix_routes_to_default(self, menu_ep):
        mock_post = AsyncMock(return_value=_http_ok(MOCK_TEXT_RESPONSE))
        with patch("httpx.AsyncClient.post", mock_post):
            resp = await menu_ep.invoke("plain message without command")
        assert resp.content == "All good!"

    def test_empty_menu_raises(self):
        with pytest.raises(AgentError, match="must not be empty"):
            EntryPoint({})
