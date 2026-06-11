# Deadline Reminders + Roles — Feature Plan

Status: **planned** · Author: planning session 2026-06-11 · Target team: `Default Team`
(`00000000-0000-0000-0000-000000000001`, Tracker queue `TEST`)

This document is the full plan for two related features discovered while scoping
"deadline reminders for the team":

- **MVP (build now):** Deadline reminders delivered as private Telegram DMs.
- **Deferred (post-MVP):** Team roles that activate the already-built console RBAC.

The deferred roles section is kept here so the design is not lost, but it is **out
of scope for the MVP** per the product decision on 2026-06-11.

---

## 0. Why this is cheap to build

Every building block already exists and is proven in production; we extend proven
paths rather than add infrastructure.

| Need | Existing mechanism | Location |
|---|---|---|
| Background scheduling | `SchedulerDaemon` ticks 60s, `SELECT … FOR UPDATE SKIP LOCKED`, dispatches by `payload["type"]` | `core/scheduler.py` |
| **Private DM delivery (no group chat)** | `standup_poll._enqueue_private_message` → `TelegramOutbox(target_user_id=…)`; gateway sends `sendMessage` to `target_chat_id or target_user_id` | `core/standup_poll.py:310`, `telegram-gateway/runtime.py:226` |
| Deadline querying | YQL `Deadline: < now()` (documented), search returns the `deadline` field | `tracker.py`, `tracker_tools.py:357`, `tracker_tool_helpers.py:293` |
| DM-able member resolution | `load_registered_participants` (active TG link + confirmed match) | `standup_poll.py:191` |
| Member role label | `team_memberships.role` (default `user`) | `models.py:247` |
| Console permission tier | `users.role ∈ {dev, admin, user}` + `require_roles(...)` | `console-api/main.py:52, 337` |
| Config-driven cron, in-flight update | `cron_expr` config + `_ensure_scheduled_job` updates live job on restart | `config.py:299`, `daily_digest.py:815` |
| Job seeding at boot | `ensure_schema_and_seed` → `ensure_daily_digest_scheduled_job` | `orchestrator.py:122` |

**Key constraint:** the existing *daily digest* is broken because it posts to a
**group chat**. Deadline reminders therefore ride the **private-DM channel** only
and never touch the group-digest path.

---

## 1. MVP — Deadline Reminders

### 1.1 Behaviour

- **Recipients (both):**
  1. **Per-assignee private DM** — each team member gets a DM listing only *their
     own* at-risk tasks. Members with nothing at risk get no message.
  2. **Lead summary DM** — one consolidated DM to the team lead with everyone's
     at-risk tasks. (Lead resolution: see 1.5; until roles ship, this is config-
     pinned to `nukolaus`.)
- **Window (overdue + due-soon):** open tasks (`Resolution: empty()`) whose
  `Deadline` is `≤ today + soon_days` (default 3), split into two buckets:
  - 🔴 **Просрочено** — `deadline < today`
  - 🟡 **Скоро дедлайн** — `today ≤ deadline ≤ today + soon_days`
- **Schedule:** starts **hourly**, later switches to **daily at 16:00 MSK** (see 1.6).
- **No LLM call** — deterministic Tracker query → formatted DM. Cheap, reliable.
- **No group chat, no DB migration.**

### 1.2 Config — `DeadlineReminderConfig`

New section in `core/config.py` (env prefix `deadline_reminder_`), registered on
`Config` and exported:

| Field | Default | Notes |
|---|---|---|
| `enabled` | `True` | toggle the scheduled job |
| `cron_expr` | `"0 * * * *"` | **Phase 1 hourly**; → `"0 13 * * *"` for Phase 2 |
| `timezone` | `"Europe/Moscow"` | day-window + "today" reference |
| `soon_days` | `3` | due-soon window |
| `max_issues_per_member` | `20` | cap per DM |
| `notify_assignees` | `True` | per-assignee DMs |
| `notify_lead` | `True` | lead summary DM |
| `lead_roles` | `"lead,admin"` | which `team_memberships.role` values receive the summary |

### 1.3 New module — `core/deadline_reminders.py`

