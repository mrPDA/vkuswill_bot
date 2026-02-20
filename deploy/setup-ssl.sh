#!/usr/bin/env bash
# ============================================================
# Настройка nginx + Let's Encrypt SSL на Yandex Cloud VM
# ============================================================
# Запускать один раз при первой настройке VM:
#   sudo bash setup-ssl.sh --domain bot.example.com --email admin@example.com
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
DOMAIN=""
EMAIL=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --domain) DOMAIN="$2"; shift 2 ;;
    --email)  EMAIL="$2"; shift 2 ;;
    *) err "Неизвестный параметр: $1"; exit 1 ;;
  esac
done

if [[ -z "$DOMAIN" || -z "$EMAIL" ]]; then
  err "Использование: sudo bash setup-ssl.sh --domain bot.example.com --email admin@example.com"
  exit 1
fi

# ─── 1. Установка nginx ───────────────────────────────────
log "Установка nginx..."
apt-get update -qq
apt-get install -y -qq nginx > /dev/null 2>&1
log "nginx установлен"

# ─── 2. Установка certbot ─────────────────────────────────
log "Установка certbot..."
apt-get install -y -qq certbot python3-certbot-nginx > /dev/null 2>&1
log "certbot установлен"

# ─── 3. Директория для certbot challenge ───────────────────
mkdir -p /var/www/certbot

# ─── 4. Временный nginx-конфиг для получения сертификата ───
log "Настройка временного nginx-конфига для ${DOMAIN}..."

cat > /etc/nginx/sites-available/vkuswill-bot << NGINX_EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 200 'OK';
        add_header Content-Type text/plain;
    }
}
NGINX_EOF

# Активировать конфиг
ln -sf /etc/nginx/sites-available/vkuswill-bot /etc/nginx/sites-enabled/vkuswill-bot
rm -f /etc/nginx/sites-enabled/default

# Проверить и перезапустить nginx
nginx -t
systemctl restart nginx
log "nginx запущен с временным конфигом"

# ─── 5. Получение SSL-сертификата ──────────────────────────
log "Получение SSL-сертификата для ${DOMAIN}..."
certbot certonly \
  --webroot \
  --webroot-path /var/www/certbot \
  --domain "$DOMAIN" \
  --email "$EMAIL" \
  --agree-tos \
  --non-interactive

if [[ ! -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ]]; then
  err "Не удалось получить сертификат! Проверьте:"
  err "  1. DNS A-запись ${DOMAIN} указывает на IP этого сервера"
  err "  2. Порт 80 открыт в Security Group"
  exit 1
fi

log "SSL-сертификат получен!"

# ─── 6. Установка полного nginx-конфига с SSL ──────────────
log "Установка production nginx-конфига..."

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONF_SOURCE="${SCRIPT_DIR}/nginx-bot.conf"

if [[ -f "$CONF_SOURCE" ]]; then
  # Подставляем домен в шаблон
  sed "s/\${DOMAIN}/${DOMAIN}/g" "$CONF_SOURCE" > /etc/nginx/sites-available/vkuswill-bot
  log "Конфиг установлен из ${CONF_SOURCE}"
else
  warn "nginx-bot.conf не найден рядом со скриптом, генерируем встроенный конфиг..."

  cat > /etc/nginx/sites-available/vkuswill-bot << NGINX_PROD_EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\\\$host\\\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;

    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    location /webhook {
        proxy_pass http://127.0.0.1:8080/webhook;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 60s;
    }

    location /health {
        proxy_pass http://127.0.0.1:8080/health;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
    }

    location /langfuse/ {
        proxy_pass http://127.0.0.1:3000/;
        proxy_set_header Host \\\$host;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }

    location /mcp {
        proxy_pass http://127.0.0.1:8081;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host 127.0.0.1:8081;
        proxy_set_header X-Real-IP \\\$remote_addr;
        proxy_set_header X-Forwarded-For \\\$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \\\$scheme;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    location / {
        return 404;
    }
}
NGINX_PROD_EOF
fi

# Проверить и перезапустить nginx
nginx -t
systemctl restart nginx
log "nginx перезапущен с SSL"

# ─── 7. Автообновление сертификата ─────────────────────────
log "Настройка автообновления сертификата..."
# certbot автоматически создаёт таймер systemd
systemctl enable certbot.timer 2>/dev/null || true
systemctl start certbot.timer 2>/dev/null || true

# Добавляем reload nginx после обновления
RENEW_HOOK="/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh"
cat > "$RENEW_HOOK" << 'HOOK_EOF'
#!/bin/bash
systemctl reload nginx
HOOK_EOF
chmod +x "$RENEW_HOOK"

log "Автообновление настроено (certbot timer + nginx reload)"

# ─── 8. Открытие портов в iptables (если есть) ─────────────
if command -v iptables &>/dev/null; then
  iptables -C INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || \
    iptables -A INPUT -p tcp --dport 80 -j ACCEPT
  iptables -C INPUT -p tcp --dport 443 -j ACCEPT 2>/dev/null || \
    iptables -A INPUT -p tcp --dport 443 -j ACCEPT
  log "Порты 80 и 443 открыты в iptables"
fi

# ─── 9. Итог ───────────────────────────────────────────────
log "════════════════════════════════════════"
log "SSL настройка завершена!"
log "Домен:      ${DOMAIN}"
log "Сертификат: /etc/letsencrypt/live/${DOMAIN}/"
log "Nginx:      /etc/nginx/sites-available/vkuswill-bot"
log "Webhook:    https://${DOMAIN}/webhook"
log "Health:     https://${DOMAIN}/health"
log "════════════════════════════════════════"
log ""
log "Следующие шаги:"
log "  1. Убедитесь, что в Yandex Cloud Security Group открыты порты 80 и 443"
log "  2. Обновите GitHub Secret WEBHOOK_HOST:"
log "     gh secret set WEBHOOK_HOST -b \"${DOMAIN}\""
log "  3. Запустите CD pipeline для передеплоя бота"
