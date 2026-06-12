#!/usr/bin/env bash
# Provision host nginx + Let's Encrypt TLS on the Telegram tunnel server.
#
# This server is the public entrypoint for misisdarkhorse.ru. It terminates TLS
# and reverse-proxies:
#   /telegram/webhook  -> local telegram-gateway container (127.0.0.1:8080/webhook)
#   /grafana/          -> Grafana on the main server over WireGuard
#   /  (everything)    -> web-ui (GUI) on the main server over WireGuard
#
# The main server is reachable only via the WireGuard tunnel (MAIN_WG_IP).
#
# Idempotent: safe to re-run on every deploy. Certificates are only re-issued
# when close to expiry (--keep-until-expiring).
#
# Required env:
#   DOMAIN          public domain (e.g. misisdarkhorse.ru)
#   CERTBOT_EMAIL   contact email for Let's Encrypt expiry notices
# Optional env (with defaults):
#   MAIN_WG_IP      WireGuard IP of the main server         (default 10.99.0.1)
#   GATEWAY_ADDR    local gateway address:port              (default 127.0.0.1:8080)
#   GRAFANA_PORT    Grafana port on the main server         (default 3000)
#   WEBUI_PORT      web-ui port on the main server          (default 5173)
set -euo pipefail

DOMAIN="${DOMAIN:?DOMAIN is required}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:?CERTBOT_EMAIL is required}"
MAIN_WG_IP="${MAIN_WG_IP:-10.99.0.1}"
GATEWAY_ADDR="${GATEWAY_ADDR:-127.0.0.1:8080}"
GRAFANA_PORT="${GRAFANA_PORT:-3000}"
WEBUI_PORT="${WEBUI_PORT:-5173}"

WEBROOT="/var/www/certbot"
SITE_AVAILABLE="/etc/nginx/sites-available/${DOMAIN}.conf"
SITE_ENABLED="/etc/nginx/sites-enabled/${DOMAIN}.conf"
LIVE_DIR="/etc/letsencrypt/live/${DOMAIN}"

# Run privileged commands with sudo only when not already root.
SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

log() { echo "[setup-tunnel-nginx] $*"; }

# ── 1. Packages ──────────────────────────────────────────────────────────────
if ! command -v nginx >/dev/null 2>&1 || ! command -v certbot >/dev/null 2>&1; then
  log "Installing nginx + certbot..."
  # Use `env` for the assignment: `$SUDO VAR=val cmd` breaks when $SUDO is empty
  # (bash treats VAR=val as the command name after the empty expansion).
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -y
  $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y nginx certbot
else
  log "nginx + certbot already installed"
fi

$SUDO mkdir -p "${WEBROOT}/.well-known/acme-challenge"

# ── 2. Bootstrap HTTP-only site so certbot webroot challenge can pass even on a
#       first run (the full HTTPS config references certs that don't exist yet) ─
write_bootstrap() {
  $SUDO tee "$SITE_AVAILABLE" >/dev/null <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location ^~ /.well-known/acme-challenge/ {
        root ${WEBROOT};
        default_type "text/plain";
    }
    location / {
        return 503;
    }
}
EOF
}

# ── 3. Full config: TLS + reverse proxy ──────────────────────────────────────
write_full() {
  # WebSocket upgrade map lives in http context (conf.d is included there).
  $SUDO tee /etc/nginx/conf.d/websocket_upgrade.conf >/dev/null <<'EOF'
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}
EOF

  $SUDO tee "$SITE_AVAILABLE" >/dev/null <<EOF
# HTTP: ACME challenge + redirect everything else to HTTPS.
server {
    listen 80;
    listen [::]:80;
    server_name ${DOMAIN};

    location ^~ /.well-known/acme-challenge/ {
        root ${WEBROOT};
        default_type "text/plain";
    }
    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ${DOMAIN};

    ssl_certificate     ${LIVE_DIR}/fullchain.pem;
    ssl_certificate_key ${LIVE_DIR}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;

    client_max_body_size 65M;

    # Telegram webhook -> local gateway container. The app serves /webhook;
    # the public path /telegram/webhook is mapped onto it here.
    location = /telegram/webhook {
        proxy_pass http://${GATEWAY_ADDR}/webhook;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        # Pass Telegram's secret header through untouched.
        proxy_set_header X-Telegram-Bot-Api-Secret-Token \$http_x_telegram_bot_api_secret_token;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
    }

    # Grafana on the main server (over WireGuard). Served under /grafana via
    # GF_SERVER_SERVE_FROM_SUB_PATH=true + GF_SERVER_ROOT_URL=.../grafana.
    location /grafana/ {
        proxy_pass http://${MAIN_WG_IP}:${GRAFANA_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_read_timeout 300s;
    }
    location = /grafana {
        return 301 https://\$host/grafana/;
    }

    # GUI (web-ui) on the main server (over WireGuard). The web-ui container
    # serves the SPA and proxies its own /api/ to console-api internally.
    location / {
        proxy_pass http://${MAIN_WG_IP}:${WEBUI_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
EOF
}

reload_nginx() {
  $SUDO ln -sf "$SITE_AVAILABLE" "$SITE_ENABLED"
  $SUDO rm -f /etc/nginx/sites-enabled/default
  $SUDO nginx -t
  if $SUDO systemctl is-active --quiet nginx; then
    $SUDO systemctl reload nginx
  else
    $SUDO systemctl enable --now nginx
  fi
}

# ── 4. Provision ─────────────────────────────────────────────────────────────
if [ -d "$LIVE_DIR" ]; then
  log "Certificate already present — writing full config"
  write_full
  reload_nginx
else
  log "No certificate yet — bootstrapping HTTP for ACME challenge"
  write_bootstrap
  reload_nginx
fi

log "Requesting/renewing certificate for ${DOMAIN}..."
$SUDO certbot certonly --webroot -w "$WEBROOT" -d "$DOMAIN" \
  --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --keep-until-expiring || {
    log "ERROR: certbot failed. Is ${DOMAIN} pointing here and is port 80 open?"
    exit 1
  }

if [ ! -d "$LIVE_DIR" ]; then
  log "ERROR: certificate directory ${LIVE_DIR} missing after certbot"
  exit 1
fi

log "Writing full HTTPS config and reloading"
write_full
reload_nginx

log "Done. https://${DOMAIN}/ (GUI), /grafana/ (Grafana), /telegram/webhook (bot)"
