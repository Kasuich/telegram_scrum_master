# Создание агентов

Платформа использует **code-first** подход: агент — это Python-класс, который автоматически подхватывается системой без ручных INSERT в БД.

---

## Быстрый старт

### 1. Определите инструменты

```python
# myservice/tools.py
from core.tools import platform_tool

@platform_tool(name="get_sprint_status", risk="low", scopes=["tracker:read"])
async def get_sprint_status(queue: str) -> dict:
    "Return current sprint status for a queue."
    return {"queue": queue, "open": 5, "done": 12}

@platform_tool(name="create_issue", risk="medium", scopes=["tracker:write"])
async def create_issue(queue: str, summary: str, priority: str = "normal") -> dict:
    "Create a Yandex Tracker issue."
    return {"key": f"{queue}-42", "summary": summary}
```

Параметры декоратора:

| Параметр | Описание |
|----------|----------|
| `name` | Уникальный идентификатор инструмента |
| `risk` | `"low"` / `"medium"` / `"high"` — уровень риска для ворот автономии |
| `scopes` | Список scope-ов для контроля доступа |
| `description` | Описание для LLM (если не указан — берётся из docstring) |

---

### 2. Определите агента

```python
# myservice/agents.py
from core.agent import BaseAgent, LLMSettings

class PMReportAgent(BaseAgent):
    name = "pm_report_agent"             # уникальный slug
    description = "Готовит отчёты по спринту из данных Tracker"
    prompt = "Ты PM-ассистент. Сегодня {current_date}. Отвечай кратко."
    tools = ["get_sprint_status"]        # имена из @platform_tool
    llm_configs = [
        LLMSettings(model="gpt-oss-120b", temperature=0.3),   # primary
        LLMSettings(model="yandexgpt-lite", temperature=0.3),  # fallback
    ]
```

Обязательные атрибуты класса:

| Атрибут | Тип | Описание |
|---------|-----|----------|
| `name` | `str` | Уникальный идентификатор (snake_case) |
| `description` | `str` | Описание для пользователей и sub-agent вызовов |
| `prompt` | `str` | Системный промпт; поддерживает `{переменные}` |
| `tools` | `list[str]` | Имена инструментов из `@platform_tool` |
| `llm_configs` | `list[LLMSettings]` | Конфиги LLM — перебираются по порядку при ошибке |

---

### 3. Оберните в бот

```python
# myservice/bot.py
from core.agent import LLMSettings
from core.bot import BaseBot
from core.entry_point import EntryPoint
from myservice.agents import PMReportAgent, TaskCreatorAgent

PM_BOT = BaseBot(
    bot_id="pm_bot",                    # уникальный ID
    name="PM Bot",
    description="Ассистент для управления проектами",
    entry_point=EntryPoint(PMReportAgent()),   # один агент
    platforms=["web", "telegram"],
)
```

Бот **автоматически регистрируется** в `BotRegistry` при создании — никаких дополнительных вызовов не нужно.

---

## EntryPoint: два режима

### Режим одного агента

```python
entry_point = EntryPoint(PMReportAgent())
```

Все сообщения уходят в один агент.

### Меню (несколько агентов)

```python
entry_point = EntryPoint({
    "report": PMReportAgent(),
    "task":   TaskCreatorAgent(),
})
```

Пользователь вызывает нужного агента через команду:

```
/report Покажи статус спринта BACKEND
/task   Создай задачу «Обновить README»
/help   Список доступных команд
```

Сообщение без `/команды` маршрутизируется к первому агенту в словаре.

---

## Несколько LLM и fallback

```python
llm_configs = [
    LLMSettings(model="gpt-oss-120b",  temperature=0.3),  # пробуется первым
    LLMSettings(model="yandexgpt-lite", temperature=0.3),  # fallback при ошибке
]
```

Если первая модель вернула ошибку (`LLMError`), агент автоматически пробует следующую. В `AgentResponse.llm_attempts` отражается сколько конфигов было использовано.

Параметры `LLMSettings`:

| Параметр | Тип | По умолчанию | Описание |
|----------|-----|------|----------|
| `model` | `str` | `"gpt-oss-120b"` | Модель YandexGPT |
| `temperature` | `float \| None` | `None` | Температура (наследует из конфига) |
| `max_tokens` | `int \| None` | `None` | Макс. токенов |
| `timeout` | `int \| None` | `None` | Таймаут запроса (сек) |
| `max_retries` | `int \| None` | `None` | Кол-во ретраев внутри одного клиента |

---

## Переменные в промпте

```python
prompt = "Ты ассистент. Сегодня {current_date}. Пользователь: {user}."

# При вызове:
response = await agent.run(
    [Message(role="user", content="Привет!")],
    prompt_vars={"current_date": "2026-06-03", "user": "Alice"},
)
```

Если переменная есть в промпте, но не передана в `prompt_vars` — бросается `AgentError`.

---

## Реестр ботов

```python
from core.registry import get_bot_registry

registry = get_bot_registry()

registry.list_all()                  # все боты
registry.list_for_platform("web")   # боты для конкретной платформы
registry.get("pm_bot")              # бот по ID
registry.exists("pm_bot")          # проверить наличие
```

---

## Полный пример

Смотри: [`packages/core/examples/06_code_first_agents.py`](../packages/core/examples/06_code_first_agents.py)

```
uv run python packages/core/examples/06_code_first_agents.py
```

---

## Структура файлов

```
packages/core/src/core/
├── agent.py        # BaseAgent, LLMSettings, AgentResponse
├── bot.py          # BaseBot
├── entry_point.py  # EntryPoint (agent / menu)
└── registry.py     # BotRegistry, get_bot_registry()
```

Тесты: [`packages/core/tests/unit/test_agent_framework.py`](../packages/core/tests/unit/test_agent_framework.py)