Modeled on `standup_poll.py` / `daily_digest.py`.

- Constants: `DEADLINE_REMINDER_JOB_NAME = "team_deadline_reminder"`,
  `DEADLINE_REMINDER_PAYLOAD_TYPE = "team_deadline_reminder"`,
  `DEADLINE_REMINDER_CATEGORY = "deadline_reminder"`.
- **Date math in Python** (not YQL relative dates): `today` in `timezone`, `cutoff
  = today + soon_days`, embedded as quoted ISO strings — robust against YQL
  relative-date quirks (mirrors `daily_digest._next_iso_date`).
- **YQL builder:**
  `Queue: "<q>" AND Assignee: "<login>" AND Resolution: empty() AND Deadline: notEmpty() AND Deadline: <= "<cutoff>"`.
- `DeadlineIssue` dataclass: `key, summary, deadline, status, url, bucket, assignee_login, assignee_display`.
- `load_reminder_recipients(session, team_id)` — same join as
  `load_registered_participants`, additionally returning `membership.role`.
- `fetch_member_deadline_issues(...)` — search, classify each issue into
  overdue/soon by comparing `deadline` to `today`.
- `format_member_reminder(...)` — per-assignee DM text (two sections; returns
  `None`/skips when empty).
- `format_lead_summary(...)` — consolidated per-member rollup.
- `send_team_deadline_reminders(session, *, team_id, now=None, client_factory=TrackerClient)`
  — orchestrates: resolve queue → load recipients → per-member fetch/classify →
  enqueue per-assignee DMs → aggregate → resolve lead by role → enqueue lead
  summary DM. **Dedupe key** uses a `local_hour_key` (`YYYY-MM-DDTHH`) slot ⇒
  idempotent within a run (works for both hourly and the single daily slot).
- `ensure_deadline_reminder_scheduled_job(session, team_id)` — upserts the
  `ScheduledJob` from `cfg.cron_expr`/`enabled`, updating cron + `next_run` when
  the config value changed (this *is* the in-flight cron tracking).

### 1.4 Wiring (3 small edits)

- `core/scheduler.py::_fire` — add branch:
  `elif job_type == "team_deadline_reminder": → send_team_deadline_reminders(...)`.
- `orchestrator.ensure_schema_and_seed` — add
  `await ensure_deadline_reminder_scheduled_job(session, self._team_id)`.
- `core/__init__.py` — export new public names + `DeadlineReminderConfig`.

### 1.5 Lead resolution (MVP)

The lead summary goes to the member whose `team_memberships.role ∈ lead_roles`.
Roles are not yet populated (see §2), so for the MVP the lead is **config-pinned**:
a `deadline_reminder_lead_login` fallback (default `nukolaus`) is used when no row
has a `lead` role. When roles ship, this fallback becomes redundant and the
summary auto-targets whoever holds `lead`. If no lead is DM-able, skip + log.

### 1.6 Hourly → daily switch (tracked like config)

`cfg.cron_expr` is the single source of truth; the job's cron auto-syncs on
orchestrator restart via `ensure_deadline_reminder_scheduled_job`.

| Phase | When | `DEADLINE_REMINDER_CRON` | Meaning |
|---|---|---|---|
| 1 | rollout / testing | `0 * * * *` | every hour |
| 2 | steady state | `0 13 * * *` | daily 16:00 MSK (= 13:00 UTC) |

Procedure to switch: set the env var → restart `pm-orchestrator`. The seeder
detects the change and updates the live `ScheduledJob`.

### 1.7 Tests — `tests/unit/test_deadline_reminders.py`

- YQL builder shape.
- Overdue vs soon classification at the date boundary.
- Per-member DM formatting (both sections; empty member skipped).
- Lead-summary aggregation + lead resolution by role (+ graceful skip when none).
- `send_team_deadline_reminders` enqueues **private** outbox rows; idempotent
  within a slot (run twice → no duplicates).
- `ensure_deadline_reminder_scheduled_job` upsert + cron-change update.
- Scheduler `_fire` dispatches the new `payload["type"]`.

### 1.8 Docs / config

