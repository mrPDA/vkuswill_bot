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
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/vkuswill-bot}"
CONTAINER_NAME="${CONTAINER_NAME:-vkuswill-bot}"
MCP_CONTAINER_NAME="${MCP_CONTAINER_NAME:-vkuswill-mcp-server}"
LANGFUSE_CONTAINER_NAME="${LANGFUSE_CONTAINER_NAME:-vkuswill-langfuse}"
METABASE_CONTAINER_NAME="${METABASE_CONTAINER_NAME:-vkuswill-metabase}"
HEALTH_PORT="${HEALTH_PORT:-8080}"
MCP_DEFAULT_PORT="${MCP_DEFAULT_PORT:-8081}"
LANGFUSE_PORT="${LANGFUSE_PORT:-3000}"
METABASE_PORT="${METABASE_PORT:-3001}"
PUBLIC_WEBHOOK_PATH=""
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

ENV_FILE="${DEPLOY_ROOT}/.env"
PROMPT_FILE="${DEPLOY_ROOT}/system_prompt.txt"
DATA_DIR="${DEPLOY_ROOT}/data"
SSL_DIR="${DEPLOY_ROOT}/ssl"

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
  # SYSTEM_PROMPT сохраняется в отдельный файл (Docker --env-file не поддерживает многострочные значения)
  echo "$LOCKBOX_JSON" | python3 -c "
import json, sys
data = json.load(sys.stdin)
prompt = ''
env_lines = []
for entry in data.get('entries', []):
    key = entry['key']
    value = entry.get('text_value', '')
    if key == 'SYSTEM_PROMPT':
        prompt = value
    else:
        env_lines.append(f'{key}={value}')
with open('${ENV_FILE}', 'w') as f:
    f.write('\n'.join(env_lines) + '\n')
with open('${PROMPT_FILE}', 'w') as f:
    f.write(prompt)
print(f'env={len(env_lines)} prompt={len(prompt)}')
"

  chmod 600 "$ENV_FILE" "$PROMPT_FILE"
  log "Секреты загружены из Lockbox ($(grep -c '=' "$ENV_FILE" || echo 0) записей, промпт: $(wc -c < "$PROMPT_FILE") байт)"
}

# ─── 2b. Определение публичного пути webhook ────────────────
resolve_public_webhook_path() {
  local raw=""

  if [[ -f "$ENV_FILE" ]]; then
    raw=$(grep -E '^WEBHOOK_PATH=' "$ENV_FILE" | tail -1 | cut -d'=' -f2- || true)
  fi

  if [[ -z "$raw" ]]; then
    if [[ "$DEPLOY_ROOT" == *-stg ]]; then
      raw="/webhook-stg"
    else
      raw="/webhook"
    fi
  fi

  PUBLIC_WEBHOOK_PATH=$(python3 - "$raw" <<'PYCODE'
import sys

path = (sys.argv[1] or "").strip()
if not path:
    path = "/webhook"
if not path.startswith("/"):
    path = f"/{path}"
if len(path) > 1:
    path = path.rstrip("/")
print(path)
PYCODE
)

  log "Публичный webhook path: ${PUBLIC_WEBHOOK_PATH}"
}

