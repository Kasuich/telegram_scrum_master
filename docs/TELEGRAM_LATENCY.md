# Telegram latency: diagnosis and fair comparison

## Why API and Telegram feel different

| Path | What is measured |
|------|------------------|
| `POST /chat` | Agent only; response returns in HTTP body |
| Telegram group | Webhook → gateway spool → ingest (agent runs **inside** ingest) → outbox → Bot API |

Prometheus `telegram_bridge_e2e_latency_seconds{category="agent"}` measures **gateway receive → outbox queued**, including the full agent call. It does **not** include Bot API delivery back to the user.

## Fair A/B (same agent workload)

API defaults to a **new `session_id` per request**. Telegram uses a stable id:

```text
telegram:{installation_id}:{chat_id}:{thread_id_or_0}
```

A warm Telegram session has more history → often slower LLM turns. To compare fairly:

```bash
export PLATFORM_API_URL=https://main.example/chat
export TELEGRAM_SESSION_ID="telegram:<installation_uuid>:<-100chat_id>:0"
export TEST_MESSAGE="@bot ваш тестовый вопрос"
./scripts/telegram_latency_ab.sh
```

Interpretation:

- **B1 fast, B2 slow** → session history (H1), not Telegram transport
- **B1 ≈ B2 ≈ Telegram e2e** → agent time dominates; optimize agent or history
- **B2 ≈ API but Telegram UX slower** → gateway transport / time-to-first-feedback (run deliver loop in parallel; see gateway `runtime.py`)

## Metrics

| Metric | Meaning |
|--------|---------|
| `telegram_bridge_ingest_latency_seconds` | Full ingest HTTP handler |
| `telegram_bridge_pre_agent_seconds` | Routing + auth + thinking enqueue before invoke |
| `telegram_bridge_agent_invoke_seconds` | `rpc_client.invoke` only |
| `telegram_bridge_e2e_latency_seconds{category="agent"}` | Spool `received_at` → outbox queued |
| `telegram_gateway_forward_latency_seconds` | Gateway → platform-api ingest |
| `telegram_gateway_deliver_latency_seconds` | Outbox lease → Bot API send |

Grafana: `monitoring/grafana/dashboards/05_agent_debug.json`.

## Production gateway settings

- `GATEWAY_BRIDGE_TIMEOUT=120` — ingest must outlive slow agent turns (default was 10s)
- `TELEGRAM_TRANSPORT_MODE=webhook` — polling adds up to long-poll wait
- `GATEWAY_WORKER_POLL_INTERVAL=0.5` — deliver loop cadence

## Session history (if H1 confirmed)

Options (product decision):

1. Document fair-compare for benchmarks (this file)
2. `/reset` or per-thread session reset command
3. Aggressive `_compact_session_history` when `context.channel == "telegram"`

Do not change the stable `telegram:…` session id without a migration plan — it carries conversation continuity.
