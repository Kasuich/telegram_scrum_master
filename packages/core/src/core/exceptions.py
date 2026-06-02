"""
Custom exceptions for PM Agent Platform.
"""


class CoreError(Exception):
    """Base exception for all core errors."""

    def __init__(self, message: str, cause: Exception | None = None):
        super().__init__(message)
        self.message = message
        self.cause = cause
        if cause is not None:
            self.__cause__ = cause


class ConfigError(CoreError):
    """Configuration-related errors."""
    pass


class DBError(CoreError):
    """Database-related errors."""
    pass


class LLMError(CoreError):
    """LLM-related errors."""
    pass


class ToolError(CoreError):
    """Tool-related errors."""
    pass


class ToolNotFoundError(ToolError):
    """Tool not found in registry."""
    pass


class ToolValidationError(ToolError):
    """Tool argument validation failed."""
    pass


class ToolExecutionError(ToolError):
    """Tool execution failed."""
    pass


class ConfirmError(CoreError):
    """Confirmation request errors."""
    pass


class ConfirmTimeoutError(ConfirmError):
    """Confirmation request timed out."""
    pass


class ConfirmRejectedError(ConfirmError):
    """Confirmation was rejected."""
    pass


class AutonomyError(CoreError):
    """Autonomy gate errors."""
    pass


class AgentError(CoreError):
    """Agent-related errors."""
    pass


class A2AError(CoreError):
    """Agent-to-agent communication errors."""
    pass


class RegistryError(CoreError):
    """Agent registry errors."""
    pass


__all__ = [
    "CoreError",
    "ConfigError",
    "DBError",
    "LLMError",
    "ToolError",
    "ToolNotFoundError",
    "ToolValidationError",
    "ToolExecutionError",
    "ConfirmError",
    "ConfirmTimeoutError",
    "ConfirmRejectedError",
    "AutonomyError",
    "AgentError",
    "A2AError",
    "RegistryError",
]
