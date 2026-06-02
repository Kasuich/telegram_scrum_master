#!/usr/bin/env bash
# Запускать от root на свежем Ubuntu 22.04 / 24.04
# Использование: bash vps-setup.sh <github_deploy_public_key>
# Пример: bash vps-setup.sh "ssh-ed25519 AAAAC3... github-actions"

set -euo pipefail

DEPLOY_USER="deploy"
APP_DIR="/opt/pm-agent"
REPO_URL="https://github.com/Artem216/digital_breakthrough_2026.git"
GITHUB_PUBKEY="${1:-}"

if [[ -z "$GITHUB_PUBKEY" ]]; then
  echo "Usage: $0 <github_deploy_public_key>"
  exit 1
fi

echo ">>> Installing Docker..."
apt-get update -q
apt-get install -y -q ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin

echo ">>> Creating user '${DEPLOY_USER}'..."
id -u "$DEPLOY_USER" &>/dev/null || useradd -m -s /bin/bash "$DEPLOY_USER"
usermod -aG docker "$DEPLOY_USER"

echo ">>> Setting up SSH key for GitHub Actions..."
SSH_DIR="/home/${DEPLOY_USER}/.ssh"
mkdir -p "$SSH_DIR"
echo "$GITHUB_PUBKEY" >> "${SSH_DIR}/authorized_keys"
chmod 700 "$SSH_DIR"
chmod 600 "${SSH_DIR}/authorized_keys"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "$SSH_DIR"

echo ">>> Creating app directories..."
mkdir -p "${APP_DIR}/test"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "$APP_DIR"

echo ">>> Cloning repository (test)..."
sudo -u "$DEPLOY_USER" git clone -b develop "$REPO_URL" "${APP_DIR}/test" 2>/dev/null || \
  echo "  (репо пустое или ветка develop ещё не создана — склонируй вручную позже)"

echo ""
echo "=== Done ==="
echo "  App dir : ${APP_DIR}/test"
echo "  SSH user: ${DEPLOY_USER}"
echo ""
echo "Добавь в GitHub Secrets:"
echo "  VPS_HOST     — IP или hostname этого сервера"
echo "  VPS_USER     — ${DEPLOY_USER}"
echo "  VPS_SSH_KEY  — приватный ключ, парный к публичному выше"
echo "  TEST_DB_PASSWORD, YC_API_KEY, YC_FOLDER_ID"
