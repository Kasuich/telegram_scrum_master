# Goal Architecture Implementation Plan (Variant C)

> GoalItem evolves from ScenarioItem, one LLM call replaces two, success_criteria drives reflection.
> Date: 2026-06-09. Based on AGENT_IMPROVEMENT_PLAN.md diagnosis.

---

## Architecture

```
message
  │
  ├─ Step 1: build_goal_plan() → GoalPlan([GoalItem, ...])
  │   ONE LLM call (replaces decompose_turn_llm + classify_stage_llm)
  │   GoalItem = {stage, payload, intent, entities, success_criteria, missing_info}
  │
  ├─ Step 2: CLARIFY GATE
  │   if any GoalItem.missing_info not empty →
  │     agent formulates ONE human question (not _is_chatty_delegation)
  │     pause, wait for response
  │     re-run build_goal_plan with updated context
  │
  ├─ Step 3: ACT (existing ReAct loop, minimal changes)
  │   for each GoalItem:
  │     freeze stage = GoalItem.stage_hint
  │     run_scenario (as before, but stage is hint not sentence)
  │     terminal by success_criteria, not by any_read_answer
  │
  └─ Step 4: REFLECT vs success_criteria
      goal met → RESPOND (human voice)
      not met + budget → back to ACT
      unreachable → honest answer
```

## File-by-file changes

### 1. NEW: `packages/core/src/core/goal.py`

Replace turn_plan.py as the primary planning module. turn_plan.py stays for backward compat during migration.

```python
@dataclass
class GoalItem:
    stage: StageId              # hint, not sentence (was ScenarioItem.stage)
    payload: str                # fragment of original message
    intent: str                 # "узнать суммарные SP Николая"
    entities: dict[str, str]    # {"person": "Коля", "metric": "SP", "assignee": "nikolai"}
    success_criteria: str       # "число — сумма SP всех задач Коли на доске"
    missing_info: list[str]     # [] or ["неясно какой приоритет"]
    rationale: str | None = None  # kept for compat

@dataclass
class GoalPlan:
    items: list[GoalItem]
    is_dialog: bool = False
```

LLM decomposer prompt: requests `{stage, payload, intent, entities, success_criteria, missing_info}` JSON.
One call replaces `decompose_turn_llm` + `classify_stage_llm`.

Serialization/deserialization: same pattern as turn_plan.py.

`build_goal_plan(message, *, use_llm=True) -> GoalPlan` — main entry point.
Rules fallback: `detect_stage_rules` for stage, empty intent/entities/criteria.

### 2. MODIFY: `packages/core/src/core/invocation.py`

Add fields to InvocationContext:
```python
actor_tracker_login: str | None = None
actor_role: str | None = None
actor_default_board_id: str | None = None
actor_settings: dict[str, Any] = Field(default_factory=dict)
```

Update `format_transport_context_for_prompt` to reveal ALL identity fields:
```python
if ctx.actor_tracker_login:
    lines.append(f"- your_tracker_login: {ctx.actor_tracker_login}")
if ctx.actor_role:
    lines.append(f"- your_role: {ctx.actor_role}")
if ctx.actor_default_board_id:
    lines.append(f"- your_default_board: {ctx.actor_default_board_id}")
if ctx.actor_settings:
    for k, v in ctx.actor_settings.items():
        lines.append(f"- preference_{k}: {v}")
```

Also reveal metadata fields if present (backward compat):
```python
for key in ("tracker_login", "role", "default_board_id"):
    val = ctx.metadata.get(key)
    if val and key not in already_rendered:
        lines.append(f"- {key}: {val}")
```

### 3. MODIFY: `packages/core/src/core/tracker_tools.py`

Enrich `tracker_board_snapshot` return with:
- `by_assignee_sp: dict[str, float]` — sum of story points per assignee
- `total_sp: float` — total SP on board
- `by_status_sp: dict[str, float]` — SP by status (useful for burndown)

Implementation: accumulate `story_points` from `issue_summary()` alongside existing counters.

### 4. MODIFY: `packages/core/src/core/react.py`

#### 4a. Wire GoalPlan
- Import `build_goal_plan` from `core.goal`
- Replace `plan_turn(message)` call with `build_goal_plan(message)`
- Replace `TurnPlan` / `ScenarioItem` usage with `GoalPlan` / `GoalItem`
- `state["_plan"]` = `serialize_goal_plan(goal_plan)` instead of `serialize_plan`

#### 4b. Clarify Gate (replace _is_chatty_delegation)
- After `build_goal_plan()`, check if any `GoalItem.missing_info` is non-empty
- If yes: formulate clarification from missing_info, return `ScenarioOutcome(kind="clarification", ...)`
- Remove `_is_chatty_delegation` function
- Remove nudge logic (`_action_only_nudges`)

#### 4c. Terminal-by-goal
- QUERY stage: remove `any_read_answer` as terminal predicate
- New terminal: `goal_terminal(goal_item, turn_steps)` — checks if success_criteria likely met
  - Deterministic: if a read tool returned data that could answer the criteria
  - Fallback: max_iterations (safety net)
- Other stages: keep existing terminal predicates (they work for mutations)

#### 4d. Reflection vs success_criteria — TWO-TIER verification

**Tier 1: Deterministic (free, 0 LLM calls)**

| Stage | Check | Example |
|---|---|---|
| INTAKE | `created_issue_keys_in_turn` + assignee matches entities | «создай Коле задачу» → key created, assignee=nikolai |
| STATUS | `comment_succeeded` | comment went through |
| TRANSITION | `transition_or_close_succeeded` | status changed |
| QUERY | tool_result contains data matching entities | «сколько SP у Коли?» → `by_assignee_sp["nikolai"]` exists |

