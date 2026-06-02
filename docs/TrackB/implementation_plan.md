# Track B — Core Platform Implementation Plan

> **Миссия:** Построить фундамент платформы PM-агента. Все остальные треки зависят от твоих интерфейсов.
> **Критический путь:** Интерфейсы в день 1, чтобы разблокировать A/C/D.

---

## 🎯 Обзор задач

| Фаза | Что делаем | День | Критерий готовности |
|------|-----------|------|---------------------|
| 1 | Конфигурация (config.py) | 1 | Другие треки могут импортировать |
| 2 | База данных (db.py + модели) | 1-2 | Миграции работают, сессии создаются |
| 3 | LLM integration (llm.py) | 2-3 | YandexGPT вызывается, tool_calls парсятся |
| 4 | Tool System (tools.py) | 2-3 | @platform_tool работает, Registry функционирует |
| 5 | Интеграция и тесты | 3-5 | Полный срез end-to-end |

---

## 📦 Фаза 1: Система конфигурации (День 1)

### 1.1 Конфиг модуль
**Файл:** `packages/core/src/core/config.py`

```python
# Структура:
# - Pydantic Settings v2
# - Загрузка из .env
# - Поддержка runtime_configs (team overlay)
# - Валидация типов
```

#### Задачи:
- [ ] Базовый класс `Config` с полями:
  - `DATABASE_URL: str`
  - `YC_API_KEY: str`
  - `YC_FOLDER_ID: str`
  - `TRACKER_TOKEN: str`
  - `TRACKER_ORG_ID: str`
  - `TRACKER_QUEUE: str`
  - `YANDEXGPT_MODEL: str = "yandexgpt-pro"`
  - `YANDEXGPT_TEMPERATURE: float = 0.7`
  - `YANDEXGPT_MAX_TOKENS: int = 4000`
- [ ] Поддержка `.env` файлов
- [ ] Валидация URL и required полей
- [ ] Runtime config: загрузка из YAML/БД
- [ ] Team-based override (base → team overlay → env)

#### Тесты (`packages/core/tests/unit/test_config.py`):
```python
# 1. test_config_from_env()
#    - Загрузить из тестового .env
#    - Проверить все поля
#    - Assert типы корректны

# 2. test_config_validation_missing_required()
#    - Отсутствует DATABASE_URL → ValidationError

# 3. test_config_validation_invalid_url()
#    - DATABASE_URL = "not-a-url" → ValidationError

# 4. test_team_overlay()
#    - Base config + team override
#    - Override перекрывает base значения

# 5. test_env_vars_take_precedence()
#    - Env vars важнее .env файла

# 6. test_multiple_teams()
#    - Разные team_id → разные конфиги
#    - Изоляция между командами
```

---

### 1.2 .env.example
**Файл:** `packages/core/.env.example`

#### Задачи:
- [ ] Все переменные с комментариями
- [ ] Примеры значений
- [ ] Документация по получению токенов

---

## 📦 Фаза 2: База данных (Дни 1-2)

### 2.1 DB Engine
**Файл:** `packages/core/src/core/db.py`

```python
# Структура:
# - SQLAlchemy 2.0 async + asyncpg
# - Connection pooling
# - Async session context manager
# - Checkpointer для LangGraph
```

#### Задачи:
- [ ] `create_engine()` с пулом (pool_size=20, max_overflow=10)
- [ ] `get_session()` — async context manager
- [ ] `health_check()` — проверка connectivity
- [ ] Checkpointer integration для LangGraph
- [ ] Transaction management utilities

#### Тесты (`packages/core/tests/unit/test_db.py`):
```python
# 1. test_engine_creation()
#    - Engine создаётся
#    - Пул инициализирован

# 2. test_session_context_manager()
#    - Сессия создаётся в context
#    - Закрывается корректно после выхода
#    - Transaction rollback при exception

# 3. test_session_execute_query()
#    - SELECT работает
#    - Результат парсится

# 4. test_concurrent_connections()
#    - 10 concurrent sessions
#    - Пул справляется
#    - Connections возвращаются

# 5. test_checkpointer_save_load()
#    - Сохранить checkpoint
#    - Загрузить checkpoint
#    - Данные совпадают

# 6. test_transaction_rollback()
#    - Exception в транзакции → rollback
#    - Данные не сохраняются
```

---

### 2.2 Модели
**Файл:** `packages/core/src/core/models/` + `packages/core/src/core/models.py`

#### Схема данных:

