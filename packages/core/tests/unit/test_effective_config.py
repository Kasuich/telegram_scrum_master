"""
Tests for core.effective_config — build_effective_config pure function.
"""

from __future__ import annotations

from core.agent import BaseAgent, LLMSettings
from core.config import RuntimeConfig
from core.effective_config import EffectiveAgentConfig, build_effective_config

# ---------------------------------------------------------------------------
# Minimal agent fixture
# ---------------------------------------------------------------------------


class _Agent(BaseAgent):
    name = "test_agent"
    description = "Test"
    prompt = "You are a test agent."
    tools: list[str] = []
    llm_configs = [LLMSettings(model="gpt-oss-120b", temperature=0.3)]


# ---------------------------------------------------------------------------
# No overrides → class defaults
# ---------------------------------------------------------------------------


class TestNoOverrides:
    def test_prompt_from_class(self) -> None:
        cfg = build_effective_config(_Agent(), None, None)
        assert cfg.prompt == "You are a test agent."

    def test_model_from_class(self) -> None:
        cfg = build_effective_config(_Agent(), None, None)
        assert cfg.llm_configs[0].model == "gpt-oss-120b"

    def test_runtime_config_defaults(self) -> None:
        cfg = build_effective_config(_Agent(), None, None)
        assert cfg.runtime_config.auto_risk == ["low"]
        assert cfg.runtime_config.confirm_risk == ["medium", "high"]
        assert cfg.runtime_config.always_confirm_tools == []

    def test_returns_effective_agent_config(self) -> None:
        cfg = build_effective_config(_Agent(), None, None)
        assert isinstance(cfg, EffectiveAgentConfig)


# ---------------------------------------------------------------------------
# spec overrides class
# ---------------------------------------------------------------------------


class TestSpecOverrides:
    def test_prompt_from_spec(self) -> None:
        spec = {"prompt": "Spec prompt.", "model": None, "autonomy": {}}
        cfg = build_effective_config(_Agent(), spec, None)
        assert cfg.prompt == "Spec prompt."

    def test_model_from_spec(self) -> None:
        spec = {"prompt": "", "model": "yandexgpt-lite", "autonomy": {}}
        cfg = build_effective_config(_Agent(), spec, None)
        assert cfg.llm_configs[0].model == "yandexgpt-lite"

    def test_auto_risk_from_spec_autonomy(self) -> None:
        spec = {
            "prompt": "",
            "model": None,
            "autonomy": {"auto_risk": ["low", "medium"], "confirm_risk": ["high"]},
        }
        cfg = build_effective_config(_Agent(), spec, None)
        assert cfg.runtime_config.auto_risk == ["low", "medium"]
        assert cfg.runtime_config.confirm_risk == ["high"]

    def test_empty_spec_falls_back_to_class(self) -> None:
        cfg = build_effective_config(_Agent(), {}, None)
        assert cfg.prompt == "You are a test agent."


# ---------------------------------------------------------------------------
# overlay overrides spec overrides class
# ---------------------------------------------------------------------------


class TestOverlayOverrides:
    def test_prompt_overlay_beats_spec(self) -> None:
        spec = {"prompt": "Spec prompt.", "model": None, "autonomy": {}}
        overlay = {"prompt": "Overlay prompt."}
        cfg = build_effective_config(_Agent(), spec, overlay)
        assert cfg.prompt == "Overlay prompt."

    def test_model_overlay_beats_spec(self) -> None:
        spec = {"prompt": "", "model": "yandexgpt-lite", "autonomy": {}}
        overlay = {"model": "gpt-oss-120b"}
        cfg = build_effective_config(_Agent(), spec, overlay)
        assert cfg.llm_configs[0].model == "gpt-oss-120b"

    def test_confirm_risk_overlay(self) -> None:
        overlay = {"auto_risk": ["low", "medium"], "confirm_risk": ["high"]}
        cfg = build_effective_config(_Agent(), None, overlay)
        assert cfg.runtime_config.auto_risk == ["low", "medium"]
        assert cfg.runtime_config.confirm_risk == ["high"]

    def test_always_confirm_tools_overlay(self) -> None:
        overlay = {"always_confirm_tools": ["tracker_close_issue"]}
        cfg = build_effective_config(_Agent(), None, overlay)
        assert "tracker_close_issue" in cfg.runtime_config.always_confirm_tools

    def test_overlay_autonomy_nested(self) -> None:
        overlay = {"autonomy": {"auto_risk": ["low", "medium", "high"]}}
        cfg = build_effective_config(_Agent(), None, overlay)
        assert cfg.runtime_config.auto_risk == ["low", "medium", "high"]

    def test_overlay_prompt_empty_preserves_spec(self) -> None:
        spec = {"prompt": "Spec prompt.", "model": None, "autonomy": {}}
        overlay = {"prompt": ""}  # empty → falsy → not applied
        cfg = build_effective_config(_Agent(), spec, overlay)
        assert cfg.prompt == "Spec prompt."

    def test_partial_overlay_does_not_reset_spec(self) -> None:
        spec = {"prompt": "Spec prompt.", "model": "yandexgpt-lite", "autonomy": {}}
        overlay = {"auto_risk": ["low"]}  # only autonomy in overlay
        cfg = build_effective_config(_Agent(), spec, overlay)
        assert cfg.prompt == "Spec prompt."  # from spec
        assert cfg.llm_configs[0].model == "yandexgpt-lite"  # from spec


