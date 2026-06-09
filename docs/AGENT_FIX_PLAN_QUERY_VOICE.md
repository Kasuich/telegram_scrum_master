# План правок: «живой голос» для read-вопросов + анализ регрессий

> Составлено: 2026-06-09. Это ПЛАН правок (не применённый патч) + анализ влияния на
> нечатовый функционал. База: ре-аудит изменений рабочего дерева (Goal-слой, идентичность,
> суммы SP, clarify-гейт, человеческое подтверждение уже внедрены).

## Проблема, которую чиним

Контрольный сценарий «Сколько story points у Коли?» по текущему коду доходит до
пользователя как **`«tracker_board_snapshot: выполнено»`**, а не словами. Причина —
для `action_only` финализация перетирает естественный ответ LLM детерминированным отчётом
в ДВУХ местах:

- `react.py:603` `_action_only_final_reply` → сначала `_build_action_report`, и только если он
  пуст — `llm_text`. Для `board_snapshot` отчёт непустой (дефолт `«tool: выполнено»`,
  `react.py:374`) → ответ LLM выбрасывается.
- `react.py:919-920` `_reflect_and_finalize` (single-goal): при `_goal_terminal_for_stage` →
  `reply = _build_action_report(...)`, снова перетирая `outcome.reply` (llm_text).

Вторично: `_goal_met` для QUERY (`react.py:494-497`) — обе ветки идентичны, `entities` не влияет:
«есть данные» = «цель достигнута», даже если данные не отвечают на вопрос (счётчик вместо суммы SP).

---

## Правки (по файлам)

### Правка 1 — read-цели финализируются текстом LLM (критическая)

**Файл:** `packages/core/src/core/react.py`

**1а. `_action_only_final_reply` — принять стадию и для read-стадий предпочесть llm_text.**
```
# было
def _action_only_final_reply(steps, llm_text, had_tool):
    report = _build_action_report(steps)
    if report: return report
    if not had_tool: return "Действия не выполнены."
    return llm_text

# станет
_READ_VOICE_STAGES = {StageId.QUERY}   # PROACTIVE/HYGIENE НЕ трогаем (нечатовые дайджесты)
def _action_only_final_reply(steps, llm_text, had_tool, *, stage_id=None):
    # read-цель: ответ словами от LLM приоритетнее отчёта
    if stage_id in _READ_VOICE_STAGES and llm_text.strip():
        return llm_text.strip()
    report = _build_action_report(steps)
    if report: return report
    if not had_tool: return "Действия не выполнены."
    return llm_text
```
Вызов на `react.py:1300` — передать `stage_id=StageId(state.get("_stage"))` (он уже в state).

**1б. `_reflect_and_finalize` single-goal — для QUERY не перетирать `outcome.reply`.**
`react.py:918-924`:
```
if outcome.kind == "done":
    if stage and stage.id == StageId.QUERY and outcome.reply:
        reply = outcome.reply                      # llm_text, уже собранный в _run_scenario
    elif _goal_terminal_for_stage(stage, item, turn_steps):
        reply = _build_action_report(turn_steps) or "Действие выполнено."
    elif outcome.reply:
        reply = outcome.reply
    else:
        reply = _build_action_report(turn_steps) or "Действие выполнено."
```

> Скоуп строго `StageId.QUERY`. INTAKE/BOARD/STATUS/TRANSITION/REORG/HYGIENE/PROACTIVE — без
> изменений (их отчёты сохраняются как есть).

### Правка 2 — `_goal_met` для QUERY с метрикой не закрывать на tier-1

**Файл:** `packages/core/src/core/react.py:489-497`
```
elif stage_id == StageId.QUERY:
    has_data = any(... not error ...)
    metric = (goal_item.entities or {}).get("metric")
    if has_data and not metric:
        return GoalVerdict(met=True, reason="query_data_present", tier=1)
    # метрика (SP/кол-во/нагрузка) запрошена → пусть судья проверит, что ответ её содержит
    if has_data and metric and not use_llm:
        return GoalVerdict(met=True, reason="query_data_present_no_llm", tier=1)  # фолбэк для тестов
    # иначе — провалимся в tier-2 ниже
```
Тем самым «есть строка board_snapshot» больше не считается ответом на «сколько SP»; судья
(`_GOAL_JUDGE_SYSTEM`) решит по `success_criteria`. **`use_llm=False` сохраняет детерминизм** —
важно для юнит-тестов и резюма.

### Правка 3 — резолв «мне / Коля» ДО clarify-гейта (снять переспрашивание)

**Файлы:** `packages/core/src/core/react.py:735-745`, `assignee_resolver.py`

Перед тем как собирать вопрос из `missing_info`:
1. Если в `missing_info`/`entities` фигурирует исполнитель и сообщение в 1-м лице
   («мне/я/мои») → подставить `ctx.actor_tracker_login`, убрать пункт из `missing_info`.
2. Если упомянуто имя («Коля») и среди членов команды ровно одно совпадение по
   `tracker_display_name` → резолвить, убрать из `missing_info`.
3. Только непокрытый остаток выносить в вопрос, и формулировать по-человечески
   («У нас два Николая — Коновалов и Петров. Чей SP?»), а не дампом списка.

### Правка 4 — мелочи корректности

- **Промпт-противоречие** `pm_agent.py:18-21`: убрать жёсткое «ЗАПРЕЩЕНО: укажите задачу /
  хотите ли вы», т.к. оно конфликтует с «если данных нет — уточни». Оставить запрет только на
  пустые вопросы при наличии данных.