```python
# organizations
# - id: UUID (PK)
# - name: str

# teams
# - id: UUID (PK)
# - organization_id: UUID (FK)
# - name: str
# - tracker_queue: str

# agent_specs
# - id: UUID (PK)
# - name: str
# - model: str
# - prompt: text
# - tools: jsonb
# - autonomy: jsonb

# agent_instances
# - id: UUID (PK)
# - team_id: UUID (FK)
# - spec_id: UUID (FK)
# - overlay: jsonb
# - enabled: bool

# actions
# - id: UUID (PK)
# - team_id: UUID (FK)
# - agent_instance_id: UUID (FK)
# - tool_name: str
# - input: jsonb
# - output: jsonb
# - risk_level: str (low/medium/high)
# - status: str (pending/completed/failed)
# - trace_id: UUID (FK)
# - created_at: timestamptz

# traces
# - id: UUID (PK)
# - session_id: UUID
# - steps: jsonb
# - metadata: jsonb
# - created_at: timestamptz

# confirms
# - id: UUID (PK)
# - action_id: UUID (FK)
# - prompt: text
# - status: str (pending/approved/rejected)
# - answer: str
# - created_at: timestamptz
# - responded_at: timestamptz

# runtime_configs
# - id: UUID (PK)
# - team_id: UUID (FK)
# - key: str
# - value: jsonb

# scheduled_jobs
# - id: UUID (PK)
# - agent_instance_id: UUID (FK)
# - cron_expr: str
# - payload: jsonb
# - max_runs: int
# - run_count: int
# - next_run: timestamptz
# - enabled: bool

# action_feedback
# - id: UUID (PK)
# - action_id: UUID (FK)
# - user_id: UUID (FK)
# - rating: int (1-5)
# - comment: text
# - created_at: timestamptz
```

#### Задачи:
- [ ] Base model с UUID PK, created_at
- [ ] Organization, Team models
- [ ] AgentSpec, AgentInstance models
- [ ] Action, Trace, Confirm models
- [ ] RuntimeConfig model
- [ ] ScheduledJob model
- [ ] ActionFeedback model
- [ ] Relationships и foreign keys

#### Тесты (`packages/core/tests/unit/test_models.py`):
```python
# 1. test_base_model_uuid_generation()
#    - Новый объект получает UUID
#    - UUID валидный

# 2. test_base_model_timestamps()
#    - created_at автоматически
#    - updated_at меняется при save

# 3. test_organization_team_relationship()
#    - Team привязан к Organization
#    - Cascade delete работает

# 4. test_action_trace_relationship()
#    - Action связан с Trace
#    - trace_id foreign key

# 5. test_confirm_requires_action()
#    - Confirm нельзя создать без Action
#    - CASCADE работает

# 6. test_runtime_config_team_isolation()
#    - Конфиги разных команд изолированы
#    - team_id foreign key

# 7. test_scheduled_job_cron_validation()
#    - Валидный cron_expr сохраняется
#    - Невалидный → ValidationError

# 8. test_action_feedback_rating_bounds()
#    - rating < 1 → ValidationError
#    - rating > 5 → ValidationError

# 9. test_bulk_insert_performance()
#    - 1000 actions в bulk
#    - Время < 1 секунды
```

---

### 2.3 Миграции (Alembic)
**Папка:** `packages/core/migrations/`

#### Задачи:
- [ ] Инициализация Alembic
- [ ] Initial migration со всеми таблицами
- [ ] Индексы:
  - `idx_actions_team_id`
  - `idx_actions_trace_id`
  - `idx_traces_session_id`
  - `idx_confirms_action_id`
  - `idx_runtime_configs_team_id`
- [ ] Foreign key constraints
- [ ] Migration down (drop tables)

#### Тесты (`packages/core/tests/migrations/`):
```python
# 1. test_migration_up()
#    - Запустить миграцию
#    - Все таблицы созданы
#    - Constraints существуют

# 2. test_migration_down()
#    - Rollback миграции
#    - Таблицы удалены
#    - Нет orphaned data

# 3. test_fresh_database()
#    - Пустая БД → миграция → работает

# 4. test_migration_preserves_data()
#    - Данные → миграция → данные целы

# 5. test_indexes_exist()
#    - EXPLAIN ANALYZE использует индексы
```

---

## 📦 Фаза 3: LLM Integration (Дни 2-3)

### 3.1 LiteLLM Wrapper
**Файл:** `packages/core/src/core/llm.py`

```python
# Структура:
# - LLMClient wrapper для YandexGPT
# - complete() функция (messages, tools) → LLMResponse
# - Tool calling support
# - Streaming
# - Fallback chain
# - Retry с exponential backoff
# - Token usage tracking
```

