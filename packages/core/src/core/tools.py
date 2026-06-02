"""
Tool system with decorator and registry.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import typing
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from core.exceptions import ToolError, ToolNotFoundError, ToolValidationError


class ToolParameter(BaseModel):
    """Tool parameter definition."""
    name: str
    type: str
    description: str | None = None
    required: bool = True
    default: Any = None


class Tool(BaseModel):
    """Tool definition with metadata."""
    name: str
    description: str
    func: Callable[..., Any]
    risk: Literal["low", "medium", "high"] = "medium"
    scopes: list[str] = Field(default_factory=list)
    parameters: list[ToolParameter] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    def execute(self, **kwargs: Any) -> Any:
        """Execute tool with provided arguments."""
        sig = inspect.signature(self.func)
        bound = sig.bind(**kwargs)
        bound.apply_defaults()

        if asyncio.iscoroutinefunction(self.func):
            return self.func(**bound.arguments)

        return self.func(**bound.arguments)

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate tool arguments against parameter schema."""
        validated = {}
        for param in self.parameters:
            if param.name in arguments:
                value = arguments[param.name]
                try:
                    validated[param.name] = self._coerce_type(value, param.type)
                except (ValueError, TypeError) as e:
                    raise ToolValidationError(
                        f"Invalid value for {param.name}: {e}"
                    ) from e
            elif param.required and param.default is None:
                raise ToolValidationError(f"Missing required argument: {param.name}")
            elif param.default is not None:
                validated[param.name] = param.default

        return validated

    @staticmethod
    def _coerce_type(value: Any, target_type: str) -> Any:
        """Coerce value to target type."""
        if target_type == "string":
            return str(value)
        if target_type == "integer":
            return int(value)
        if target_type == "number":
            return float(value)
        if target_type == "boolean":
            if isinstance(value, str):
                return value.lower() in ("true", "1", "yes")
            return bool(value)
        if target_type == "array":
            if isinstance(value, str):
                return [v.strip() for v in value.split(",")]
            return list(value)
        return value

    def get_schema(self) -> dict[str, Any]:
        """Generate OpenAPI-style schema for this tool."""
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param in self.parameters:
            prop: dict[str, Any] = {"type": param.type}
            if param.description:
                prop["description"] = param.description
            properties[param.name] = prop
            if param.required and param.default is None:
                required.append(param.name)

        parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            parameters["required"] = required

        return {
            "name": self.name,
            "description": self.description,
            "parameters": parameters,
        }


class ToolRegistry:
    """
    Singleton registry for platform tools.

    Provides registration, discovery, and execution of tools.
    """

    _instance: ToolRegistry | None = None
    _tools: dict[str, Tool] = {}

    def __new__(cls) -> ToolRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools = {}
        return cls._instance

    def register(self, tool: Tool) -> None:
        """Register a tool in the registry."""
        if tool.name in self._tools:
            raise ToolError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """Get tool by name."""
        if name not in self._tools:
            raise ToolNotFoundError(f"Tool not found: {name}")
        return self._tools[name]

    def list(self, scopes: list[str] | None = None) -> list[Tool]:
        """List all registered tools, optionally filtered by scopes."""
        if scopes is None:
            return list(self._tools.values())

        return [
            tool for tool in self._tools.values()
            if any(scope in tool.scopes for scope in scopes)
        ]

    def get_schemas(self, scopes: list[str] | None = None) -> list[dict[str, Any]]:
        """Get OpenAPI schemas for tools."""
        return [tool.get_schema() for tool in self.list(scopes)]

    def exists(self, name: str) -> bool:
        """Check if tool exists."""
        return name in self._tools

    def unregister(self, name: str) -> None:
        """Unregister a tool."""
        if name in self._tools:
            del self._tools[name]

    def clear(self) -> None:
        """Clear all registered tools."""
        self._tools.clear()


def _bind_positional_args(
    sig: inspect.Signature,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Merge positional args into kwargs using the function signature."""
    if not args:
        return kwargs
    merged = dict(kwargs)
    param_names = list(sig.parameters.keys())
    for i, value in enumerate(args):
        if i < len(param_names):
            merged[param_names[i]] = value
    return merged


def platform_tool(
    name: str,
    risk: Literal["low", "medium", "high"] = "medium",
    scopes: list[str] | None = None,
    description: str | None = None,
) -> Callable[[Callable[..., Any]], Tool]:
    """
    Decorator to register a function as a platform tool.

    Args:
        name: Tool name (must be unique)
        risk: Risk level (low/medium/high)
        scopes: Required scopes for tool access
        description: Tool description (from docstring if not provided)

    Returns:
        Decorated function as Tool
    """
    def decorator(func: Callable[..., Any]) -> Tool:
        sig = inspect.signature(func)
        # Evaluate string annotations (from __future__ import annotations) to real types
        try:
            resolved_hints: dict[str, Any] = typing.get_type_hints(func)
        except Exception:
            resolved_hints = {}

        parameters = []

        for param_name, param in sig.parameters.items():
            param_type = "string"
            ann = resolved_hints.get(param_name, param.annotation)
            if ann is not inspect.Parameter.empty:
                origin = getattr(ann, "__origin__", None)
                if ann is int:
                    param_type = "integer"
                elif ann is float:
                    param_type = "number"
                elif ann is bool:
                    param_type = "boolean"
                elif ann is list or origin is list:
                    param_type = "array"
                elif ann is dict or origin is dict:
                    param_type = "object"
                elif hasattr(ann, "__name__"):
                    ann_name = ann.__name__.lower()
                    if ann_name in ("int", "integer"):
                        param_type = "integer"
                    elif ann_name in ("float", "number"):
                        param_type = "number"
                    elif ann_name in ("bool", "boolean"):
                        param_type = "boolean"
                    elif ann_name in ("list", "array"):
                        param_type = "array"
                    elif ann_name == "dict":
                        param_type = "object"

            has_default = param.default is not inspect.Parameter.empty
            parameters.append(ToolParameter(
                name=param_name,
                type=param_type,
                description=description,
                required=not has_default,
                default=param.default if has_default else None,
            ))

        tool_description = description or func.__doc__ or ""
        if tool_description:
            tool_description = tool_description.strip().split("\n")[0]

        tool = Tool(
            name=name,
            description=tool_description,
            func=func,
            risk=risk,
            scopes=scopes or [],
            parameters=parameters,
        )

        registry = ToolRegistry()
        registry.register(tool)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            merged = _bind_positional_args(sig, args, kwargs)
            validated = tool.validate_arguments(merged)
            return tool.execute(**validated)

        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                merged = _bind_positional_args(sig, args, kwargs)
                validated = tool.validate_arguments(merged)
                return await tool.execute(**validated)
            return async_wrapper  # type: ignore

        return wrapper  # type: ignore

    return decorator


def get_registry() -> ToolRegistry:
    """Get the global tool registry."""
    return ToolRegistry()


__all__ = [
    "Tool",
    "ToolParameter",
    "ToolRegistry",
    "platform_tool",
    "get_registry",
]