**Tier 2: LLM Judge (only when Tier 1 is uncertain)**

```python
_GOAL_JUDGE_SYSTEM = (
    "Ты — судья PM-агента. Реши, достигнута ли цель по результатам инструментов.\n"
    "Ответь СТРОГО одно слово: YES / NO / NEEDS_MORE.\n"
    "Если NO или NEEDS_MORE — через | укажи что не хватает.\n"
    "Пример: NO|нет данных по story points"
)
# temperature=0.0, max_tokens=32
```

When judge is invoked:
- Tier 1 returned "uncertain" (data exists but unclear if sufficient)
- Multi-step goal (several tools in plan)
- After retry, still unclear

New function `_goal_met(goal_item, turn_steps, *, ctx=None)`:
1. Try Tier 1 deterministic check for the goal_item.stage
2. If definitive → return True/False
3. If uncertain → call LLM judge (Tier 2)
4. Return GoalVerdict(result: bool, reason: str, tier: int)

**Why not always judge:** SP-example is pure deterministic — `by_assignee_sp["nikolai"] = 15.0` means goal met. Judge is overkill there. Judge is needed for: "оформи доску" (all tasks created?), "найди просрочку и предупреди" (found + warned?), after retry (better?).

Retry loop: if not met and budget → back to ACT with judge feedback, not brute retry

#### 4e. Human confirmations
- Replace raw confirm prompt:
  ```python
  # OLD:
  f"Agent wants to call '{tool_call.name}' (risk={tool.risk}) with: {tool_call.arguments}"
  # NEW:
  f"Запрос на действие: {_human_tool_description(tool_call.name, tool_call.arguments)}\nРиск: {tool.risk}\nРазрешить?"
  ```
- `_human_tool_description`: maps tool names to Russian descriptions + human-readable arg summary

### 5. MODIFY: `packages/core/src/core/stage_graph.py`

- Keep all stage definitions, guards, forced_next as-is
- Change QUERY terminal: from `any_read_answer` to `lambda steps: False` (let react.py decide)
- Add `max_iterations` parameter to Stage (default None = unlimited for QUERY, existing limits for others)

### 6. MODIFY: `packages/core/src/core/assignee_resolver.py`

- Add `resolve_first_person(ctx: InvocationContext) -> str | None`
  - If message contains "мне/мной/я/мои/мой/моё/моя" and `ctx.actor_tracker_login` exists
  - Return `ctx.actor_tracker_login`
- Used by GoalItem builder and by react.py before LLM call

### 7. MODIFY: `services/platform-api/src/platform_api/telegram_bridge.py`

- Extend `context.metadata.update()` at line ~586 to include `role` and `settings_json`
- Map new InvocationContext fields from metadata

### 8. MODIFY: `services/pm-orchestrator/src/pm_orchestrator/agents/pm_agent.py`

- Add identity block to prompt:
  ```
  ## Кто ты и с кем говоришь
  Если сообщение содержит «мне/я/мои» — это про пользователя {your_tracker_login}.
  Дефолтная доска: {your_default_board}. Роль: {your_role}.
  ```
- Remove «Ты НЕ чат-бот» and «Без вопросов» (Phase 3, but prompt can soften now)
- Keep action_only=True for now (change in Phase 3)

---

## Test strategy

### Unit tests (packages/core/tests/unit/)

1. **test_goal.py** (new):
   - `test_goal_item_creation` — dataclass fields
   - `test_goal_plan_serialization` — round-trip
   - `test_parse_goal_json` — parse LLM output
   - `test_rules_fallback` — no LLM, rules-only path
   - `test_missing_info_detection` — clarify gate triggers
   - `test_first_person_resolution` — "мне" → tracker_login from context

2. **test_invocation.py** (extend existing or new):
   - `test_actor_tracker_login_in_prompt` — format_transport_context_for_prompt includes tracker_login
   - `test_actor_role_in_prompt` — includes role
   - `test_metadata_fallback` — metadata fields still rendered if no explicit fields

3. **test_board_snapshot_sp.py** (extend existing or new):
   - `test_by_assignee_sp` — SP sums per assignee
   - `test_total_sp` — board total
   - `test_no_sp_tasks` — tasks without SP don't break sums

4. **test_goal_reflection.py** (new):
   - `test_goal_met_deterministic` — QUERY with matching data → True
   - `test_goal_not_met` — no relevant data → False → retry
   - `test_clarify_gate` — missing_info → clarification outcome

5. **test_human_confirm.py** (extend existing):
   - `test_confirm_prompt_human_readable` — no raw tool name in prompt
   - `test_confirm_prompt_russian` — Russian text

### Integration test (packages/core/tests/)

6. **test_goal_e2e.py** (new):
   - `test_sp_example` — "Сколько SP у Коли?" → GoalItem(stage=QUERY, success_criteria="сумма SP Коли") → board_snapshot → reflection → answer
   - `test_create_for_me` — "Создай мне задачу" → GoalItem(entities={assignee: ctx.tracker_login}) → create

---

## Migration path (backward compat)

- `turn_plan.py` stays, `GoalPlan` is opt-in via `use_goal_plan=True` flag in react.py
- Phase 0: GoalItem dataclass + LLM decomposer (no react.py changes yet)
- Phase 1: Wire GoalPlan into react.py behind flag
- Phase 2: Remove flag, delete old TurnPlan path
- This lets us test GoalItem independently before changing the main loop
