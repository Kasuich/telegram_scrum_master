# Telegram Gateway Runbook

## Quick Reference

| Command | Description |
|---------|-------------|
| `docker compose -f docker-compose.telegram-gateway.yml ps` | Status |
| `docker compose -f docker-compose.telegram-gateway.yml logs -f` | Logs |
| `curl http://localhost:8080/health` | Health check |
| `curl http://localhost:8080/metrics` | Metrics |
| `docker compose -f docker-compose.telegram-gateway.yml restart` | Restart |

## Deployment

```bash
# Deploy new version
gh workflow run deploy-telegram-gateway.yml -f environment=telegram-staging -f action=deploy

# Rollback to previous image
gh workflow run deploy-telegram-gateway.yml -f environment=telegram-staging -f action=rollback
```

## Public Entrypoint (nginx + TLS)

The tunnel server (`misisdarkhorse.ru`) terminates TLS and reverse-proxies
everything. It is provisioned automatically by the `Provision nginx + TLS` step
of `deploy-telegram-gateway.yml` (script: `scripts/setup-tunnel-nginx.sh`,
idempotent, re-runs each deploy):

| Public URL | Backend |
|---|---|
| `https://misisdarkhorse.ru/telegram/webhook` | local gateway `127.0.0.1:8080/webhook` |
| `https://misisdarkhorse.ru/grafana/` | Grafana on main over WireGuard (`MAIN_WG_IP:3000`) |
| `https://misisdarkhorse.ru/` | web-ui GUI on main over WireGuard (`MAIN_WG_IP:5173`) |

Transport is **webhook** mode: the gateway registers the webhook with Telegram
on startup (`set_webhook` in `runtime.sync_transport_mode`) using
`TELEGRAM_WEBHOOK_BASE_URL` + `TELEGRAM_WEBHOOK_PATH`. Grafana is served under a
sub-path via `GF_SERVER_SERVE_FROM_SUB_PATH=true` + `GRAFANA_URL=.../grafana` on
the main server.

Certificates: Let's Encrypt via certbot webroot (`/var/www/certbot`), renewed by
certbot's systemd timer. Required deploy secrets: `TG_DOMAIN`, `CERTBOT_EMAIL`,
`MAIN_WG_IP`, `TELEGRAM_WEBHOOK_BASE_URL`.

Check it:
```bash
curl -sf https://misisdarkhorse.ru/grafana/api/health
curl -s "https://api.telegram.org/bot$TOKEN/getWebhookInfo" | python3 -m json.tool
```

## Outage Scenarios

### Telegram outage (api.telegram.org unreachable)
1. Gateway cannot deliver outbox or receive webhook ACKs
2. Spool will grow as Telegram retries webhooks
3. **No action needed** — gateway will resume delivery when Telegram recovers
4. Monitor: `telegram_gateway_spool_depth` metric
5. If outage > 24h, check whether Telegram stopped retrying older webhooks

### Main server outage
1. Gateway cannot forward inbound events — spool grows
2. Outbound messages accumulate in Postgres outbox
3. Gateway retries ingest with exponential backoff
4. **After main recovers**: spool drains automatically; outbox drained by gateway
5. Monitor: `telegram_bridge_ingest_total{status="error"}` on gateway

### Bot token compromise
1. Immediately revoke in BotFather: send `/revoke` to @BotFather
2. Generate new token
3. Update `TELEGRAM_BOT_TOKEN` secret in GitHub environment
4. Deploy gateway: `gh workflow run deploy-telegram-gateway.yml -f environment=telegram-production -f action=deploy`
5. Gateway re-registers webhook automatically on startup
6. Check logs confirm new token is active and old one rejected

### Blocked bot (user blocked the bot)
1. Gateway logs: `Forbidden: bot was blocked by the user`
2. Dead-letter count increases — this is expected behavior
3. The outbox item moves to `dead_letter`, no automatic retry
4. If user unblocks: replay dead-lettered items (see Dead-Letter Replay below)

## Common Issues

### Gateway heartbeat missing > 2 min
1. Check `docker compose logs telegram-gateway`
2. Check network connectivity to main server
3. Check HMAC keys match on both servers
4. Restart: `docker compose -f docker-compose.telegram-gateway.yml restart telegram-gateway`

### Outbox oldest age > 5 min
1. Check main server is reachable from gateway
2. Check `TELEGRAM_BOT_TOKEN` is valid
3. Check rate limits: look for 429 responses in logs
4. Check dead-letter count: `curl -s http://localhost:8080/metrics | grep telegram_outbox`

