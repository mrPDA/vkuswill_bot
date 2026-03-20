#!/usr/bin/env bash
# ============================================================
# PostgreSQL backup → Yandex Object Storage (S3)
# ============================================================
# Использование:
#   bash scripts/pg-backup-s3.sh
#
# Cron (каждые 6 часов):
#   0 */6 * * * /opt/vkuswill-bot/pg-backup-s3.sh >> /var/log/pg-backup.log 2>&1
#
# Переменные окружения (из .env на VM):
#   PG_BACKUP_BUCKET     — S3 bucket для бэкапов
#   PG_BACKUP_ACCESS_KEY — S3 access key
#   PG_BACKUP_SECRET_KEY — S3 secret key
#   PG_SUPERUSER_PASSWORD — пароль postgres (для pg_dumpall)
#
# Бэкапит все 3 базы: vkuswill, langfuse, metabase
# Формат: backups/pg/YYYY/MM/DD/HH-MM-all.sql.gz
# Ротация: lifecycle policy на S3 bucket (30 дней)
# ============================================================
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[pg-backup $(date -u +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[pg-backup $(date -u +%H:%M:%S)]${NC} $*"; }
err() { echo -e "${RED}[pg-backup $(date -u +%H:%M:%S)]${NC} $*"; }

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/vkuswill-bot}"
ENV_FILE="${DEPLOY_ROOT}/.env"
PG_CONTAINER="${PG_CONTAINER_NAME:-vkuswill-postgres}"
BACKUP_DIR="/tmp/pg-backup-$$"
S3_ENDPOINT="https://storage.yandexcloud.net"

# ─── Загрузка переменных ────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
  export PG_BACKUP_BUCKET="${PG_BACKUP_BUCKET:-$(grep '^PG_BACKUP_BUCKET=' "$ENV_FILE" | cut -d'=' -f2- || echo "")}"
  export PG_BACKUP_ACCESS_KEY="${PG_BACKUP_ACCESS_KEY:-$(grep '^PG_BACKUP_ACCESS_KEY=' "$ENV_FILE" | cut -d'=' -f2- || echo "")}"
  export PG_BACKUP_SECRET_KEY="${PG_BACKUP_SECRET_KEY:-$(grep '^PG_BACKUP_SECRET_KEY=' "$ENV_FILE" | cut -d'=' -f2- || echo "")}"
fi

if [[ -z "${PG_BACKUP_BUCKET:-}" || -z "${PG_BACKUP_ACCESS_KEY:-}" || -z "${PG_BACKUP_SECRET_KEY:-}" ]]; then
  err "Не заданы PG_BACKUP_BUCKET, PG_BACKUP_ACCESS_KEY или PG_BACKUP_SECRET_KEY"
  exit 1
fi

# ─── Проверка PostgreSQL ────────────────────────────────────
if ! docker exec "${PG_CONTAINER}" pg_isready -U postgres -q 2>/dev/null; then
  err "PostgreSQL (${PG_CONTAINER}) не доступен"
  exit 1
fi

# ─── Дамп ───────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
trap 'rm -rf "$BACKUP_DIR"' EXIT

TIMESTAMP=$(date -u +%Y/%m/%d/%H-%M)
DUMP_FILE="${BACKUP_DIR}/all.sql.gz"

log "Запуск pg_dumpall..."
docker exec "${PG_CONTAINER}" pg_dumpall -U postgres --clean --if-exists 2>/dev/null \
  | gzip -9 > "$DUMP_FILE"

DUMP_SIZE=$(du -sh "$DUMP_FILE" | cut -f1)
log "Дамп создан: ${DUMP_SIZE}"

# ─── Загрузка в S3 ──────────────────────────────────────────
S3_KEY="backups/pg/${TIMESTAMP}-all.sql.gz"

log "Загрузка в s3://${PG_BACKUP_BUCKET}/${S3_KEY}..."

export AWS_ACCESS_KEY_ID="${PG_BACKUP_ACCESS_KEY}"
export AWS_SECRET_ACCESS_KEY="${PG_BACKUP_SECRET_KEY}"
export AWS_DEFAULT_REGION="ru-central1"

if command -v aws &>/dev/null; then
  aws s3 cp "$DUMP_FILE" "s3://${PG_BACKUP_BUCKET}/${S3_KEY}" \
    --endpoint-url "$S3_ENDPOINT" \
    --quiet
else
  # Fallback: загрузка через curl (S3 PUT с подписью v4)
  # Требует Python для генерации подписи
  python3 - "$DUMP_FILE" "$PG_BACKUP_BUCKET" "$S3_KEY" "$S3_ENDPOINT" <<'PYEOF'
import sys, hashlib, hmac, datetime, os
from urllib.request import Request, urlopen

filepath, bucket, key, endpoint = sys.argv[1:5]
access_key = os.environ["AWS_ACCESS_KEY_ID"]
secret_key = os.environ["AWS_SECRET_ACCESS_KEY"]
region = "ru-central1"
service = "s3"

with open(filepath, "rb") as f:
    payload = f.read()

now = datetime.datetime.utcnow()
datestamp = now.strftime("%Y%m%d")
amz_date = now.strftime("%Y%m%dT%H%M%SZ")
host = endpoint.replace("https://", "").replace("http://", "")

canonical_uri = f"/{bucket}/{key}"
canonical_querystring = ""
payload_hash = hashlib.sha256(payload).hexdigest()
canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
signed_headers = "host;x-amz-content-sha256;x-amz-date"

canonical_request = f"PUT\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"

algorithm = "AWS4-HMAC-SHA256"
credential_scope = f"{datestamp}/{region}/{service}/aws4_request"
string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"

def sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()

signing_key = sign(sign(sign(sign(f"AWS4{secret_key}".encode("utf-8"), datestamp), region), service), "aws4_request")
signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

authorization = f"{algorithm} Credential={access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}"

url = f"{endpoint}/{bucket}/{key}"
req = Request(url, data=payload, method="PUT")
req.add_header("Authorization", authorization)
req.add_header("x-amz-date", amz_date)
req.add_header("x-amz-content-sha256", payload_hash)
req.add_header("Content-Type", "application/gzip")
req.add_header("Host", host)

urlopen(req)
print(f"Uploaded {len(payload)} bytes to {url}")
PYEOF
fi

log "Бэкап загружен: s3://${PG_BACKUP_BUCKET}/${S3_KEY} (${DUMP_SIZE})"
log "Готово"