# ─── 2c. Идемпотентная настройка nginx для webhook ──────────
ensure_webhook_nginx_route() {
  local NGINX_CONF="/etc/nginx/sites-available/vkuswill-bot"

  if [[ -z "${PUBLIC_WEBHOOK_PATH}" ]]; then
    warn "PUBLIC_WEBHOOK_PATH не определён, пропускаем настройку webhook route"
    return 0
  fi

  if [[ ! -f "$NGINX_CONF" ]]; then
    warn "Nginx-конфиг ${NGINX_CONF} не найден, пропускаем проверку ${PUBLIC_WEBHOOK_PATH}"
    return 0
  fi

  if ! sudo -n true 2>/dev/null; then
    warn "Нет прав sudo без пароля, пропускаем автоматическую настройку webhook route"
    return 0
  fi

  log "Проверка nginx route ${PUBLIC_WEBHOOK_PATH} -> 127.0.0.1:${HEALTH_PORT}/webhook"

  if ! sudo python3 - "$NGINX_CONF" "$PUBLIC_WEBHOOK_PATH" "$HEALTH_PORT" <<'PYCODE'
import pathlib
import re
import sys

conf_path = pathlib.Path(sys.argv[1])
webhook_path = sys.argv[2]
health_port = sys.argv[3]

content = conf_path.read_text(encoding="utf-8")
escaped_path = re.escape(webhook_path)
pattern = re.compile(
    rf"(?ms)^    location\s+{escaped_path}\s*\{{.*?^    \}}\n"
)

block = (
    f"    location {webhook_path} {{\n"
    f"        proxy_pass http://127.0.0.1:{health_port}/webhook;\n"
    "        proxy_set_header Host $host;\n"
    "        proxy_set_header X-Real-IP $remote_addr;\n"
    "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
    "        proxy_set_header X-Forwarded-Proto $scheme;\n"
    "        proxy_read_timeout 60s;\n"
    "        proxy_send_timeout 60s;\n"
    "    }\n"
)

match = pattern.search(content)
if match:
    current_block = match.group(0)
    if f"proxy_pass http://127.0.0.1:{health_port}/webhook;" in current_block:
        raise SystemExit(0)
    updated = content[: match.start()] + block + content[match.end() :]
else:
    needle = "    location / {\n        return 404;\n    }\n"
    parts = content.rsplit(needle, 1)
    if len(parts) != 2:
        raise SystemExit("cannot_find_fallback_location_block")
    updated = parts[0] + block + "\n" + needle + parts[1]

conf_path.write_text(updated, encoding="utf-8")
PYCODE
  then
    err "Не удалось обновить nginx-конфиг для ${PUBLIC_WEBHOOK_PATH}"
    return 1
  fi

  if ! sudo nginx -t >/dev/null 2>&1; then
    err "nginx -t не прошел после настройки ${PUBLIC_WEBHOOK_PATH}"
    return 1
  fi

  sudo systemctl reload nginx
  log "Nginx route ${PUBLIC_WEBHOOK_PATH} настроен"
}

# ─── 2d. Идемпотентная настройка nginx для voice-link ───────
ensure_voice_link_nginx_route() {
  local NGINX_CONF="/etc/nginx/sites-available/vkuswill-bot"

  if [[ ! -f "$NGINX_CONF" ]]; then
    warn "Nginx-конфиг ${NGINX_CONF} не найден, пропускаем проверку /voice-link/"
    return 0
  fi

  if grep -qE 'location[[:space:]]+/voice-link/' "$NGINX_CONF"; then
    log "Nginx route /voice-link/ уже настроен"
    return 0
  fi

  if ! sudo -n true 2>/dev/null; then
    warn "Нет прав sudo без пароля, пропускаем автоматическое добавление /voice-link/ в nginx"
    return 0
  fi

  log "Добавление nginx route /voice-link/ в ${NGINX_CONF}..."

  if ! sudo python3 - "$NGINX_CONF" <<'PYCODE'
import pathlib
import sys

conf_path = pathlib.Path(sys.argv[1])
content = conf_path.read_text(encoding="utf-8")

if "location /voice-link/" in content:
    raise SystemExit(0)

block = """    # Voice-link API для привязки аккаунта в Alice Skill
    location /voice-link/ {
        proxy_pass http://127.0.0.1:8080/voice-link/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 20s;
        proxy_send_timeout 20s;
    }
"""

needle = "    location / {\\n        return 404;\\n    }\\n"
parts = content.rsplit(needle, 1)
if len(parts) != 2:
    raise SystemExit("cannot_find_fallback_location_block")

updated = parts[0] + block + "\\n" + needle + parts[1]
conf_path.write_text(updated, encoding="utf-8")
PYCODE
  then
    err "Не удалось обновить nginx-конфиг для /voice-link/"
    return 1
  fi

  if ! sudo nginx -t >/dev/null 2>&1; then
    err "nginx -t не прошел после добавления /voice-link/"
    return 1
  fi

  sudo systemctl reload nginx
  log "Nginx конфиг обновлён и перезагружен (/voice-link/)"
}

