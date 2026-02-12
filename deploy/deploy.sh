#!/usr/bin/env bash
# ============================================================
# Deploy script для VkusVill Bot на Yandex Cloud VM
# Вызывается из GitHub Actions CD pipeline
# ============================================================
# Использование:
#   bash deploy.sh --image cr.yandex/xxx/vkuswill-bot:v1.0.0 \
#                  --tag v1.0.0 \
#                  --lockbox e6qXXX
# ============================================================

set -euo pipefail

# ─── Цвета ───────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ─── Параметры ───────────────────────────────────────────────
IMAGE=""
TAG=""
LOCKBOX_SECRET_ID=""
GIGACHAT_MODEL_OVERRIDE=""
CONTAINER_NAME="vkuswill-bot"
HEALTH_PORT=8080
HEALTH_RETRIES=10
HEALTH_DELAY=5

while [[ $# -gt 0 ]]; do
  case $1 in
    --image)   IMAGE="$2"; shift 2 ;;
    --tag)     TAG="$2"; shift 2 ;;
    --lockbox) LOCKBOX_SECRET_ID="$2"; shift 2 ;;
    --model)   GIGACHAT_MODEL_OVERRIDE="$2"; shift 2 ;;
    *) echo -e "${RED}Неизвестный параметр: $1${NC}"; exit 1 ;;
  esac
done

if [[ -z "$IMAGE" || -z "$TAG" ]]; then
  echo -e "${RED}Ошибка: --image и --tag обязательны${NC}"
  exit 1
fi

log() { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[deploy]${NC} $*"; }
err() { echo -e "${RED}[deploy]${NC} $*"; }

# ─── 0. Установка yc CLI (если отсутствует) ────────────────────
ensure_yc_cli() {
  # Проверить стандартные пути
  for p in /usr/local/bin/yc /home/deploy/yandex-cloud/bin/yc "$HOME/yandex-cloud/bin/yc"; do
    if [[ -x "$p" ]]; then
      export PATH="$(dirname "$p"):$PATH"
      return
    fi
  done

  if command -v yc &>/dev/null; then
    return
  fi

  log "Установка Yandex Cloud CLI..."
  local YC_HOME="$HOME/yandex-cloud"
  curl -sSL https://storage.yandexcloud.net/yandexcloud-yc/install.sh | \
    bash -s -- -i "$YC_HOME" -n 2>&1 || true

  export PATH="${YC_HOME}/bin:$PATH"

  if command -v yc &>/dev/null; then
    log "yc CLI установлен: $(yc version 2>/dev/null || echo 'OK')"
  else
    warn "Не удалось установить yc CLI, продолжаем без Lockbox"
    return
  fi

  # Настроить использование instance service account (привязан к VM)
  if ! yc config get instance-service-account 2>/dev/null | grep -q true; then
    log "Настройка yc CLI: instance-service-account"
    yc config set instance-service-account true 2>/dev/null || true
    # folder-id из метаданных Yandex Cloud VM
    local FOLDER_ID
    FOLDER_ID=$(curl -sf -H 'Metadata-Flavor: Google' http://169.254.169.254/computeMetadata/v1/yandex/folder-id 2>/dev/null) || true
    if [[ -n "$FOLDER_ID" ]]; then
      yc config set folder-id "$FOLDER_ID" 2>/dev/null || true
      log "yc CLI folder-id: ${FOLDER_ID}"
    fi
  fi
}

ensure_yc_cli

# ─── 1. Авторизация в Container Registry ────────────────────
# Docker login выполняется в CD pipeline (json_key).
# НЕ вызываем yc container registry configure-docker —
# он перезаписывает Docker credential helper, требуя профиль yc.
log "Docker auth: используется существующий docker login"

# ─── 2. Загрузка секретов из Lockbox ────────────────────────
load_lockbox_secrets() {
  if [[ -z "$LOCKBOX_SECRET_ID" ]]; then
    warn "LOCKBOX_SECRET_ID не указан, пропускаем загрузку секретов"
    return
  fi

  # Проверяем наличие yc CLI
  if ! command -v yc &>/dev/null; then
    warn "yc CLI не найден на VM, пропускаем загрузку секретов из Lockbox"
    return
  fi

  log "Загрузка секретов из Lockbox: ${LOCKBOX_SECRET_ID}..."

  local ENV_FILE="/opt/vkuswill-bot/.env"
  local LOCKBOX_JSON

  # Получаем все payload entries
  if ! LOCKBOX_JSON=$(yc lockbox payload get --id "$LOCKBOX_SECRET_ID" --format json 2>&1); then
    warn "Не удалось получить секреты из Lockbox: ${LOCKBOX_JSON}"
    warn "Проверьте, что yc CLI настроен и сервисный аккаунт VM имеет доступ к Lockbox"
    return
  fi

  # Проверяем, что получили непустой JSON
  if [[ -z "$LOCKBOX_JSON" ]] || ! echo "$LOCKBOX_JSON" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
    warn "Lockbox вернул невалидный JSON, пропускаем загрузку секретов"
    return
  fi

  # Lockbox — основной источник секретов, перезаписывает .env
  echo "$LOCKBOX_JSON" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for entry in data.get('entries', []):
    key = entry['key']
    value = entry.get('text_value', '')
    print(f'{key}={value}')
" > "$ENV_FILE"

  chmod 600 "$ENV_FILE"
  log "Секреты загружены из Lockbox ($(grep -c '=' "$ENV_FILE" || echo 0) записей)"
}

# ─── 3. Pull нового образа ──────────────────────────────────
log "Pulling image: ${IMAGE}..."
docker pull "$IMAGE"

# ─── 4. Остановка старого контейнера ────────────────────────
if docker ps -q -f "name=${CONTAINER_NAME}" | grep -q .; then
  log "Остановка текущего контейнера ${CONTAINER_NAME}..."
  docker stop "$CONTAINER_NAME" --time 30 2>/dev/null || true
  docker rm "$CONTAINER_NAME" 2>/dev/null || true
  log "Старый контейнер остановлен"
else
  warn "Контейнер ${CONTAINER_NAME} не запущен"
fi

# ─── 5. Загрузка секретов ────────────────────────────────────
load_lockbox_secrets

# ─── 5b. Запуск Langfuse (self-hosted, если настроен) ────────
deploy_langfuse() {
  local LANGFUSE_NAME="vkuswill-langfuse"
  local ENV_FILE="/opt/vkuswill-bot/.env"

  # Проверяем, включён ли Langfuse
  if [[ ! -f "$ENV_FILE" ]] || ! grep -q '^LANGFUSE_ENABLED=true' "$ENV_FILE"; then
    log "Langfuse не включён (LANGFUSE_ENABLED!=true), пропускаем"
    return
  fi

  # Извлекаем параметры из .env
  local LF_DB_URL LF_AUTH_SECRET LF_SALT
  LF_DB_URL=$(grep '^LANGFUSE_DATABASE_URL=' "$ENV_FILE" | cut -d'=' -f2- || echo "")
  LF_AUTH_SECRET=$(grep '^LANGFUSE_NEXTAUTH_SECRET=' "$ENV_FILE" | cut -d'=' -f2- || echo "")
  LF_SALT=$(grep '^LANGFUSE_SALT=' "$ENV_FILE" | cut -d'=' -f2- || echo "")

  if [[ -z "$LF_DB_URL" ]]; then
    warn "LANGFUSE_DATABASE_URL не задан, Langfuse пропущен"
    return
  fi

  log "Обновление Langfuse..."
  docker pull langfuse/langfuse:2 2>/dev/null || true

  # Остановить предыдущий контейнер
  if docker ps -q -f "name=${LANGFUSE_NAME}" | grep -q .; then
    docker stop "$LANGFUSE_NAME" --time 10 2>/dev/null || true
    docker rm "$LANGFUSE_NAME" 2>/dev/null || true
  fi

  docker run -d \
    --name "$LANGFUSE_NAME" \
    --restart unless-stopped \
    --network host \
    -e "DATABASE_URL=${LF_DB_URL}" \
    -e "NEXTAUTH_URL=http://localhost:3000" \
    -e "NEXTAUTH_SECRET=${LF_AUTH_SECRET}" \
    -e "SALT=${LF_SALT}" \
    -e "TELEMETRY_ENABLED=false" \
    -e "HOSTNAME=0.0.0.0" \
    -e "PORT=3000" \
    --log-driver json-file \
    --log-opt max-size=20m \
    --log-opt max-file=2 \
    --label "service=langfuse" \
    langfuse/langfuse:2

  # Подождать и проверить, что контейнер жив
  sleep 5
  if docker ps -q -f "name=${LANGFUSE_NAME}" | grep -q .; then
    log "Langfuse запущен на порту 3000"
  else
    warn "Langfuse контейнер упал! Логи:"
    docker logs "$LANGFUSE_NAME" --tail 30 2>&1 || true
  fi
}

deploy_langfuse

# ─── 6. Запуск нового контейнера ────────────────────────────
log "Запуск контейнера ${CONTAINER_NAME} (${TAG})..."

ENV_FILE="/opt/vkuswill-bot/.env"
ENV_FLAG=""
if [[ -f "$ENV_FILE" ]]; then
  ENV_FLAG="--env-file ${ENV_FILE}"
fi

# Проверить, что WEBHOOK_HOST задан в .env (необходим для регистрации webhook в Telegram)
if [[ -f "$ENV_FILE" ]]; then
  if ! grep -q '^WEBHOOK_HOST=.\+' "$ENV_FILE"; then
    warn "WEBHOOK_HOST не задан в ${ENV_FILE}. Укажите внешний домен/IP для webhook (например, bot.example.com)"
    warn "Контейнер будет запущен, но webhook может не работать"
  fi
else
  warn "Файл ${ENV_FILE} не найден. Контейнер будет запущен без переменных окружения из файла."
  warn "Убедитесь, что .env файл создан вручную или секреты доступны через Lockbox."
fi

# Директория для persistent-данных (SQLite preferences)
DATA_DIR="/opt/vkuswill-bot/data"
mkdir -p "$DATA_DIR"
# botuser в контейнере — непривилегированный; даём права на запись
chmod a+rwx "$DATA_DIR"
log "DATA_DIR=${DATA_DIR} — права: $(ls -ld "$DATA_DIR")"

# SSL-сертификат для самоподписанного webhook
SSL_DIR="/opt/vkuswill-bot/ssl"
SSL_MOUNT=""
SSL_ENV=""
if [[ -f "${SSL_DIR}/cert.pem" ]]; then
  SSL_MOUNT="-v ${SSL_DIR}/cert.pem:/app/ssl/cert.pem:ro"
  SSL_ENV="-e WEBHOOK_CERT_PATH=/app/ssl/cert.pem"
  log "Найден SSL-сертификат: ${SSL_DIR}/cert.pem"
else
  warn "SSL-сертификат не найден в ${SSL_DIR}/cert.pem — webhook без сертификата"
fi

# Переопределение модели: --model имеет приоритет над Lockbox/.env
MODEL_ENV=""
if [[ -n "$GIGACHAT_MODEL_OVERRIDE" ]]; then
  MODEL_ENV="-e GIGACHAT_MODEL=${GIGACHAT_MODEL_OVERRIDE}"
  log "Модель GigaChat: ${GIGACHAT_MODEL_OVERRIDE} (override)"
fi

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --network host \
  $ENV_FLAG \
  $MODEL_ENV \
  -v "${DATA_DIR}:/app/data" \
  $SSL_MOUNT \
  $SSL_ENV \
  -e "USE_WEBHOOK=true" \
  -e "WEBHOOK_PORT=${HEALTH_PORT}" \
  --health-cmd "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:${HEALTH_PORT}/health')\" 2>/dev/null || exit 1" \
  --health-interval=30s \
  --health-timeout=10s \
  --health-start-period=15s \
  --health-retries=3 \
  --log-driver json-file \
  --log-opt max-size=50m \
  --log-opt max-file=3 \
  --label "version=${TAG}" \
  --label "deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$IMAGE"

# ─── 7. Health check ────────────────────────────────────────
log "Проверка health (${HEALTH_RETRIES} попыток, интервал ${HEALTH_DELAY}s)..."

for i in $(seq 1 $HEALTH_RETRIES); do
  sleep "$HEALTH_DELAY"
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:${HEALTH_PORT}/health" 2>/dev/null || echo "000")
  if [[ "$STATUS" == "200" ]]; then
    log "Health check OK (попытка ${i}/${HEALTH_RETRIES})"
    break
  fi
  warn "Попытка ${i}/${HEALTH_RETRIES}: status=${STATUS}"
done

if [[ "$STATUS" != "200" ]]; then
  err "Health check FAILED после ${HEALTH_RETRIES} попыток!"
  err "Логи контейнера:"
  docker logs --tail 50 "$CONTAINER_NAME" 2>&1
  exit 1
fi

# ─── 8. Очистка старых образов ──────────────────────────────
log "Очистка неиспользуемых Docker-образов..."
docker image prune -f --filter "until=168h" 2>/dev/null || true

# ─── 9. Итог ─────────────────────────────────────────────────
log "════════════════════════════════════════"
log "Деплой ${TAG} завершён успешно!"
log "Image:     ${IMAGE}"
log "Container: ${CONTAINER_NAME}"
log "Health:    http://localhost:${HEALTH_PORT}/health"
log "════════════════════════════════════════"

# Показать статус
docker ps --filter "name=${CONTAINER_NAME}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
