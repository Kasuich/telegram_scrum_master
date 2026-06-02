"""
Tests for tool system.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from core.exceptions import ToolNotFoundError, ToolValidationError
from core.tools import Tool, ToolParameter, ToolRegistry, platform_tool, get_registry


class TestToolParameter:
    """Tests for ToolParameter model."""

    def test_basic_parameter(self) -> None:
        """Basic parameter creation."""
        param = ToolParameter(name="arg1", type="string")
        assert param.name == "arg1"
        assert param.type == "string"
        assert param.required is True

    def test_optional_parameter(self) -> None:
        """Optional parameter with default."""
        param = ToolParameter(
            name="arg2",
            type="integer",
            required=False,
            default=10,
        )
        assert param.required is False
        assert param.default == 10

    def test_parameter_with_description(self) -> None:
        """Parameter with description."""
        param = ToolParameter(
            name="arg3",
            type="boolean",
            description="A boolean flag",
        )
        assert param.description == "A boolean flag"


class TestTool:
    """Tests for Tool model."""

    def test_tool_creation(self) -> None:
        """Tool creation with function."""
        def sample_func(a: str, b: int) -> str:
            return f"{a}: {b}"

        tool = Tool(
            name="sample",
            description="A sample tool",
            func=sample_func,
            risk="low",
            scopes=["test:read"],
        )
        assert tool.name == "sample"
        assert tool.risk == "low"

    def test_execute_sync(self) -> None:
        """Synchronous tool execution."""
        def add(a: int, b: int) -> int:
            return a + b

        tool = Tool(
            name="add",
            description="Add two numbers",
            func=add,
        )
        result = tool.execute(a=5, b=3)
        assert result == 8

    @pytest.mark.asyncio
    async def test_execute_async(self) -> None:
        """Async tool execution."""
        async def async_add(a: int, b: int) -> int:
            return a + b

        tool = Tool(
            name="async_add",
            description="Async add",
            func=async_add,
        )
        result = await tool.execute(a=5, b=3)
        assert result == 8

    def test_validate_arguments_valid(self) -> None:
        """Valid argument validation."""
        def sample(a: str, b: int) -> str:
            return a

        tool = Tool(
            name="sample",
            description="Sample",
            func=sample,
            parameters=[
                ToolParameter(name="a", type="string", required=True),
                ToolParameter(name="b", type="integer", required=True),
            ],
        )
        validated = tool.validate_arguments({"a": "test", "b": 5})
        assert validated == {"a": "test", "b": 5}

    def test_validate_arguments_missing_required(self) -> None:
        """Missing required argument raises error."""
        def sample(a: str, b: int) -> str:
            return a

        tool = Tool(
            name="sample",
            description="Sample",
            func=sample,
            parameters=[
                ToolParameter(name="a", type="string", required=True),
                ToolParameter(name="b", type="integer", required=True),
            ],
        )
        with pytest.raises(ToolValidationError) as exc:
            tool.validate_arguments({"a": "test"})
        assert "b" in str(exc.value)

    def test_validate_arguments_with_defaults(self) -> None:
        """Optional args use defaults."""
        def sample(a: str, b: int = 10) -> int:
            return b

        tool = Tool(
            name="sample",
            description="Sample",
            func=sample,
            parameters=[
                ToolParameter(name="a", type="string", required=True),
                ToolParameter(name="b", type="integer", required=False, default=10),
            ],
        )
        validated = tool.validate_arguments({"a": "test"})
        assert validated == {"a": "test", "b": 10}

    def test_type_coercion(self) -> None:
        """Type coercion works."""
        def sample(a: int, b: bool, c: list) -> tuple:
            return (a, b, c)

        tool = Tool(
            name="sample",
            description="Sample",
            func=sample,
            parameters=[
                ToolParameter(name="a", type="integer"),
                ToolParameter(name="b", type="boolean"),
                ToolParameter(name="c", type="array"),
            ],
        )
        validated = tool.validate_arguments({
            "a": "42",
            "b": "true",
            "c": "x,y,z",
        })
        assert validated["a"] == 42
        assert validated["b"] is True
        assert validated["c"] == ["x", "y", "z"]

    def test_get_schema(self) -> None:
        """Schema generation."""
        def sample(a: str, b: int) -> str:
            return a

        tool = Tool(
            name="sample",
            description="A sample tool",
            func=sample,
            parameters=[
                ToolParameter(name="a", type="string", required=True),
                ToolParameter(name="b", type="integer", required=False),
            ],
        )
        schema = tool.get_schema()
        assert schema["name"] == "sample"
        assert "a" in schema["parameters"]["properties"]
        assert "required" in schema["parameters"]


class TestToolRegistry:
    """Tests for ToolRegistry singleton."""

    def setup_method(self) -> None:
        """Reset registry before each test."""
        registry = get_registry()
        registry.clear()

    def test_singleton(self) -> None:
        """Registry is singleton."""
        registry1 = get_registry()
        registry2 = get_registry()
        assert registry1 is registry2

    def test_register_and_get(self) -> None:
        """Register and retrieve tool."""
        def sample() -> str:
            return "sample"

        tool = Tool(name="test_tool", description="Test", func=sample)
        registry = get_registry()
        registry.register(tool)

        retrieved = registry.get("test_tool")
        assert retrieved.name == "test_tool"

    def test_get_not_found(self) -> None:
        """Get non-existent tool raises error."""
        registry = get_registry()
        with pytest.raises(ToolNotFoundError):
            registry.get("nonexistent")

    def test_list_all(self) -> None:
        """List all registered tools."""
        def sample1() -> None: pass
        def sample2() -> None: pass

        registry = get_registry()
        registry.register(Tool(name="tool1", description="Tool 1", func=sample1))
        registry.register(Tool(name="tool2", description="Tool 2", func=sample2))

        tools = registry.list()
        assert len(tools) == 2

    def test_list_filtered_by_scope(self) -> None:
        """List filtered by scope."""
        def sample1() -> None: pass
        def sample2() -> None: pass

        registry = get_registry()
        registry.register(Tool(name="tool1", description="Tool 1", func=sample1, scopes=["read"]))
        registry.register(Tool(name="tool2", description="Tool 2", func=sample2, scopes=["write"]))

        tools = registry.list(scopes=["read"])
        assert len(tools) == 1
        assert tools[0].name == "tool1"

    def test_get_schemas(self) -> None:
        """Get schemas for all tools."""
        def sample() -> None: pass

        registry = get_registry()
        registry.register(Tool(
            name="test",
            description="Test",
            func=sample,
            parameters=[ToolParameter(name="arg", type="string")],
        ))

        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "test"

    def test_exists(self) -> None:
        """Check tool existence."""
        def sample() -> None: pass

        registry = get_registry()
        registry.register(Tool(name="test", description="Test", func=sample))

        assert registry.exists("test") is True
        assert registry.exists("nonexistent") is False

    def test_unregister(self) -> None:
        """Unregister tool."""
        def sample() -> None: pass

        registry = get_registry()
        registry.register(Tool(name="test", description="Test", func=sample))
        registry.unregister("test")

        assert registry.exists("test") is False

    def test_clear(self) -> None:
        """Clear all tools."""
        def sample1() -> None: pass
        def sample2() -> None: pass

        registry = get_registry()
        registry.register(Tool(name="tool1", description="Tool 1", func=sample1))
        registry.register(Tool(name="tool2", description="Tool 2", func=sample2))
        registry.clear()

        assert len(registry.list()) == 0

    def test_duplicate_registration(self) -> None:
        """Duplicate registration raises error."""
        from core.exceptions import ToolError

        def sample() -> None: pass

        registry = get_registry()
        registry.register(Tool(name="test", description="Test", func=sample))

        with pytest.raises(ToolError) as exc:
            registry.register(Tool(name="test", description="Test", func=sample))
        assert "already registered" in str(exc.value)


class TestPlatformToolDecorator:
    """Tests for @platform_tool decorator."""

    def setup_method(self) -> None:
        """Reset registry before each test."""
        registry = get_registry()
        registry.clear()

    def test_basic_decorator(self) -> None:
        """Basic decorator usage."""
        @platform_tool(name="hello", risk="low", scopes=["test:read"])
        def hello(name: str) -> str:
            return f"Hello, {name}!"

        result = hello(name="World")
        assert result == "Hello, World!"

        registry = get_registry()
        tool = registry.get("hello")
        assert tool.risk == "low"
        assert "test:read" in tool.scopes

    def test_decorator_with_params(self) -> None:
        """Decorator with multiple parameters."""
        @platform_tool(name="add", risk="medium")
        def add(a: int, b: int, desc: str = "addition") -> int:
            return a + b

        result = add(a=5, b=3)
        assert result == 8

        registry = get_registry()
        tool = registry.get("add")
        assert len(tool.parameters) == 3

    @pytest.mark.asyncio
    async def test_decorator_async(self) -> None:
        """Async function decorator."""
        @platform_tool(name="async_hello")
        async def async_hello(name: str) -> str:
            return f"Async Hello, {name}!"

        result = await async_hello(name="World")
        assert "Hello" in result

    def test_decorator_type_inference(self) -> None:
        """Parameter types inferred from annotations."""
        @platform_tool(name="types")
        def typed_func(
            s: str,
            i: int,
            f: float,
            b: bool,
            l: list,
        ) -> str:
            return s

        registry = get_registry()
        tool = registry.get("types")

        types = {p.name: p.type for p in tool.parameters}
        assert types["s"] == "string"
        assert types["i"] == "integer"
        assert types["f"] == "number"
        assert types["b"] == "boolean"
        assert types["l"] == "array"

    def test_decorator_validation(self) -> None:
        """Decorated function validates arguments."""
        @platform_tool(name="validated")
        def validated(a: int, b: str) -> str:
            return f"{b}: {a}"

        with pytest.raises(ToolValidationError):
            validated(a="not-int", b="test")
