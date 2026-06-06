# Telegram Track Acceptance Checklist

Automated + manual acceptance criteria for Track A (Telegram-контур).

## Automated Tests

Run all tests:
```bash
cd services/platform-api && uv run pytest tests/ -q
cd services/telegram-gateway && uv run pytest tests/ -q
cd packages/core && uv run pytest tests/unit/test_telegram_message_repository.py -q
```

**Expected: 122+ tests passing**

| Suite | Expected |
|-------|----------|
| platform-api | 85+ |
| telegram-gateway | 22+ |
| core | 15+ |

---

## Manual Acceptance Criteria

Mark ✅ when verified on staging with real Telegram bot.

### 1. Group Message Storage
- [ ] Bot added to group with privacy mode disabled
- [ ] Message in group → appears in `telegram_messages` with author, thread, reply context
- [ ] Regular message in group does NOT call LLM

### 2. Group Mention → Agent Response
- [ ] User mentions bot: `@botname hello`
- [ ] PM Orchestrator receives invoke with correct context
- [ ] Bot responds in the same topic/thread

### 3. DM Dialog Persistence
- [ ] Send message to bot → DM thread established
- [ ] Restart both services
- [ ] Send another message → same session continues

### 4. Confirm Flow
- [ ] Agent asks for confirmation
- [ ] Inline buttons shown to user
- [ ] User taps "Approve" → action executes once
- [ ] User taps "Reject" → action does NOT execute
- [ ] Different user taps button → rejected

### 5. Scheduler Notifications
- [ ] Reminder scheduled via agent
- [ ] Delivered at correct time
- [ ] Respects user's quiet hours if set

### 6. Alertmanager → Outbox
- [ ] Prometheus alert fires
- [ ] Alert delivered via outbox (NOT direct Telegram call from main server)

### 7. Gateway Spool on Main Outage
- [ ] Send messages while main server is down
- [ ] Gateway continues accepting webhooks
- [ ] Spool grows (verify: `telegram_gateway_spool_depth` metric)
- [ ] After main recovers, spool drains without duplicates

### 8. Outbox Persistence on Gateway Outage
- [ ] Agent generates response while gateway is down
- [ ] Message in `pending` state in outbox
- [ ] After gateway recovers, message delivered (no business action repeated)

### 9. Webhook Idempotency
- [ ] Telegram retries webhook delivery
- [ ] Same message NOT duplicated in database

### 10. Correspondence Query API
- [ ] Call `GET /internal/telegram/v1/messages?team_id=...&limit=50`
- [ ] Returns paginated messages with cursor
- [ ] Cursor pagination works across edits

### 11. Import Dedupe
- [ ] Import Telegram Desktop export JSON
- [ ] Import same export again
- [ ] No duplicate messages created

### 12. Bot Token Isolation
- [ ] `TELEGRAM_BOT_TOKEN` exists ONLY on gateway server
- [ ] `grep -r TELEGRAM_BOT_TOKEN` returns nothing on main server
- [ ] Token not in main server logs

### 13. Unbound Chat Rejection
- [ ] Message from unknown chat → HTTP 404 on ingest
- [ ] No message stored, no agent called

### 14. Secretary Mode — Selected Chat Scope
- [ ] User connects via `business_connection:connect`
- [ ] Only selected chats appear in messages
- [ ] Unselected chat messages NOT processed

### 15. Secretary Mode — Revoke Revokes Access
- [ ] User revokes connection via `business_connection:revoke`
- [ ] Pending deliveries cancelled
- [ ] New messages from connection rejected

### 16. HMAC Security
- [ ] Request without valid HMAC signature → HTTP 401
- [ ] Replay of old request (same nonce) → rejected
- [ ] Request with wrong key → rejected

### 17. Deployment — Two Servers
- [ ] Gateway on separate server with public IP
- [ ] Main server has no access to api.telegram.org
- [ ] WireGuard tunnel established between servers
- [ ] Gateway heartbeat visible on main server

### 18. Rollback
- [ ] Deploy new version
- [ ] Trigger rollback via workflow
- [ ] Previous version running within 2 minutes

---

## Performance Criteria

| Metric | Target |
|--------|--------|
| Webhook ACK p95 | < 300ms |
| Agent reply p95 (no LLM) | < 2s |
| Transport overhead | < 2s |
| Outbox oldest age (normal) | < 60s |
| No loss with 10k duplicate webhooks | ✅ |

---

## Security Checklist

- [ ] Bot token only on gateway server
- [ ] HMAC keys rotated with overlap
- [ ] No secrets in GitHub Actions logs
- [ ] No secrets in docker-compose files
- [ ] Webhook URL not guessable
- [ ] Callback tokens are opaque (no internal IDs)
- [ ] Unbound chats rejected at ingest
- [ ] LLM prompt does not contain raw metadata
