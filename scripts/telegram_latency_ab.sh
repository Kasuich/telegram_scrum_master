#!/usr/bin/env bash
# A/B latency: API (fresh session) vs API (telegram session_id) vs Prometheus phases.
#
# Usage:
#   export PLATFORM_API_URL=https://your-main-server/chat
#   export TELEGRAM_SESSION_ID="telegram:<installation_uuid>:<chat_id>:0"
#   export TEST_MESSAGE="@bot сколько задач в очереди?"
#   ./scripts/telegram_latency_ab.sh
#
# For Telegram wall-clock: send TEST_MESSAGE in the group and note seconds until reply.
# Compare with B1/B2 below.

set -euo pipefail

PLATFORM_API_URL="${PLATFORM_API_URL:-http://localhost:8000/chat}"
TEST_MESSAGE="${TEST_MESSAGE:-сколько задач в очереди?}"
TELEGRAM_SESSION_ID="${TELEGRAM_SESSION_ID:-}"
PLATFORM_METRICS_URL="${PLATFORM_METRICS_URL:-http://localhost:8000/metrics}"
GATEWAY_METRICS_URL="${GATEWAY_METRICS_URL:-http://localhost:8080/metrics}"
ORCHESTRATOR_METRICS_URL="${ORCHESTRATOR_METRICS_URL:-http://localhost:8001/metrics}"
OUT="${OUT:-scripts/telegram_latency_snapshot.txt}"

json_payload() {
  local session_id="$1"
  python3 -c "
import json, sys
print(json.dumps({'message': sys.argv[1], 'session_id': sys.argv[2]}))
" "$TEST_MESSAGE" "$session_id"
}

run_curl() {
  local label="$1"
  local session_id="$2"
  echo "## $label"
  echo "session_id=$session_id"
  curl -sS -o /tmp/latency_ab_body.json -w "HTTP_TIME:%{time_total}\n" \
    -X POST "$PLATFORM_API_URL" \
    -H "Content-Type: application/json" \
    -d "$(json_payload "$session_id")"
  echo "reply_preview: $(python3 -c "import json; d=json.load(open('/tmp/latency_ab_body.json')); print((d.get('reply') or '')[:120])" 2>/dev/null || echo '(parse error)')"
  echo
}

fresh_session() {
  python3 -c "import uuid; print(uuid.uuid4())"
}

{
  echo "# Telegram vs API Latency A/B"
  echo "# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo
  echo "TEST_MESSAGE=$TEST_MESSAGE"
  echo
  run_curl "B1 API fresh session" "$(fresh_session)"
  if [[ -n "$TELEGRAM_SESSION_ID" ]]; then
    run_curl "B2 API telegram session_id" "$TELEGRAM_SESSION_ID"
  else
    echo "## B2 API telegram session_id"
    echo "SKIP: set TELEGRAM_SESSION_ID=telegram:<inst>:<chat_id>:0"
    echo
  fi
  echo "## Manual A — Telegram group"
  echo "Send the same TEST_MESSAGE with @bot in the group; record wall-clock to final reply."
  echo
  echo "## platform-api /metrics (phase breakdown)"
  curl -sS "$PLATFORM_METRICS_URL" 2>/dev/null | grep -E \
    'telegram_bridge_(ingest|e2e|agent_invoke|pre_agent)_latency_seconds|telegram_outbox_pending' \
    || echo "(platform-api metrics unreachable)"
  echo
  echo "## telegram-gateway /metrics"
  curl -sS "$GATEWAY_METRICS_URL" 2>/dev/null | grep -E \
    'telegram_gateway_(forward|deliver)_latency_seconds|telegram_gateway_(forward|deliver)_total' \
    || echo "(gateway metrics unreachable)"
  echo
  echo "## pm-orchestrator /metrics (LLM)"
  curl -sS "$ORCHESTRATOR_METRICS_URL" 2>/dev/null | grep -E 'pm_llm_latency_seconds_' \
    | head -20 || echo "(orchestrator metrics unreachable)"
} | tee "$OUT"

echo "Wrote $OUT"
