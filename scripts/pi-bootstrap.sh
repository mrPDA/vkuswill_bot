#!/usr/bin/env bash
# Первичная подготовка Raspberry Pi (Debian/Ubuntu ARM64): Docker, Compose, git.
# Запуск на самой Pi: bash scripts/pi-bootstrap.sh
# После — клонировать репозиторий (или скопировать), настроить .env, make docker-up-pi

set -euo pipefail

log() { echo "[pi-bootstrap] $*"; }
err() { echo "[pi-bootstrap] ERROR: $*" >&2; exit 1; }

[[ "$(uname -m)" == "aarch64" ]] || log "Предупреждение: ожидался aarch64 (ARM64). Продолжаю."

if [[ "${EUID:-}" -eq 0 ]]; then
  SUDO=""
else
  command -v sudo >/dev/null || err "Нужен sudo"
  SUDO="sudo"
fi

log "Обновление пакетов..."
$SUDO apt-get update -qq
$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
  ca-certificates curl git

if command -v docker >/dev/null 2>&1; then
  log "Docker уже установлен: $(docker --version)"
else
  log "Установка Docker (get.docker.com)..."
  curl -fsSL https://get.docker.com | $SUDO sh
fi

$SUDO systemctl enable --now docker 2>/dev/null || true

TARGET_USER="${SUDO_USER:-${USER:-}}"
if [[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]]; then
  if groups "$TARGET_USER" | grep -q '\bdocker\b'; then
    log "Пользователь $TARGET_USER уже в группе docker"
  else
    log "Добавляю $TARGET_USER в группу docker (нужен перелогин или newgrp docker)"
    $SUDO usermod -aG docker "$TARGET_USER"
  fi
fi

if docker compose version >/dev/null 2>&1; then
  log "Docker Compose: $(docker compose version)"
else
  err "Плагин docker compose не найден после установки Docker"
fi

log "Готово."
echo ""
echo "Дальше (от вашего пользователя, после newgrp docker или нового SSH-сессии):"
echo "  git clone <url> vkuswill_bot && cd vkuswill_bot"
echo "  cp .env.example .env   # заполнить BOT_TOKEN, LLM_*, LANGFUSE_* при необходимости"
echo "  make docker-up-pi"
echo ""