- `.env.example` — `DEADLINE_REMINDER_*` block incl. the Phase 1/2 cron note.
- This doc — operational reference (recipients, window, switch procedure).

---

## 2. Deferred (post-MVP) — Team Roles

**Out of MVP scope.** Captured here so it is ready to pick up.

### 2.1 Problem

`team_memberships.role` is hardcoded to `"user"` at registration
(`telegram_auth.py:510`), never updated, never read. `users.role` (the field the
console RBAC actually checks) is `user` for all three real members. So the
**already-built** dev/admin capabilities are dormant.

### 2.2 The dormant RBAC that roles would activate

No new authorization code needed — only role *values*:

- `console-api`: `PATCH /agents/{name}/spec` (prompt + model) and
  `/agents/{name}/overlay` (enabled + autonomy) gated `require_roles("dev","admin")`
  (`main.py:766, 789`); scheduled-jobs endpoints dev/admin-gated (`:948–968`).
- `web-ui`: `roleLabel` `dev → "разработчик"`, `admin → "админ"` (`App.tsx:96`);
  `/dev` (debug: actions/traces) and `/admin` routes; `AgentConfigPanel` editor.

### 2.3 Intended role mapping (real prod logins)

| Person | `tracker_login` | `team_memberships.role` (label) | `users.role` (permissions — operative) | Unlocks |
|---|---|---|---|---|
| Николай Александров | `nukolaus` | `lead` | `admin` | full admin + dev |
| Roman Shinkarenko | `shinkarenkorom` | `developer` | `dev` | edit agent prompts/overlay/model, scheduled jobs, /dev debug |
| Сергей Сергей | `geroi.serg` | `member` | `user` | basic console |

`developer` is the **extended** tier (edit prompts, debug); `lead` ⇒ admin.

### 2.4 How roles get set — now → future (same columns, different writer)

- **Now (when un-deferred):** config map `{nukolaus: lead, shinkarenkorom: developer}`,
  default `member`. Two write points:
  1. Boot seeder `ensure_team_member_roles(team_id)` in `ensure_schema_and_seed` —
     idempotently writes `team_memberships.role` + mirrored `users.role`; fixes the
     3 existing prod rows.
  2. Registration (`telegram_auth.py`) — new members get their role from the map.
- **Future:** admin manages roles in the UI — add `GET /members` +
  `PATCH /members/{user_id}/role` (`require_roles("admin")`) and a "Команда" panel
  on `AdminPage`. Same columns; seeder becomes pure bootstrap.
- **Seam:** seeder only sets a role still at the default (`user`) → won't clobber a
  future UI edit. Harden later with a `role_source` (`seed`|`manual`) flag.

### 2.5 Interaction with the MVP

The deadline-reminder lead summary reads `team_memberships.role ∈ lead_roles`.
Until roles ship it uses the config-pinned fallback (§1.5); after roles ship the
fallback is redundant. No rework to the reminder code.

---

## 3. Rollout

1. Land MVP code + tests; deploy `pm-orchestrator` (prod runs as `test-*` compose
   on the VPS; Postgres `test-postgres-1`). Seeder creates the hourly job.
2. Verify: at the top of the hour, registered members with at-risk tasks receive a
   DM; `nukolaus` receives the lead summary. Check `telegram_outbox` rows +
   gateway delivery.
3. After validation, switch to Phase 2 cron (`0 13 * * *`) + restart; confirm the
   job's `cron_expr`/`next_run` updated.
4. (Later, separate change) Un-defer roles per §2.

## 4. Footprint & non-goals

- **No DB migration**; reuses `scheduled_jobs`, `telegram_outbox`, existing role
  columns.
- **No LLM calls**, **no group-chat path**.
- New files: `deadline_reminders.py`, `test_deadline_reminders.py`, this doc.
  Edits: `config.py`, `scheduler.py`, `orchestrator.py`, `__init__.py`, `.env.example`.
- **Non-goals (MVP):** role assignment/enforcement, members-management UI,
  autonomy-by-role, snoozing/ack buttons on reminders, per-member custom windows.
</content>
</invoke>
