#!/usr/bin/env bash
# ============================================================
# Настройка самоподписанного SSL + nginx на Yandex Cloud VM
# ============================================================
# Для Telegram Bot API — не требует домена, работает с IP.
#
# Запускать один раз при первой настройке VM:
#   sudo bash setup-selfsigned-ssl.sh --ip <VM_PUBLIC_IP>
# ============================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[setup-ssl]${NC} $*"; }
warn() { echo -e "${YELLOW}[setup-ssl]${NC} $*"; }
err() { echo -e "${RED}[setup-ssl]${NC} $*"; }

# ─── Параметры ─────────────────────────────────────────────
IP=""
SSL_DIR="/opt/vkuswill-bot/ssl"

while [[ $# -gt 0 ]]; do
  case $1 in
    --ip) IP="$2"; shift 2 ;;
    *) err "Неизвестный параметр: $1"; exit 1 ;;
  esac
done

if [[ -z "$IP" ]]; then
  err "Использование: sudo bash setup-selfsigned-ssl.sh --ip <VM_PUBLIC_IP>"
  exit 1
fi

# ─── 1. Генерация самоподписанного сертификата ─────────────
log "Генерация самоподписанного SSL-сертификата для ${IP}..."

mkdir -p "$SSL_DIR"

# Создаём конфиг OpenSSL с SAN (Subject Alternative Name)
cat > "${SSL_DIR}/openssl.cnf" << SSLCNF
[req]
default_bits       = 2048
prompt             = no
default_md         = sha256
distinguished_name = dn
x509_extensions    = v3_req

[dn]
CN = ${IP}

[v3_req]
subjectAltName = IP:${IP}
SSLCNF

# Генерируем ключ и сертификат (10 лет)
openssl req \
  -newkey rsa:2048 \
  -sha256 \
  -nodes \
  -x509 \
  -days 3650 \
  -keyout "${SSL_DIR}/private.key" \
  -out "${SSL_DIR}/cert.pem" \
  -config "${SSL_DIR}/openssl.cnf"

chmod 600 "${SSL_DIR}/private.key"
chmod 644 "${SSL_DIR}/cert.pem"

log "Сертификат создан: ${SSL_DIR}/cert.pem"
log "Ключ создан: ${SSL_DIR}/private.key"

# ─── 2. Установка nginx ───────────────────────────────────
log "Установка nginx..."
apt-get update -qq
apt-get install -y -qq nginx > /dev/null 2>&1
log "nginx установлен"

# ─── 3. Настройка nginx ───────────────────────────────────
log "Настройка nginx для ${IP}..."

cat > /etc/nginx/sites-available/vkuswill-bot << NGINX_EOF
server {
    listen 80;
    server_name ${IP};

    location /health {
        proxy_pass http://127.0.0.1:8080/health;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${IP};

    ssl_certificate     ${SSL_DIR}/cert.pem;
    ssl_certificate_key ${SSL_DIR}/private.key;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    add_header X-Frame-Options DENY always;
    add_header X-Content-Type-Options nosniff always;
    add_header Strict-Transport-Security "max-age=31536000" always;

    location /webhook {
        proxy_pass http://127.0.0.1:8080/webhook;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8080/health;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }

    location /langfuse/ {
        proxy_pass http://127.0.0.1:3000/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    location /mcp {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Connection \"\";
        proxy_set_header Host 127.0.0.1:8081;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    location / {
        return 404;
    }
}
NGINX_EOF

# Активировать конфиг
ln -sf /etc/nginx/sites-available/vkuswill-bot /etc/nginx/sites-enabled/vkuswill-bot
rm -f /etc/nginx/sites-enabled/default

# Проверить и перезапустить nginx
nginx -t
systemctl enable nginx
systemctl restart nginx
log "nginx запущен с самоподписанным SSL"

# ─── 4. Открытие портов в iptables ────────────────────────
if command -v iptables &>/dev/null; then
  iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || \
    iptables -A INPUT -p tcp --dport 80 -j ACCEPT
  iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || \
    iptables -A INPUT -p tcp --dport 443 -j ACCEPT
  log "Порты 80 и 443 открыты в iptables"
fi

# ─── 5. Итог ───────────────────────────────────────────────
log "════════════════════════════════════════"
log "Самоподписанный SSL настроен!"
log "IP:         ${IP}"
log "Сертификат: ${SSL_DIR}/cert.pem"
log "Ключ:       ${SSL_DIR}/private.key"
log "Webhook:    https://${IP}/webhook"
log "Health:     https://${IP}/health"
log "════════════════════════════════════════"
log ""
log "Следующие шаги:"
log "  1. Откройте порты 80 и 443 в Yandex Cloud Security Group"
log "  2. Добавьте GitHub Secret:"
log "     gh secret set WEBHOOK_HOST -b \"${IP}\""
log "  3. Запустите CD pipeline:"
log "     gh workflow run cd.yml --ref main -f tag=v0.4.0"
