#!/usr/bin/env bash
# Создать deploy/amnezia-wg0.conf из Amnezia .vpn (запускать на Pi из корня репозитория).
# Пример: bash scripts/pi_apply_amnezia_vpn.sh ~/amnezia_config.vpn

set -euo pipefail
VPN_FILE="${1:?Укажите путь к файлу .vpn}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/deploy/amnezia-wg0.conf"
mkdir -p "${ROOT}/deploy"
python3 "${ROOT}/scripts/decode_amnezia_vpn.py" "$VPN_FILE" --awg-out "$OUT"
chmod 600 "$OUT"
echo "Готово: $OUT"
echo "Перезапуск: cd $ROOT && docker compose -f docker-compose.pi.yml up -d"
