# Гайд разработчика: добавление нового агента

Добавление агента — это создание **одного файла** в `services/pm-orchestrator/src/pm_orchestrator/agents/`. Никакой регистрации, никаких миграций, никакого конфига.

---

## Быстрый старт (5 минут)

### 1. Создайте файл агента

```python
# services/pm-orchestrator/src/pm_orchestrator/agents/my_agent.py

from __future__ import annotations

from core.agent import BaseAgent, LLMSettings


class MyAgent(BaseAgent):
    name = "my_agent"                          # уникальный slug (snake_case)
    description = "Что делает агент"           # показывается в GET /agents
    prompt = """Ты — агент для ...
    Отвечай кратко на русском языке."""
    tools = []                                  # имена @platform_tool
    llm_configs = [
        LLMSettings(model="yandexgpt", temperature=0.3),
        LLMSettings(model="yandexgpt-lite", temperature=0.3),  # fallback
    ]
```

**Всё.** При следующем старте оркестратора агент появится в `GET /agents` и будет доступен на `POST /agents/my_agent/chat`.

---

## Подробный гайд

### Шаг 1: Определите инструменты

Инструменты — это async-функции, задекорированные `@platform_tool`:

```python
# services/pm-orchestrator/src/pm_orchestrator/agents/my_agent.py
from __future__ import annotations

from typing import Any

from core.tools import platform_tool


@platform_tool(name="my_tool_read", risk="low", scopes=["myservice:read"])
async def my_tool_read(param: str) -> dict[str, Any]:
    """Прочитать что-то. Описание идёт в LLM."""
    # реальная логика
    return {"result": param}


@platform_tool(name="my_tool_write", risk="medium", scopes=["myservice:write"])
async def my_tool_write(target: str, value: str) -> dict[str, Any]:
    """Записать что-то. medium = требует подтверждения пользователя."""
    return {"written": True, "target": target}
```

**Уровни риска:**
| risk | поведение |
|------|----------|
| `low` | выполняется автоматически |
| `medium` | пауза → пользователь подтверждает ✅ или отклоняет ❌ |
| `high` | всегда требует confirm (деструктивные операции) |

### Шаг 2: Напишите агента

```python
from __future__ import annotations

# Импорт регистрирует инструменты в ToolRegistry — side effect!
import pm_orchestrator.agents.my_agent as _tools  # noqa: F401

from core.agent import BaseAgent, LLMSettings


class MyAgent(BaseAgent):
    name = "my_agent"
    description = "Агент для работы с MyService"

    # Системный промпт: расскажите LLM кто он и что умеет
    prompt = """Ты — агент для работы с MyService.

## Доступные инструменты
- my_tool_read  — прочитать данные по параметру
- my_tool_write — записать значение (требует подтверждения)

## Правила
1. Отвечай кратко на русском языке.
2. Перед записью уточни у пользователя если что-то неясно.
"""

    tools = [
        "my_tool_read",
        "my_tool_write",
    ]

    llm_configs = [
        LLMSettings(model="yandexgpt", temperature=0.3),
        LLMSettings(model="yandexgpt-lite", temperature=0.3),  # fallback
    ]
```

### Шаг 3: Перезапустите оркестратор

```bash
# Локально
uvicorn pm_orchestrator.rpc:app --reload --port 8001

# В Docker
docker compose restart pm-orchestrator
```

### Шаг 4: Проверьте

```bash
# Агент появился?
curl http://localhost:8001/health
# → {"status": "ok", "agents": ["pm_agent", "my_agent"]}

# Поговорите с ним
curl -X POST http://localhost:8000/agents/my_agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "прочитай foo", "session_id": "test1"}'
```

---

## Структура файла агента (шаблон)

```
services/pm-orchestrator/src/pm_orchestrator/agents/
└── my_agent.py          # всё в одном файле для простых агентов

# Для сложных агентов с многими инструментами:
└── my_agent/
    ├── __init__.py      # from .agent import MyAgent
    ├── agent.py         # class MyAgent(BaseAgent)
    └── tools.py         # @platform_tool функции
```

---

## LLM конфигурация

```python
llm_configs = [
    # Primary: точная модель для сложных задач
    LLMSettings(model="yandexgpt", temperature=0.1),

    # Fallback: быстрая/дешёвая если primary недоступна
    LLMSettings(model="yandexgpt-lite", temperature=0.1),
]
```