#### Модели:

```python
class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str

class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any]

class TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class LLMResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall] | None
    usage: TokenUsage | None
    model: str
    latency_ms: int
    finish_reason: str | None
```

#### Задачи:
- [ ] `LLMClient` class
- [ ] `complete(messages, tools=None, **kwargs)` → LLMResponse
- [ ] Tool calling parsing (function_call)
- [ ] Streaming support (async generator)
- [ ] Fallback chain (primary → backup)
- [ ] Retry с exponential backoff (max 3 attempts)
- [ ] Token usage tracking
- [ ] Request timeout (60s default)
- [ ] Rate limiting

#### Тесты (`packages/core/tests/unit/test_llm.py`):
```python
# 1. test_basic_completion()
#    - Send message
#    - Receive response
#    - Latency measured

# 2. test_completion_with_system_prompt()
#    - System + user message
#    - System applied

# 3. test_tool_calling_extraction()
#    - Response с tool_calls
#    - Tool name + arguments parsed
#    - No tool_calls → content only

# 4. test_streaming_response()
#    - Stream chunks
#    - All chunks received
#    - Final assembled response

# 5. test_fallback_on_error()
#    - Primary fails → backup used
#    - All fail → LLMError

# 6. test_retry_on_transient_error()
#    - 2 failures → retry
#    - 3 failures → raise
#    - Backoff increases

# 7. test_token_usage_recorded()
#    - usage.prompt_tokens > 0
#    - usage.completion_tokens > 0
#    - usage.total_tokens = sum

# 8. test_timeout_handling()
#    - Slow response → TimeoutError
#    - Configurable timeout

# 9. test_rate_limit_wait()
#    - Respect rate limits
#    - Queue requests

# 10. test_invalid_json_tool_call()
#     - Malformed tool call → graceful handling
#     - Partial parsing

# Mock tests (не стучимся в реальный API):
# 11. test_complete_with_mock()
#     - Mock response
#     - Verify parsing logic

# 12. test_tool_call_format()
#     - Various formats парсятся
```

---

### 3.2 Prompt Templates
**Файл:** `packages/core/src/core/prompts.py`

#### Задачи:
- [ ] System prompts для агентов
- [ ] Tool descriptions formatting
- [ ] Confirm prompt templates
- [ ] Error message templates

---

## 📦 Фаза 4: Tool System (Дни 2-3)

### 4.1 Tool Decorator & Registry
**Файл:** `packages/core/src/core/tools.py`

```python
# Структура:
# - @platform_tool decorator
# - Tool metadata (name, description, risk, scopes)
# - Function signature inspection
# - ToolRegistry singleton
# - Async function support
# - Input validation
```

#### Модели:

```python
class ToolParameter(BaseModel):
    name: str
    type: str
    description: str | None
    required: bool
    default: Any | None

class Tool(BaseModel):
    name: str
    description: str
    func: Callable  # Not serialized
    risk: Literal["low", "medium", "high"]
    scopes: list[str]
    parameters: list[ToolParameter]
    model_config = {"arbitrary_types_allowed": True}

class ToolRegistry:
    _tools: dict[str, Tool]
    
    def register(self, tool: Tool) -> None
    def get(self, name: str) -> Tool
    def list(self, scopes: list[str] | None = None) -> list[Tool]
    def get_schemas(self) -> list[dict]
```

#### Задачи:
- [ ] `@platform_tool(name, risk, scopes)` decorator
- [ ] Signature inspection (extract params, types, defaults)
- [ ] `ToolRegistry` singleton
- [ ] Tool registration on import
- [ ] `get(name)`, `list()`, `get_schemas()`
- [ ] OpenAPI schema generation
- [ ] Sync + async function support

