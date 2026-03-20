#!/usr/bin/env bash
# Копирует файлы CD Pi / runner с локальной машины на Raspberry Pi по SSH.
# Требуется запись в ~/.ssh/config, например:
#   Host vkbot
#     HostName 192.168.x.x
#     User denis
#
# Использование (из корня репозитория):
#   bash scripts/sync_cd_files_to_pi.sh
# Переменные:
#   PI_SSH_HOST   — хост SSH (по умолчанию vkbot)
#   PI_REMOTE_DIR — каталог клона на Pi (по умолчанию ~/vkuswill_bot)

set -euo pipefail

HOST="${PI_SSH_HOST:-vkbot}"
# На стороне Pi ~ раскрывается удалённым shell (OpenSSH scp)
REMOTE="${PI_REMOTE_DIR:-~/vkuswill_bot}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

run_scp() {
  local dest="$1"
  shift
  scp "$@" "${HOST}:${dest}"
}

echo "[sync-cd-to-pi] -> ${HOST}:${REMOTE}"

run_scp "${REMOTE}/.github/workflows/" .github/workflows/cd-pi.yml .github/workflows/cd.yml
run_scp "${REMOTE}/deploy/" deploy/deploy-pi.sh
run_scp "${REMOTE}/scripts/" scripts/pi-install-github-runner.sh scripts/sync_cd_files_to_pi.sh
run_scp "${REMOTE}/docs/" docs/deploy-pi-self-hosted-runner.md
run_scp "${REMOTE}/" README.md CHANGELOG.md docker-compose.pi.yml Makefile

ssh -o BatchMode=yes "$HOST" "chmod +x ${REMOTE}/deploy/deploy-pi.sh ${REMOTE}/scripts/pi-install-github-runner.sh ${REMOTE}/scripts/sync_cd_files_to_pi.sh"

echo "[sync-cd-to-pi] готово."
