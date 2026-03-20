#!/usr/bin/env bash
# ============================================================
# Миграция данных: Yandex Managed PostgreSQL → VM PostgreSQL
# ============================================================
# Запускать на VM после того как:
#   1. Локальный PostgreSQL запущен (deploy_postgres)
#   2. Managed PG ещё доступен
#
# Использование:
#   bash migrate-pg-to-vm.sh \
#     --source "postgresql://bot:PASS@MANAGED_HOST:6432/vkuswill?sslmode=require" \
#     --source-langfuse "postgresql://langfuse:PASS@MANAGED_HOST:6432/langfuse?sslmode=require" \
#     --source-metabase "postgresql://metabase:PASS@MANAGED_HOST:6432/metabase?sslmode=require"
# ============================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[migrate $(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[migrate $(date +%H:%M:%S)]${NC} $*"; }
err() { echo -e "${RED}[migrate $(date +%H:%M:%S)]${NC} $*"; }

SOURCE_VKUSWILL=""
SOURCE_LANGFUSE=""
SOURCE_METABASE=""
PG_CONTAINER="${PG_CONTAINER_NAME:-vkuswill-postgres}"
DUMP_DIR="/tmp/pg-migrate-$$"

while [[ $# -gt 0 ]]; do
  case $1 in
    --source)           SOURCE_VKUSWILL="$2"; shift 2 ;;
    --source-langfuse)  SOURCE_LANGFUSE="$2"; shift 2 ;;
    --source-metabase)  SOURCE_METABASE="$2"; shift 2 ;;
    *) echo -e "${RED}Неизвестный параметр: $1${NC}"; exit 1 ;;
  esac
done

if [[ -z "$SOURCE_VKUSWILL" ]]; then
  err "--source (URL Managed PG для vkuswill) обязателен"
  exit 1
fi

# Проверить, что локальный PG запущен
if ! docker exec "${PG_CONTAINER}" pg_isready -U postgres -q 2>/dev/null; then
  err "Локальный PostgreSQL (${PG_CONTAINER}) не запущен"
  exit 1
fi

mkdir -p "$DUMP_DIR"
trap 'rm -rf "$DUMP_DIR"' EXIT

# ─── Миграция vkuswill ──────────────────────────────────────
log "═══════════════════════════════════════"
log "Миграция БД: vkuswill"
log "═══════════════════════════════════════"

log "Дамп из Managed PG..."
docker exec "${PG_CONTAINER}" pg_dump \
  "${SOURCE_VKUSWILL}" \
  --no-owner --no-acl --clean --if-exists \
  > "${DUMP_DIR}/vkuswill.sql"

VKUSWILL_SIZE=$(du -sh "${DUMP_DIR}/vkuswill.sql" | cut -f1)
log "Дамп vkuswill: ${VKUSWILL_SIZE}"

log "Восстановление в локальный PG..."
docker exec -i "${PG_CONTAINER}" psql -U bot -d vkuswill \
  < "${DUMP_DIR}/vkuswill.sql" 2>&1 | tail -5

log "Проверка таблиц:"
docker exec "${PG_CONTAINER}" psql -U bot -d vkuswill -c \
  "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;"

# ─── Миграция langfuse ──────────────────────────────────────
if [[ -n "$SOURCE_LANGFUSE" ]]; then
  log ""
  log "═══════════════════════════════════════"
  log "Миграция БД: langfuse"
  log "═══════════════════════════════════════"

  log "Дамп из Managed PG..."
  docker exec "${PG_CONTAINER}" pg_dump \
    "${SOURCE_LANGFUSE}" \
    --no-owner --no-acl --clean --if-exists \
    > "${DUMP_DIR}/langfuse.sql"

  LANGFUSE_SIZE=$(du -sh "${DUMP_DIR}/langfuse.sql" | cut -f1)
  log "Дамп langfuse: ${LANGFUSE_SIZE}"

  log "Восстановление в локальный PG..."
  docker exec -i "${PG_CONTAINER}" psql -U langfuse -d langfuse \
    < "${DUMP_DIR}/langfuse.sql" 2>&1 | tail -5

  log "Langfuse мигрирован"
else
  warn "Пропуск langfuse (--source-langfuse не указан)"
fi

# ─── Миграция metabase ──────────────────────────────────────
if [[ -n "$SOURCE_METABASE" ]]; then
  log ""
  log "═══════════════════════════════════════"
  log "Миграция БД: metabase"
  log "═══════════════════════════════════════"

  log "Дамп из Managed PG..."
  docker exec "${PG_CONTAINER}" pg_dump \
    "${SOURCE_METABASE}" \
    --no-owner --no-acl --clean --if-exists \
    > "${DUMP_DIR}/metabase.sql"

  METABASE_SIZE=$(du -sh "${DUMP_DIR}/metabase.sql" | cut -f1)
  log "Дамп metabase: ${METABASE_SIZE}"

  log "Восстановление в локальный PG..."
  docker exec -i "${PG_CONTAINER}" psql -U metabase -d metabase \
    < "${DUMP_DIR}/metabase.sql" 2>&1 | tail -5

  log "Metabase мигрирован"
else
  warn "Пропуск metabase (--source-metabase не указан)"
fi

# ─── Итог ───────────────────────────────────────────────────
log ""
log "═══════════════════════════════════════"
log "Миграция завершена!"
log "═══════════════════════════════════════"
log ""
log "Следующие шаги:"
log "  1. Проверить данные: docker exec ${PG_CONTAINER} psql -U bot -d vkuswill -c 'SELECT count(*) FROM users;'"
log "  2. Обновить Lockbox (DATABASE_URL → localhost:5432)"
log "  3. Перезапустить бота с новыми секретами"
log "  4. Убедиться, что всё работает"
log "  5. Удалить Managed PostgreSQL из Terraform и применить"