#### Тесты (`packages/core/tests/unit/test_tools.py`):
```python
# 1. test_decorator_basic()
#    - @platform_tool создаёт Tool
#    - Регистрируется в Registry

# 2. test_decorator_with_params()
#    - Tool с параметрами
#    - Типы из hints
#    - Defaults preserved

# 3. test_decorator_risk_levels()
#    - low/medium/high risk
#    - Default = medium

# 4. test_decorator_scopes()
#    - scopes parameter
#    - Multiple scopes

# 5. test_registry_register()
#    - Register tool
#    - Get back same tool

# 6. test_registry_get_not_found()
#    - Unknown tool → KeyError

# 7. test_registry_list()
#    - Multiple tools registered
#    - list() returns all

# 8. test_registry_filter_by_scope()
#    - scope="tracker:write"
#    - Only matching tools

# 9. test_signature_inspection_basic()
#    - def foo(a: int, b: str) → params extracted
#    - Types correct

# 10. test_signature_inspection_with_defaults()
#     - def foo(a, b="default") → default extracted
#     - Required vs optional

# 11. test_signature_inspection_async()
#     - async def → works
#     - Await called correctly

# 12. test_openapi_schema_generation()
#     - Generate JSON schema
#     - Required fields marked
#     - Types correct

# 13. test_tool_execution_valid_args()
#     - Execute with valid args
#     - Return value correct

# 14. test_tool_execution_invalid_args()
#     - Invalid types → ValidationError
#     - Missing required → ValidationError

# 15. test_tool_execution_error_propagation()
#     - Tool raises → error bubbles
#     - Error message preserved

# 16. test_duplicate_tool_registration()
#     - Same name twice → raises

# 17. test_docstring_extraction()
#     - docstring → description
#     - Google style parsed
```

---

### 4.2 Tool Validation
**Файл:** `packages/core/src/core/tools/validation.py`

#### Задачи:
- [ ] Pydantic model generation from signature
- [ ] Runtime validation of arguments
- [ ] Error messages for validation failures
- [ ] Type coercion where safe

#### Тесты:
```python
# 1. test_validation_int_type()
#    - "5" → int(5)
#    - "not-int" → ValidationError

# 2. test_validation_optional()
#    - Missing optional → None
#    - Provided → used

# 3. test_validation_complex_types()
#    - list[str], dict, etc.
#    - Nested validation

# 4. test_validation_error_messages()
#    - Clear error message
#    - Field name included
```

---

### 4.3 BaseTool Examples
**Файл:** `packages/core/src/core/tools/examples.py`

#### Примеры тулзов для документации:

```python
@platform_tool(name="example_hello", risk="low", scopes=["example:read"])
async def hello(name: str) -> str:
    """Greet a user by name."""
    return f"Hello, {name}!"

@platform_tool(name="example_create", risk="medium", scopes=["example:write"])
async def create_item(name: str, description: str = "") -> dict:
    """Create an item."""
    return {"id": "123", "name": name, "description": description}
```

#### Тесты:
```python
# 1. test_example_tools_execute()
#    - hello("World") → "Hello, World!"
#    - create_item("Test") → dict with id

# 2. test_example_risk_levels()
#    - hello is low
#    - create_item is medium
```

---

## 📦 Фаза 5: Инфраструктура (Дни 3-4)

### 5.1 Logging & Observability
**Файл:** `packages/core/src/core/logging.py`

#### Задачи:
- [ ] Structured JSON logging
- [ ] Trace ID injection
- [ ] Correlation across modules
- [ ] Timing decorators
- [ ] Log levels configuration

#### Тесты:
```python
# 1. test_trace_id_in_log()
#    - Trace ID in log line
#    - Format: {"trace_id": "..."}

# 2. test_log_levels()
#    - DEBUG/INFO/WARNING/ERROR
#    - Respects config

# 3. test_timing_decorator()
#    - @timed decorator works
#    - Duration logged
```

---

### 5.2 Error Handling
**Файл:** `packages/core/src/core/exceptions.py`

```python
class CoreError(Exception): ...
class ConfigError(CoreError): ...
class DBError(CoreError): ...
class LLMError(CoreError): ...
class ToolError(CoreError): ...
class ToolNotFoundError(ToolError): ...
class ToolValidationError(ToolError): ...
```

#### Тесты:
```python
# 1. test_error_chain()
#    - Original exception preserved
#    - Chain of causes

# 2. test_error_message_format()
#    - Clear, actionable message
#    - No leaking internals
```

---

### 5.3 Package Exports
**Файл:** `packages/core/src/core/__init__.py`

#### Задачи:
- [ ] Export public APIs
- [ ] Version info
- [ ] Convenience functions

```python
# __init__.py exports:
from .config import Config, get_config, reload_config
from .db import get_session, get_engine, health_check
from .llm import LLMClient, complete, LLMResponse
from .tools import platform_tool, ToolRegistry, Tool, ToolCall
from .exceptions import CoreError, ConfigError, DBError, LLMError, ToolError
```

---

## 📦 Фаза 6: Интеграционные тесты (Дни 4-5)

### 6.1 Full System Tests
**Файл:** `packages/core/tests/integration/test_full_system.py`