### Webhook 4xx/5xx spikes
1. Check Telegram API status (https://downdetector.com/status/telegram/)
2. Check webhook URL is accessible from Telegram
3. Check webhook secret matches BotFather config
4. Check HMAC keys are correctly set on main server

### Gateway spool > 80% full
1. Check main server connectivity — spool drains when main is reachable
2. If main is reachable but spool growing: check ingest errors in logs
3. Emergency clear (data loss — only if spool is corrupt):
   ```bash
   docker compose -f docker-compose.telegram-gateway.yml down
   rm /var/lib/telegram-gateway/spool.db
   docker compose -f docker-compose.telegram-gateway.yml up -d
   ```
   Updates will be re-delivered from Telegram (webhook has 24h retry window).

### HMAC/replay failures spike
1. Clock skew between servers: ensure NTP sync (`timedatectl status`)
2. Key rotation in progress: verify both key IDs are present in `TELEGRAM_BRIDGE_HMAC_KEYS`
3. After rotation complete, remove old key to stop false positives

### Stuck lease (outbox oldest age growing)
1. Gateway may have crashed mid-delivery
2. Leases expire after `TELEGRAM_OUTBOX_LEASE_SECONDS` (default 60s)
3. Wait for lease expiry — items return to `pending` automatically
4. If recurring: check gateway logs for crash reason and fix

### Full disk
1. Check spool size: `du -sh /var/lib/telegram-gateway`
2. Check delivery journal: `du -sh /var/lib/telegram-gateway/delivery.db`
3. If spool huge: main server is unreachable — fix connectivity first
4. Spool entries are deleted after successful ingest to main

### Webhook registration
Gateway auto-registers webhook on startup via Telegram API. If webhook URL changed:
```bash
docker compose -f docker-compose.telegram-gateway.yml restart telegram-gateway
```

## Dead-Letter Replay

Dead-lettered outbox items (permanent failures, blocked users, invalid token) can be
replayed after the underlying issue is resolved.

```bash
# Replay all dead-letter items (up to 50 at a time)
HMAC_KEY="<bridge_hmac_key>"
TIMESTAMP=$(date +%s)
NONCE=$(uuidgen | tr -d '-')
BODY='{"limit": 50}'
BODY_SHA=$(echo -n "$BODY" | sha256sum | awk '{print $1}')
SIGNED="${METHOD}${PATH}${TIMESTAMP}${NONCE}${BODY_SHA}"
SIG=$(echo -n "$SIGNED" | openssl dgst -sha256 -hmac "$HMAC_KEY" | awk '{print $2}')

curl -X POST https://<main-server>/internal/telegram/v1/outbox:replay-dead-letter \
  -H "Content-Type: application/json" \
  -H "X-Bridge-Timestamp: $TIMESTAMP" \
  -H "X-Bridge-Nonce: $NONCE" \
  -H "X-Bridge-Signature: $SIG" \
  -H "X-Bridge-Key-Id: key1" \
  -d "$BODY"

# Replay only for a specific team
curl -X POST ... -d '{"team_id": "<uuid>", "limit": 100}'
```

After replay, items return to `pending` and gateway picks them up on the next lease.

## Key Rotation

### HMAC Key Rotation (zero-downtime)

1. Generate new key pair:
   ```bash
   openssl rand -hex 32  # new secret
   ```
2. Add new key to `TELEGRAM_BRIDGE_HMAC_KEYS` on main server: `key1:oldsecret,key2:newsecret`
3. Update gateway env `TELEGRAM_BRIDGE_HMAC_KEY_ID=key2` and add key2 secret
4. Deploy main server first, then gateway
5. Verify: `telegram_bridge_ingest_total{status="ok"}` continues without errors
6. After 5+ minutes: remove `key1` from both servers' config
7. Deploy again

### Bot Token Rotation

1. Get new token from @BotFather
2. Update `TELEGRAM_BOT_TOKEN` secret in GitHub environment
3. Deploy — gateway will re-register with new token on startup
4. Old token becomes invalid immediately

## Backup and Restore

### Main Database (Postgres)
Standard pg_dump/pg_restore. Telegram tables are included in the main backup.
```bash
pg_dump -Fc -d $DATABASE_URL -f backup_$(date +%Y%m%d).pgc
pg_restore -d $DATABASE_URL backup_$(date +%Y%m%d).pgc
```

### Gateway Spool (SQLite WAL)
Spool is ephemeral — lost items are re-delivered by Telegram within 24h.
For forensics, copy before clearing:
```bash
docker cp telegram-gateway:/var/lib/telegram-gateway/spool.db ./spool_backup.db
```
Restore is not needed — restart gateway and let Telegram retry.

## Secrets Reference

| Secret | Where | Description |
|--------|-------|-------------|
| `TELEGRAM_BOT_TOKEN` | Gateway server only | Bot token from BotFather |
| `TELEGRAM_WEBHOOK_SECRET` | Gateway server only | Random string for webhook auth |
| `TELEGRAM_BRIDGE_HMAC_KEYS` | Both servers | `key_id:secret` comma-separated pairs |
| `TELEGRAM_BRIDGE_HMAC_KEY_ID` | Gateway server | Active key ID for signing requests |
| `TG_VPS_HOST` | GitHub environment | Gateway server hostname |
| `TG_VPS_SSH_KEY` | GitHub environment | SSH key for deployment |

## Alert Response Matrix

| Alert | Severity | First Action |
|-------|----------|-------------|
| Gateway heartbeat missing > 2m | P1 | Check gateway process, restart |
| Inbound spool oldest > 5m | P2 | Check main server connectivity |
| Outbox oldest > 5m | P2 | Check gateway connectivity to Telegram |
| Dead-letter growing | P3 | Check bot status, replay after fix |
| Webhook 5xx spike | P2 | Check Telegram API status |
| HMAC failures spike | P1 | Check key rotation, clock sync |
| Disk > 80% | P2 | Check spool size, fix connectivity |