Если primary упала с `LLMError` — автоматически пробуется следующая. Количество попыток отражается в `AgentResult.llm_attempts`.

**Параметры LLMSettings:**
| Параметр | Описание | По умолчанию |
|----------|----------|------|
| `model` | `yandexgpt` или `yandexgpt-lite` | `yandexgpt` |
| `temperature` | Случайность (0.0 = детерминировано) | из конфига |
| `max_tokens` | Макс. токенов в ответе | из конфига |
| `timeout` | Таймаут запроса (сек) | 60 |
| `max_retries` | Ретраи при ошибке сети | 3 |

---

## Переменные в промпте

```python
prompt = "Сегодня {current_date}. Пользователь: {user_name}. Задача: {task}."

# При вызове передаём значения:
result = await runner.invoke(
    "привет",
    session_id="s1",
    # prompt_vars передаются через ReActRunner
)
```

Если переменная есть в промпте но не передана → `AgentError`.

---

## Autonomy Gate: тонкая настройка

По умолчанию: `low` — авто, `medium/high` — confirm.

Переопределить для конкретного агента при регистрации в `orchestrator.py`:

```python
# orchestrator.py (метод _register)
rc = RuntimeConfig(
    auto_risk=["low"],
    confirm_risk=["medium", "high"],
    always_confirm_tools=["my_critical_tool"],  # всегда confirm
)
```

---

## Тестирование агента

### Unit-тест (мок LLM)

```python
# services/pm-orchestrator/tests/test_my_agent.py
import os
os.environ.setdefault("YC_API_KEY", "stub")
# ... остальные env vars

from unittest.mock import AsyncMock, patch
from pm_orchestrator.orchestrator import OrchestratorService
from pm_orchestrator.agents.my_agent import MyAgent

async def test_my_agent_text_reply():
    svc = OrchestratorService()
    svc._register(MyAgent())

    mock_response = {
        "result": {
            "alternatives": [{
                "message": {"role": "assistant", "text": "Готово!"},
                "status": "ALTERNATIVE_STATUS_FINAL",
            }],
            "usage": {"inputTokens": "5", "completionTokens": "3", "totalTokens": "8"},
        }
    }

    with patch("httpx.AsyncClient.post", AsyncMock(return_value=mock_ok(mock_response))):
        result = await svc.invoke("my_agent", "привет", "s1")

    assert result.reply == "Готово!"
```

### Smoke-тест на реальном API

```python
# examples/smoke_my_agent.py
import asyncio
from pm_orchestrator.orchestrator import OrchestratorService
from pm_orchestrator.agents.my_agent import MyAgent

async def main():
    svc = OrchestratorService()
    svc._register(MyAgent())
    result = await svc.invoke("my_agent", "привет", "demo")
    print(result.reply or result.pending_confirm)

asyncio.run(main())
```

---

## Checklist: добавление агента

- [ ] Создан файл `agents/my_agent.py`
- [ ] Определён `class MyAgent(BaseAgent)` с `name`, `description`, `prompt`, `tools`, `llm_configs`
- [ ] Инструменты (`@platform_tool`) с правильными `risk` уровнями
- [ ] Промпт на русском, понятно объясняет агенту его роль и инструменты
- [ ] Unit-тест в `tests/test_my_agent.py`
- [ ] Проверено локально: `GET /agents` возвращает нового агента
- [ ] Протестирован сценарий: text-reply, tool-call, confirm-flow

---

## FAQ

**Q: Нужно ли регистрировать агента где-то кроме файла?**
A: Нет. `OrchestratorService.discover_agents()` сканирует `agents/` при старте.

**Q: Агент не появляется в `/agents` после добавления файла.**
A: Проверь что класс унаследован от `BaseAgent` и `name` не пустой. Посмотри логи оркестратора.

**Q: Как передать данные между инструментами?**
A: Результат каждого инструмента добавляется в историю разговора. LLM видит все предыдущие результаты и использует их при следующем вызове.

**Q: Как дать агенту доступ к кастомному API?**
A: Создай `@platform_tool` функцию, которая делает httpx-запрос. Укажи нужный `risk` уровень.

**Q: Можно ли использовать одного агента как инструмент другого?**
A: В текущей архитектуре — нет (in-memory сессии изолированы). В будущем — через A2A протокол.