- **Пагинация снапшота** `tracker_tools.py` (`search_issues(..., limit=200)`): добавить дочитку
  страниц (или явный флаг), иначе `total_sp`/`by_assignee_sp` врут на доске >200 задач.
  Дефолт лимита НЕ уменьшать (чтобы не сдвинуть числа в `test_board_snapshot`).

---

## Анализ регрессий: не ломаем ли нечатовый функционал

Матрица: каждая нечатовая функция × затрагивает ли её правка × почему безопасно / что проверить.

| Нечатовый функционал | Путь в коде | Правка 1 (voice) | Правка 2 (goal_met) | Правка 3 (clarify) | Правка 4 |
|---|---|---|---|---|---|
| **Дневной дайджест (cron)** | `daily_digest.build_daily_digest_report`/`format_daily_digest` — строится **напрямую**, НЕ через ReAct/QUERY | ❌ не затрагивает | ❌ | ❌ | ⚠️ если включить пагинацию — числа дайджеста станут полнее (это корректнее, но проверить тексты тестов дайджеста) |
| **PROACTIVE (cron-проверки агентом)** | отдельная стадия `PROACTIVE`, отчёт-дайджест | ❌ скоуп только QUERY | ❌ QUERY-only | ❌ | ❌ |
| **INTAKE — создание задач/спринтов** | `react.py:1400` / `_format_action_tool_line` «Создана …» | ❌ QUERY-only | ❌ | ⚠️ «создай мне» теперь резолвит логин вместо вопроса — это цель правки; проверить, что create-тесты не ждали уточнения | ❌ |
| **BOARD — backlog_plan→apply** | forced edge, «Доска: создано N» | ❌ | ❌ | ❌ | ❌ |
| **STATUS — «Имя:» → comment** | find→summarizer→comment, «Комментарий …» | ❌ | ❌ | ❌ | ❌ |
| **TRANSITION / REORG / HYGIENE** | отчёты по действиям | ❌ QUERY-only | ❌ | ❌ | ❌ |
| **meeting_summarizer** | `action_only=False` → ветка `llm_text` (`react.py:1302`), не отчёт | ❌ (правка только для action_only) | ❌ | ❌ | ❌ |
| **Confirm + resume (inline-кнопки)** | `_resolve_confirm`/resume, `skip_tool_confirm` | ❌ | ❌ | ❌ | ❌ |
| **Multi-scenario отчёт** | `_build_multi_scenario_report`/`_reflection_llm_check` (`react.py:880-887`) | ❌ скоуп — single-goal QUERY; multi оставляем как есть | ❌ | ❌ | ❌ |
| **Scheduler (cron-тайминги)** | `scheduler.py` — только расчёт времени | ❌ | ❌ | ❌ | ❌ |

**Вывод:** все четыре правки **скоупятся на чат-путь read-вопросов (StageId.QUERY) и на 1-е лицо**.
Нечатовые пути (дайджест, PROACTIVE, INTAKE/BOARD/STATUS/TRANSITION/REORG/HYGIENE-отчёты,
summarizer, confirm/resume, multi-scenario, scheduler) либо идут по другим веткам, либо защищены
скоупом `StageId.QUERY`/`action_only`. Самый чувствительный момент — Правка 3 (меняет, КОГДА
задаётся уточнение): затронет сценарии «создай мне», но это и есть желаемое поведение.

---

## Влияние на тесты (что проверить / обновить)

Прогон базлайна сейчас: **103 passed** (goal/clarify/reflection/human_confirm/board_snapshot/invocation).

| Тест | Ожидание после правок |
|---|---|
| `test_react_stages.py` QUERY-тест (≈187-218) | Проверяет «board_snapshot вызван» + «write заблокирован», НЕ текст reply → **должен пройти** |
| `test_react_stages.py` INTAKE/STATUS/BOARD (≈252/272/181) | Скоуп QUERY → **без изменений** |
| `test_react_stages.py` multi-scenario (≈408-445) `«✓ QUERY»` | multi-путь не трогаем → **без изменений** |
| `test_react_stages.py` clarify `«Уточни»` (≈386) | Правка 3 может убрать уточнение, если данных хватает из идентичности → **возможно обновить тест** |
| `test_goal_reflection.py` | Правка 2 c `use_llm=False` сохраняет tier-1 фолбэк → **должен пройти**; добавить кейс «QUERY+metric → tier-2 судит» |
| `test_board_snapshot.py` | Лимит не уменьшаем → числа те же → **без изменений** |
| **НОВЫЙ** `test_query_voice.py` | End-to-end: «Сколько SP у Коли?» → `board_snapshot` (с `by_assignee_sp`) → reply содержит число/имя, НЕ `«…: выполнено»` |

---

## Порядок и верификация

1. Правка 1 (voice) + новый `test_query_voice.py` — закрывает критбаг, мгновенный видимый эффект.
2. Правка 2 (goal_met) + кейс в `test_goal_reflection.py`.
3. Правка 3 (идентичность до clarify) + обновить clarify-тест.
4. Правка 4 (промпт + пагинация).
5. **Регресс-прогон:** `pytest packages/core/tests services/*/tests -q` — должно остаться зелёным,
   кроме намеренно обновлённого clarify-теста.
6. Ручная проверка нечатовых путей: дайджест (`send_team_daily_digest`), создание задачи,
   оформление доски из саммари, «Имя:»-статус — все дают прежние отчёты.