# ─── 3. Очистка места перед pull ────────────────────────────
log "Очистка Docker (образы, кеш, остановленные контейнеры)..."
# Удаляем остановленные контейнеры, висячие образы и build-кеш
docker system prune -f --filter "until=48h" 2>/dev/null || true
# Дополнительно: удаляем неиспользуемые образы старше 7 дней
docker image prune -af --filter "until=168h" 2>/dev/null || true
DISK_FREE=$(df -h / | awk 'NR==2 {print $4}')
log "Свободно на диске после очистки: ${DISK_FREE}"

# ─── 4. Pull нового образа ──────────────────────────────────
log "Pulling image: ${IMAGE}..."
docker pull "$IMAGE"

# ─── 5. Остановка старого контейнера ────────────────────────
if docker ps -q -f "name=${CONTAINER_NAME}" | grep -q .; then
  log "Остановка текущего контейнера ${CONTAINER_NAME}..."
  docker stop "$CONTAINER_NAME" --time 30 2>/dev/null || true
  docker rm "$CONTAINER_NAME" 2>/dev/null || true
  log "Старый контейнер остановлен"
else
  warn "Контейнер ${CONTAINER_NAME} не запущен"
fi

if docker ps -q -f "name=${MCP_CONTAINER_NAME}" | grep -q .; then
  log "Остановка текущего контейнера ${MCP_CONTAINER_NAME}..."
  docker stop "$MCP_CONTAINER_NAME" --time 30 2>/dev/null || true
  docker rm "$MCP_CONTAINER_NAME" 2>/dev/null || true
  log "Старый контейнер MCP остановлен"
elif docker ps -aq -f "name=${MCP_CONTAINER_NAME}" | grep -q .; then
  docker rm "$MCP_CONTAINER_NAME" 2>/dev/null || true
fi

# ─── 6. Загрузка секретов ────────────────────────────────────
load_lockbox_secrets

# ─── 6a. Определение webhook path + настройка nginx route ────
resolve_public_webhook_path
ensure_webhook_nginx_route

# ─── 6b. Проверка nginx voice-link route ─────────────────────
ensure_voice_link_nginx_route

