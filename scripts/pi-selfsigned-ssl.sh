#!/usr/bin/env bash
# Генерация самоподписанного сертификата для webhook на Raspberry Pi (без nginx).
# Telegram принимает такой cert, если передать публичный PEM в setWebhook и слушать HTTPS.
#
# Использование (из корня репозитория на Pi):
#   bash scripts/pi-selfsigned-ssl.sh 203.0.113.50
#   bash scripts/pi-selfsigned-ssl.sh bot.home.example  # DNS в SAN
#
# Файлы: ssl/cert.pem, ssl/private.key
# В .env: WEBHOOK_HOST=https://IP:8443 WEBHOOK_PORT=8443
#         WEBHOOK_CERT_PATH=/app/ssl/cert.pem WEBHOOK_KEY_PATH=/app/ssl/private.key

set -euo pipefail

CN="${1:?Укажите публичный IP или hostname (должен совпадать с WEBHOOK_HOST / SAN)}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSL_DIR="${ROOT}/ssl"
mkdir -p "$SSL_DIR"

# Числовой IP → SAN type IP, иначе DNS (через openssl.cnf — совместимость без -addext)
if [[ "$CN" =~ ^[0-9.]+$ ]]; then
  SAN_LINE="IP:${CN}"
else
  SAN_LINE="DNS:${CN}"
fi

cat > "${SSL_DIR}/openssl.cnf" << CNFCNF
[req]
default_bits       = 2048
prompt             = no
default_md         = sha256
distinguished_name = dn
x509_extensions    = v3_req

[dn]
CN = ${CN}

[v3_req]
subjectAltName = ${SAN_LINE}
CNFCNF

openssl req \
  -newkey rsa:2048 \
  -sha256 \
  -nodes \
  -x509 \
  -days 3650 \
  -keyout "${SSL_DIR}/private.key" \
  -out "${SSL_DIR}/cert.pem" \
  -config "${SSL_DIR}/openssl.cnf" \
  -extensions v3_req

chmod 600 "${SSL_DIR}/private.key"
chmod 644 "${SSL_DIR}/cert.pem"

echo "Готово: ${SSL_DIR}/cert.pem и ${SSL_DIR}/private.key"
echo "Пробросьте на роутере внешний TCP-порт → ${CN}:WEBHOOK_PORT (например 8443)."