# ---------------------------------------------------------------------------
# Per-tool overrides (overlay["tools"])
# ---------------------------------------------------------------------------


class TestToolOverrides:
    def test_defaults_empty(self) -> None:
        cfg = build_effective_config(_Agent(), None, None)
        assert cfg.runtime_config.disabled_tools == []
        assert cfg.runtime_config.tool_confirm == {}

    def test_disabled_tool(self) -> None:
        overlay = {"tools": {"GetIssue": {"enabled": False}}}
        cfg = build_effective_config(_Agent(), None, overlay)
        assert cfg.runtime_config.disabled_tools == ["GetIssue"]

    def test_enabled_tool_not_disabled(self) -> None:
        overlay = {"tools": {"GetIssue": {"enabled": True}}}
        cfg = build_effective_config(_Agent(), None, overlay)
        assert cfg.runtime_config.disabled_tools == []

    def test_confirm_override_true_and_false(self) -> None:
        overlay = {
            "tools": {
                "CloseIssue": {"confirm": True},
                "GetIssue": {"confirm": False},
            }
        }
        cfg = build_effective_config(_Agent(), None, overlay)
        assert cfg.runtime_config.tool_confirm == {"CloseIssue": True, "GetIssue": False}

    def test_confirm_null_ignored(self) -> None:
        overlay = {"tools": {"GetIssue": {"enabled": True, "confirm": None}}}
        cfg = build_effective_config(_Agent(), None, overlay)
        assert cfg.runtime_config.tool_confirm == {}

    def test_overlay_tools_beat_spec_tools(self) -> None:
        spec = {"tools": {"GetIssue": {"enabled": False}}}
        overlay = {"tools": {"GetIssue": {"enabled": True}}}
        cfg = build_effective_config(_Agent(), spec, overlay)
        assert cfg.runtime_config.disabled_tools == []


# ---------------------------------------------------------------------------
# ReActRunner: effective_prompt / effective_runtime_config propagation
# ---------------------------------------------------------------------------


class TestReActRunnerEffectiveConfig:
    """Integration: confirm that _run_loop uses effective_prompt and
    effective_runtime_config when provided."""

    async def test_effective_prompt_sent_to_llm(self) -> None:
        """System message uses effective_prompt, not class prompt."""
        from unittest.mock import MagicMock, patch

        import httpx
        from core.react import ReActRunner
        from core.tools import ToolRegistry

        ToolRegistry().clear()

        class _A(BaseAgent):
            name = "a"
            description = "a"
            prompt = "Original prompt"
            tools: list[str] = []
            llm_configs = [LLMSettings(model="gpt-oss-120b", max_retries=0)]

        runner = ReActRunner(_A(), runtime_config=RuntimeConfig())

        captured: list[dict] = []

        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()

        async def _post(*a, **kw):
            captured.append(kw.get("json", {}))
            resp.json.return_value = {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "done"}],
                    }
                ],
                "output_text": "done",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                "status": "completed",
            }
            return resp

        env = {
            "DATABASE_URL": "postgresql+asyncpg://t:t@localhost/t",
            "YC_API_KEY": "k" * 20,
            "YC_FOLDER_ID": "b1g0" * 4,
            "TRACKER_TOKEN": "t" * 30,
            "TRACKER_ORG_ID": "0" * 12,
        }

        with patch.dict("os.environ", env):
            from core.config import set_config

            set_config(None)
            with patch("httpx.AsyncClient.post", _post):
                await runner.invoke(
                    "Hello",
                    "s1",
                    effective_prompt="Overridden prompt",
                )

        assert captured, "No HTTP call was made"
        instructions = captured[0].get("instructions", "")
        assert "Overridden" in instructions
        assert "Original" not in instructions

    async def test_effective_runtime_config_changes_autonomy(self) -> None:
        """Autonomy gate uses effective_runtime_config when provided."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import httpx
        from core.react import ReActRunner
        from core.tools import ToolRegistry, platform_tool

        ToolRegistry().clear()

        @platform_tool(name="risky_tool", risk="medium")
        async def risky_tool(x: str) -> str:
            return "done"

        class _A(BaseAgent):
            name = "b"
            description = "b"
            prompt = "p"
            tools = ["risky_tool"]
            llm_configs = [LLMSettings(model="gpt-oss-120b", max_retries=0)]

        runner = ReActRunner(_A(), runtime_config=RuntimeConfig())

        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "fc1",
                    "name": "risky_tool",
                    "arguments": '{"x": "value"}',
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "status": "completed",
        }

        env = {
            "DATABASE_URL": "postgresql+asyncpg://t:t@localhost/t",
            "YC_API_KEY": "k" * 20,
            "YC_FOLDER_ID": "b1g0" * 4,
            "TRACKER_TOKEN": "t" * 30,
            "TRACKER_ORG_ID": "0" * 12,
        }

        with patch.dict("os.environ", env):
            from core.config import set_config

            set_config(None)
            with patch("httpx.AsyncClient.post", AsyncMock(return_value=resp)):
                # Effective config: medium is AUTO (not confirm)
                result = await runner.invoke(
                    "Do it",
                    "s2",
                    effective_runtime_config=RuntimeConfig(
                        auto_risk=["low", "medium"],
                        confirm_risk=["high"],
                    ),
                )

        # With auto_risk=["low","medium"], risky_tool should execute without confirm
        assert result.pending_confirm is None

        ToolRegistry().clear()