# ─── 6c. Запуск Langfuse (self-hosted, если настроен) ────────
deploy_langfuse() {

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

  # URL-кодирование пароля в DATABASE_URL (спецсимволы / и + ломают Prisma)
  if [[ -n "$LF_DB_URL" ]]; then
    LF_DB_URL=$(echo "$LF_DB_URL" | python3 -c "
import sys
from urllib.parse import urlparse, quote, unquote, urlunparse
url = sys.stdin.read().strip()
u = urlparse(url)
if u.password:
    user = unquote(u.username or '')
    password = unquote(u.password or '')
    host = u.hostname or ''
    port = f':{u.port}' if u.port else ''
    netloc = f'{quote(user, safe=\"\")}:{quote(password, safe=\"\")}@{host}{port}'
    print(urlunparse(u._replace(netloc=netloc)))
else:
    print(url)
")
  fi

  if [[ -z "$LF_DB_URL" ]]; then
    warn "LANGFUSE_DATABASE_URL не задан, Langfuse пропущен"
    return
  fi

  log "Обновление Langfuse..."
  docker pull langfuse/langfuse:2 2>/dev/null || true

  # Остановить предыдущий контейнер
  if docker ps -q -f "name=${LANGFUSE_CONTAINER_NAME}" | grep -q .; then
    docker stop "$LANGFUSE_CONTAINER_NAME" --time 10 2>/dev/null || true
    docker rm "$LANGFUSE_CONTAINER_NAME" 2>/dev/null || true
  fi

  docker run -d \
    --name "$LANGFUSE_CONTAINER_NAME" \
    --init \
    --restart unless-stopped \
    --network host \
    -e "DATABASE_URL=${LF_DB_URL}" \
    -e "NEXTAUTH_URL=http://localhost:${LANGFUSE_PORT}" \
    -e "NEXTAUTH_SECRET=${LF_AUTH_SECRET}" \
    -e "SALT=${LF_SALT}" \
    -e "TELEMETRY_ENABLED=false" \
    -e "HOSTNAME=0.0.0.0" \
    -e "PORT=${LANGFUSE_PORT}" \
    --log-driver json-file \
    --log-opt max-size=20m \
    --log-opt max-file=2 \
    --label "service=langfuse" \
    langfuse/langfuse:2

  # Подождать и проверить, что контейнер жив
  sleep 5
  if docker ps -q -f "name=${LANGFUSE_CONTAINER_NAME}" | grep -q .; then
    log "Langfuse запущен на порту ${LANGFUSE_PORT}"
  else
    warn "Langfuse контейнер упал! Логи:"
    docker logs "$LANGFUSE_CONTAINER_NAME" --tail 30 2>&1 || true
  fi
}

deploy_langfuse

# ─── 6c. Запуск Metabase (BI-дашборды, если настроен) ────────
deploy_metabase() {

  # Проверяем, включён ли Metabase
  if [[ ! -f "$ENV_FILE" ]] || ! grep -q '^METABASE_ENABLED=true' "$ENV_FILE"; then
    log "Metabase не включён (METABASE_ENABLED!=true), пропускаем"
    return
  fi

  # Извлекаем DATABASE_URL из .env
  local MB_DB_URL
  MB_DB_URL=$(grep '^METABASE_DATABASE_URL=' "$ENV_FILE" | cut -d'=' -f2- || echo "")

  if [[ -z "$MB_DB_URL" ]]; then
    warn "METABASE_DATABASE_URL не задан, Metabase пропущен"
    return
  fi

  # Парсим компоненты URL (postgresql://user:pass@host:port/dbname?params)
  local MB_HOST MB_PORT MB_USER MB_PASS MB_DBNAME
  read -r MB_HOST MB_PORT MB_USER MB_PASS MB_DBNAME < <(python3 -c "
from urllib.parse import urlparse, unquote
u = urlparse('${MB_DB_URL}')
print(u.hostname or '', u.port or 6432, unquote(u.username or ''), unquote(u.password or ''), (u.path or '/metabase').lstrip('/'))
")

  if [[ -z "$MB_HOST" || -z "$MB_USER" ]]; then
    warn "Не удалось распарсить METABASE_DATABASE_URL, Metabase пропущен"
    return
  fi

  log "Обновление Metabase..."
  docker pull metabase/metabase:v0.58.x 2>/dev/null || true

  # Остановить предыдущий контейнер
  if docker ps -q -f "name=${METABASE_CONTAINER_NAME}" | grep -q .; then
    docker stop "$METABASE_CONTAINER_NAME" --time 10 2>/dev/null || true
    docker rm "$METABASE_CONTAINER_NAME" 2>/dev/null || true
  elif docker ps -aq -f "name=${METABASE_CONTAINER_NAME}" | grep -q .; then
    docker rm "$METABASE_CONTAINER_NAME" 2>/dev/null || true
  fi

  docker run -d \
    --name "$METABASE_CONTAINER_NAME" \
    --init \
    --restart unless-stopped \
    --network host \
    -e "MB_DB_TYPE=postgres" \
    -e "MB_DB_DBNAME=${MB_DBNAME}" \
    -e "MB_DB_PORT=${MB_PORT}" \
    -e "MB_DB_USER=${MB_USER}" \
    -e "MB_DB_PASS=${MB_PASS}" \
    -e "MB_DB_HOST=${MB_HOST}" \
    -e "MB_JETTY_HOST=0.0.0.0" \
    -e "MB_JETTY_PORT=${METABASE_PORT}" \
    -e "MB_APPLICATION_DB_MAX_CONNECTION_POOL_SIZE=5" \
    -e "JAVA_TOOL_OPTIONS=-Xmx512m" \
    --memory 1g \
    --log-driver json-file \
    --log-opt max-size=20m \
    --log-opt max-file=2 \
    --label "service=metabase" \
    metabase/metabase:v0.58.x

  # Подождать и проверить, что контейнер жив
  sleep 5
  if docker ps -q -f "name=${METABASE_CONTAINER_NAME}" | grep -q .; then
    log "Metabase запущен на порту ${METABASE_PORT}"
  else
    warn "Metabase контейнер упал! Логи:"
    docker logs "$METABASE_CONTAINER_NAME" --tail 30 2>&1 || true
  fi
}

deploy_metabase

# ─── 7. Запуск нового контейнера ────────────────────────────
log "Запуск контейнера ${CONTAINER_NAME} (${TAG})..."

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
  log "WEBHOOK_PATH=${PUBLIC_WEBHOOK_PATH}"
else
  warn "Файл ${ENV_FILE} не найден. Контейнер будет запущен без переменных окружения из файла."
  warn "Убедитесь, что .env файл создан вручную или секреты доступны через Lockbox."
fi

# Параметры MCP-сервера из .env
MCP_ENABLED="false"
MCP_PORT="${MCP_DEFAULT_PORT}"
if [[ -f "$ENV_FILE" ]]; then
  mcp_enabled_raw=$(grep -E '^MCP_SERVER_ENABLED=' "$ENV_FILE" | tail -1 | cut -d'=' -f2- || true)
  if [[ "${mcp_enabled_raw,,}" == "true" ]]; then
    MCP_ENABLED="true"
  fi

  mcp_port_raw=$(grep -E '^MCP_SERVER_PORT=' "$ENV_FILE" | tail -1 | cut -d'=' -f2- || true)
  if [[ "$mcp_port_raw" =~ ^[0-9]+$ ]] && (( mcp_port_raw >= 1 && mcp_port_raw <= 65535 )); then
    MCP_PORT="$mcp_port_raw"
  fi
fi
log "MCP_SERVER_ENABLED=${MCP_ENABLED}, MCP_SERVER_PORT=${MCP_PORT}"

# Директория для persistent-данных (SQLite preferences)
mkdir -p "$DATA_DIR"
# botuser в контейнере имеет UID/GID 10001 (Dockerfile)
# Даём права только владельцу и группе — НЕ world-writable
# sudo нужен: deploy-пользователь не владеет файлами, созданными контейнером
sudo chown -R 10001:10001 "$DATA_DIR" 2>/dev/null || warn "chown DATA_DIR не удался (нет sudo?), пропускаем"
sudo chmod 750 "$DATA_DIR" 2>/dev/null || true
sudo chmod -R 640 "$DATA_DIR/"* 2>/dev/null || true
log "DATA_DIR=${DATA_DIR} — права: $(ls -ld "$DATA_DIR")"
log "Файлы в DATA_DIR: $(ls -la "$DATA_DIR/" 2>/dev/null || echo '(пусто)')"

# SSL-сертификат для самоподписанного webhook
SSL_MOUNT=""
SSL_ENV=""
SSL_CERT_SOURCE="${SSL_DIR}/cert.pem"
# Для staging допускаем fallback на общий cert production-контура,
# если отдельный stg-cert не создан.
if [[ ! -f "${SSL_CERT_SOURCE}" && "${DEPLOY_ROOT}" == *-stg && -f "/opt/vkuswill-bot/ssl/cert.pem" ]]; then
  SSL_CERT_SOURCE="/opt/vkuswill-bot/ssl/cert.pem"
fi

if [[ -f "${SSL_CERT_SOURCE}" ]]; then
  SSL_MOUNT="-v ${SSL_CERT_SOURCE}:/app/ssl/cert.pem:ro"
  SSL_ENV="-e WEBHOOK_CERT_PATH=/app/ssl/cert.pem"
  log "Найден SSL-сертификат: ${SSL_CERT_SOURCE}"
else
  warn "SSL-сертификат не найден (${SSL_DIR}/cert.pem) — webhook без сертификата"
fi

# Переопределение модели: --model имеет приоритет над Lockbox/.env
MODEL_ENV=""
if [[ -n "$GIGACHAT_MODEL_OVERRIDE" ]]; then
  MODEL_ENV="-e GIGACHAT_MODEL=${GIGACHAT_MODEL_OVERRIDE}"
  log "Модель GigaChat: ${GIGACHAT_MODEL_OVERRIDE} (override)"
fi

# SYSTEM_PROMPT хранится в отдельном файле (Docker --env-file не поддерживает многострочные значения)
# Экспортируем в окружение, а docker run наследует через `-e SYSTEM_PROMPT` (без =value)
PROMPT_ENV=""
if [[ -f "$PROMPT_FILE" ]] && [[ -s "$PROMPT_FILE" ]]; then
  export SYSTEM_PROMPT
  SYSTEM_PROMPT=$(cat "$PROMPT_FILE")
  PROMPT_ENV="-e SYSTEM_PROMPT"
  log "SYSTEM_PROMPT загружен из ${PROMPT_FILE} ($(wc -c < "$PROMPT_FILE") байт)"
fi

docker run -d \
  --name "$CONTAINER_NAME" \
  --init \
  --restart unless-stopped \
  --network host \
  $ENV_FLAG \
  $MODEL_ENV \
  $PROMPT_ENV \
  ${DEPLOY_EXTRA_ENV:-} \
  -v "${DATA_DIR}:/app/data" \
  $SSL_MOUNT \
  $SSL_ENV \
  -e "USE_WEBHOOK=true" \
  -e "WEBHOOK_PATH=${PUBLIC_WEBHOOK_PATH}" \
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

deploy_mcp_server() {
  if [[ "$MCP_ENABLED" != "true" ]]; then
    log "MCP-сервер отключён (MCP_SERVER_ENABLED!=true), пропускаем запуск"
    return
  fi

  log "Запуск контейнера ${MCP_CONTAINER_NAME} на порту ${MCP_PORT}..."
  docker run -d \
    --name "$MCP_CONTAINER_NAME" \
    --init \
    --restart unless-stopped \
    --network host \
    $ENV_FLAG \
    -v "${DATA_DIR}:/app/data" \
    --health-cmd "python -c \"import socket; socket.create_connection(('127.0.0.1', ${MCP_PORT}), 3).close()\" 2>/dev/null || exit 1" \
    --health-interval=30s \
    --health-timeout=10s \
    --health-start-period=10s \
    --health-retries=3 \
    --log-driver json-file \
    --log-opt max-size=50m \
    --log-opt max-file=3 \
    --label "service=mcp-server" \
    --label "version=${TAG}" \
    --label "deployed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$IMAGE" \
    python -m vkuswill_bot.mcp_server --http --host 0.0.0.0 --port "${MCP_PORT}"

  for i in $(seq 1 "$HEALTH_RETRIES"); do
    sleep "$HEALTH_DELAY"
    if python3 -c "import socket; socket.create_connection(('127.0.0.1', ${MCP_PORT}), 3).close()" 2>/dev/null; then
      log "MCP health check OK (попытка ${i}/${HEALTH_RETRIES})"
      return
    fi
    warn "MCP health check попытка ${i}/${HEALTH_RETRIES}: port ${MCP_PORT} недоступен"
  done

  err "MCP health check FAILED после ${HEALTH_RETRIES} попыток!"
  err "Логи контейнера ${MCP_CONTAINER_NAME}:"
  docker logs --tail 50 "$MCP_CONTAINER_NAME" 2>&1 || true
  exit 1
}

deploy_mcp_server

# ─── 8. Health check ────────────────────────────────────────
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

# ─── 9. Финальная очистка ────────────────────────────────────
log "Финальная очистка неиспользуемых Docker-образов..."
docker image prune -f --filter "until=168h" 2>/dev/null || true

# ─── 10. Итог ────────────────────────────────────────────────
log "════════════════════════════════════════"
log "Деплой ${TAG} завершён успешно!"
log "Image:     ${IMAGE}"
log "Container: ${CONTAINER_NAME}"
if [[ "$MCP_ENABLED" == "true" ]]; then
  log "MCP:       http://localhost:${MCP_PORT}/mcp"
fi
log "Health:    http://localhost:${HEALTH_PORT}/health"
log "════════════════════════════════════════"

# Показать статус
docker ps --filter "name=${CONTAINER_NAME}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
docker ps --filter "name=${MCP_CONTAINER_NAME}" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
