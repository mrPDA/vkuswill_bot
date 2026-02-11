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
CONTAINER_NAME="vkuswill-bot"
HEALTH_PORT=8080
HEALTH_RETRIES=10
HEALTH_DELAY=5

while [[ $# -gt 0 ]]; do
  case $1 in
    --image)   IMAGE="$2"; shift 2 ;;
    --tag)     TAG="$2"; shift 2 ;;
    --lockbox) LOCKBOX_SECRET_ID="$2"; shift 2 ;;
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

# ─── 1. Авторизация в Container Registry ────────────────────
log "Авторизация в Yandex Container Registry..."
yc container registry configure-docker 2>/dev/null || true

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
  if ! LOCKBOX_JSON=$(yc lockbox payload get "$LOCKBOX_SECRET_ID" --format json 2>&1); then
    warn "Не удалось получить секреты из Lockbox: ${LOCKBOX_JSON}"
    warn "Проверьте, что yc CLI настроен и сервисный аккаунт VM имеет доступ к Lockbox"
    return
  fi

  # Проверяем, что получили непустой JSON
  if [[ -z "$LOCKBOX_JSON" ]] || ! echo "$LOCKBOX_JSON" | python3 -c "import json,sys; json.load(sys.stdin)" 2>/dev/null; then
    warn "Lockbox вернул невалидный JSON, пропускаем загрузку секретов"
    return
  fi

  echo "$LOCKBOX_JSON" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for entry in data.get('entries', []):
    key = entry['key']
    # text_value или binary_value (base64)
    value = entry.get('text_value', '')
    print(f'{key}={value}')
" > "$ENV_FILE"

  chmod 600 "$ENV_FILE"
  log "Секреты загружены в ${ENV_FILE}"
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

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --network host \
  $ENV_FLAG \
  -v "${DATA_DIR}:/app/data" \
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