```python
# 1. test_config_db_llm_integration()
#    - Load config
#    - Connect to DB
#    - Make LLM call
#    - All work together

# 2. test_tool_execution_with_persistence()
#    - Define tool
#    - Execute tool
#    - Log action to DB
#    - Verify persisted

# 3. test_multi_tenancy()
#    - Two teams
#    - Different configs
#    - Isolated data

# 4. test_error_propagation_full_stack()
#    - Tool error → logged → surfaced
#    - Context preserved

# 5. test_concurrent_load()
#    - 50 concurrent requests
#    - Pool handles load
#    - No deadlocks
```

---

### 6.2 Fixtures
**Файл:** `packages/core/tests/conftest.py`

```python
@pytest.fixture
async def test_config():
    """Test configuration."""
    ...

@pytest.fixture
async def db_session():
    """Database session for tests."""
    ...

@pytest.fixture
async def llm_client():
    """Mock LLM client."""
    ...

@pytest.fixture
def sample_team():
    """Sample team data."""
    ...
```

---

## 📦 Фаза 7: Документация (День 4)

### 7.1 Docstrings
#### Задачи:
- [ ] Все public функции с docstrings
- [ ] Type hints на всех APIs
- [ ] Examples в docstrings

### 7.2 Examples
**Папка:** `packages/core/examples/`

```
examples/
├── 01_config_usage.py
├── 02_db_operations.py
├── 03_llm_integration.py
├── 04_creating_tools.py
└── 05_full_agent_flow.py
```

#### Задачи:
- [ ] Примеры рабочего кода
- [ ] Документированные шаги
- [ ] Ready to run

---

## 🧪 Требования к тестам

### Coverage
| Компонент | Minimum | Target |
|-----------|---------|--------|
| config.py | 90% | 95% |
| db.py | 85% | 90% |
| llm.py | 80% | 85% |
| tools.py | 90% | 95% |
| exceptions.py | 100% | 100% |
| **Total** | **85%** | **90%** |

### Test Categories
- **Unit tests:** Изолированные компоненты, mocks
- **Integration tests:** Взаимодействие компонентов
- **Contract tests:** Интерфейсы не меняются

### Mock Strategy
```python
# DO: Mock LLM API calls
with patch("core.llm.LiteLLM.completion") as mock:
    mock.return_value = {"choices": [...]}
    result = complete([...])
    assert result.content == "..."

# DON'T: Call real YandexGPT in tests
```

---

## 📅 Timeline

```
День 1 (утро):    Контракты + config.py stub → публикуем интерфейсы
День 1 (день):    db.py + модели + миграции
День 2:           llm.py + tools.py
День 3:           Тесты unit + фиксы
День 4:           Интеграционные тесты + examples
День 5:           Рефакторинг + code review + docs
```

---

## ✅ Definition of Done (Track B)

- [ ] `packages/core` импортируется без ошибок
- [ ] Все config fields загружаются из .env
- [ ] Миграции создают все таблицы
- [ ] `get_session()` возвращает рабочую сессию
- [ ] `complete(messages, tools)` возвращает LLMResponse
- [ ] `@platform_tool` регистрирует tool в registry
- [ ] Tool execution работает с валидацией
- [ ] Unit tests: coverage ≥ 85%
- [ ] Integration tests: end-to-end flow работает
- [ ] Примеры запускаются без ошибок
- [ ] Docstrings на всех public APIs

---

## 🚨 Critical Notes

### Интерфейсы — приоритет №1
День 1 утром ты должен опубликовать:
```python
# Это импортируют другие треки:
from core import Config, get_session, complete, platform_tool, ToolRegistry
```

### Коммуникация
- Пиши в общий чат когда интерфейс готов
- Если меняешь интерфейс → предупреждай сразу
- Другие треки кодируют против твоих stub-ов

### Testing Philosophy
- Unit tests ловят баги локально
- Integration tests ловят проблемы интеграции
- Mock external APIs всегда
- Тестируй error paths

---

## 🎯 Senior Level Checklist

- [ ] Type hints на 100% public APIs
- [ ] Pydantic v2 модели
- [ ] Async-first (asyncpg, httpx)
- [ ] Structured logging (JSON)
- [ ] Custom exceptions с context
- [ ] Error handling на каждом слое
- [ ] Retry с exponential backoff
- [ ] Connection pooling
- [ ] Graceful degradation
- [ ] Self-documenting code
- [ ] No type suppression (`as Any`)
- [ ] No empty catch blocks
- [ ] Tests for edge cases
- [ ] Performance benchmarks
- [ ] Clean git history (atomic commits)
