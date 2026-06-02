"""
PM Agent Platform - Core Package

Provides foundational components for multi-agent platform:
- Configuration management (Pydantic Settings)
- Database layer (SQLAlchemy 2.0 async)
- LLM integration (YandexGPT)
- Tool system (@platform_tool decorator)
- Custom exceptions
- Database models
"""

__version__ = "0.1.0"

from core import config
from core import db
from core import exceptions
from core import llm
from core import logging
from core import models
from core import prompts
from core import tools

from core.config import (
    Config,
    DatabaseConfig,
    YandexConfig,
    TrackerConfig,
    LLMConfig,
    AppConfig,
    RuntimeConfig,
    get_config,
    reload_config,
    set_config,
)

from core.db import (
    create_db_engine,
    get_engine,
    get_session,
    get_session_factory,
    health_check,
    close_engine,
    reset_engine,
    Checkpointer,
)

from core.llm import (
    Message,
    ToolCall,
    TokenUsage,
    LLMResponse,
    LLMClient,
    complete,
)

from core.tools import (
    Tool,
    ToolParameter,
    ToolRegistry,
    platform_tool,
    get_registry,
)

from core.exceptions import (
    CoreError,
    ConfigError,
    DBError,
    LLMError,
    ToolError,
    ToolNotFoundError,
    ToolValidationError,
    ToolExecutionError,
    ConfirmError,
    ConfirmTimeoutError,
    ConfirmRejectedError,
    AutonomyError,
    AgentError,
    A2AError,
    RegistryError,
)

from core.logging import (
    JSONFormatter,
    get_logger,
    set_trace_id,
    get_trace_id,
    configure_logging,
    timed,
)

from core.prompts import (
    PM_AGENT_SYSTEM_PROMPT,
    format_tool_descriptions,
    format_confirm_prompt,
    format_error_message,
    ROLE_SYSTEM,
    ROLE_USER,
    ROLE_ASSISTANT,
)

from core.models import (
    Base,
    Organization,
    Team,
    AgentSpec,
    AgentInstance,
    Action,
    Trace,
    Confirm,
    RuntimeConfigModel,
    ScheduledJob,
    ActionFeedback,
)

__all__ = [
    "__version__",
    "config",
    "db",
    "exceptions",
    "llm",
    "logging",
    "models",
    "prompts",
    "tools",
    "Config",
    "DatabaseConfig",
    "YandexConfig",
    "TrackerConfig",
    "LLMConfig",
    "AppConfig",
    "RuntimeConfig",
    "get_config",
    "reload_config",
    "set_config",
    "create_db_engine",
    "get_engine",
    "get_session",
    "get_session_factory",
    "health_check",
    "close_engine",
    "reset_engine",
    "Checkpointer",
    "Message",
    "ToolCall",
    "TokenUsage",
    "LLMResponse",
    "LLMClient",
    "complete",
    "Tool",
    "ToolParameter",
    "ToolRegistry",
    "platform_tool",
    "get_registry",
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
    "JSONFormatter",
    "get_logger",
    "set_trace_id",
    "get_trace_id",
    "configure_logging",
    "timed",
    "PM_AGENT_SYSTEM_PROMPT",
    "format_tool_descriptions",
    "format_confirm_prompt",
    "format_error_message",
    "ROLE_SYSTEM",
    "ROLE_USER",
    "ROLE_ASSISTANT",
    "Base",
    "Organization",
    "Team",
    "AgentSpec",
    "AgentInstance",
    "Action",
    "Trace",
    "Confirm",
    "RuntimeConfigModel",
    "ScheduledJob",
    "ActionFeedback",
]
