#!/usr/bin/env bash
# Установка GitHub Actions self-hosted runner на Raspberry Pi (ARM64) для workflow CD Raspberry Pi.
# Запуск на Pi из любого каталога:
#   curl -fsSL .../pi-install-github-runner.sh | bash   # или скопируйте из репозитория
#   bash scripts/pi-install-github-runner.sh
#
# Неинтерактивная регистрация (токен ~1 ч с GitHub: Settings → Actions → Runners → New self-hosted runner):
#   export RUNNER_REGISTRATION_TOKEN="xxxxxxxx"
#   export RUNNER_REPO_URL="https://github.com/OWNER/REPO"   # или OWNER/REPO
#   bash scripts/pi-install-github-runner.sh
#
# После config (вручную или через env): сервис
#   cd "$ACTIONS_RUNNER_DIR" && ./svc.sh install && ./svc.sh start
#
# Переменные:
#   ACTIONS_RUNNER_DIR  — каталог установки (по умолчанию ~/actions-runner-vkuswill)
#   RUNNER_NAME         — имя runner на GitHub (по умолчанию hostname)
#   RUNNER_LABELS       — доп. метки через запятую (всегда добавляется vkuswill-pi)
#   RUNNER_INSTALL_SERVICE=1 — после config выполнить svc.sh install && start (нужен sudo)

set -euo pipefail

log() { echo "[pi-install-runner] $*"; }
err() { echo "[pi-install-runner] ERROR: $*" >&2; exit 1; }

ARCH="$(uname -m)"
[[ "$ARCH" == "aarch64" ]] || log "Предупреждение: ожидался aarch64 (ARM64), сейчас $ARCH"

if [[ "${EUID:-}" -eq 0 ]]; then
  SUDO=""
else
  command -v sudo >/dev/null || err "Нужен sudo (для installdependencies.sh и опционально svc.sh)"
  SUDO="sudo"
fi

command -v curl >/dev/null || err "Установите curl"
command -v tar >/dev/null || err "Установите tar"
command -v python3 >/dev/null || err "Установите python3"

ACTIONS_RUNNER_DIR="${ACTIONS_RUNNER_DIR:-${HOME}/actions-runner-vkuswill}"
RUNNER_LABELS="${RUNNER_LABELS:-}"
STANDARD_LABELS="vkuswill-pi"
if [[ -n "$RUNNER_LABELS" ]]; then
  LABEL_ARG="${STANDARD_LABELS},${RUNNER_LABELS}"
else
  LABEL_ARG="${STANDARD_LABELS}"
fi
RUNNER_NAME="${RUNNER_NAME:-$(hostname -s)}"

log "каталог runner: ${ACTIONS_RUNNER_DIR}"
mkdir -p "${ACTIONS_RUNNER_DIR}"
cd "${ACTIONS_RUNNER_DIR}"

log "получаю URL последнего actions-runner-linux-arm64..."
DL_URL="$(
  python3 <<'PY'
import json
import urllib.request

url = "https://api.github.com/repos/actions/runner/releases/latest"
with urllib.request.urlopen(url, timeout=60) as r:
    data = json.load(r)
for a in data.get("assets") or []:
    name = a.get("name") or ""
    if name.startswith("actions-runner-linux-arm64-") and name.endswith(".tar.gz"):
        print(a["browser_download_url"])
        break
else:
    raise SystemExit("нет arm64 .tar.gz в последнем релизе actions/runner")
PY
)"

log "скачивание: ${DL_URL}"
TMP_TAR="$(mktemp)"
curl -fsSL -o "$TMP_TAR" "$DL_URL"
tar xzf "$TMP_TAR"
rm -f "$TMP_TAR"

if [[ -x ./bin/installdependencies.sh ]]; then
  log "зависимости runner (installdependencies.sh)..."
  $SUDO ./bin/installdependencies.sh || log "installdependencies.sh завершился с ошибкой — проверьте пакеты вручную"
fi

normalize_repo_url() {
  local raw="${1:-}"
  raw="${raw#"${raw%%[![:space:]]*}"}"
  raw="${raw%"${raw##*[![:space:]]}"}"
  [[ -n "$raw" ]] || { echo ""; return 0; }
  if [[ "$raw" != *"://"* ]]; then
    raw="https://github.com/${raw#github.com/}"
  fi
  echo "$raw"
}

REPO_URL="$(normalize_repo_url "${RUNNER_REPO_URL:-${GITHUB_REPOSITORY:-}}")"
if [[ -n "${GITHUB_REPOSITORY:-}" && "$REPO_URL" != *github.com* ]]; then
  REPO_URL="https://github.com/${GITHUB_REPOSITORY}"
fi

if [[ -n "${RUNNER_REGISTRATION_TOKEN:-}" && -n "$REPO_URL" ]]; then
  log "регистрация runner (unattended), labels: ${LABEL_ARG}"
  ./config.sh --url "$REPO_URL" --token "$RUNNER_REGISTRATION_TOKEN" --name "$RUNNER_NAME" \
    --labels "$LABEL_ARG" --unattended --replace
  if [[ "${RUNNER_INSTALL_SERVICE:-0}" == "1" ]]; then
    log "установка systemd-сервиса..."
    $SUDO ./svc.sh install
    $SUDO ./svc.sh start
    log "сервис запущен: ./svc.sh status"
  else
    log "сервис не ставился. Выполните: cd $(printf '%q' "${ACTIONS_RUNNER_DIR}") && ./svc.sh install && ./svc.sh start"
  fi
else
  log "архив распакован в: ${ACTIONS_RUNNER_DIR}"
  echo ""
  echo "Дальше вручную:"
  echo "  1. GitHub → репозиторий → Settings → Actions → Runners → New self-hosted runner → Linux / ARM64"
  echo "  2. Скопируйте одноразовый token и URL репозитория"
  echo "  3. Выполните:"
  echo "       cd ${ACTIONS_RUNNER_DIR}"
  echo "       ./config.sh --url https://github.com/OWNER/REPO --token TOKEN --labels ${LABEL_ARG} --unattended"
  echo "       ./svc.sh install && ./svc.sh start"
  echo ""
  echo "Или повторите этот скрипт с переменными RUNNER_REGISTRATION_TOKEN и RUNNER_REPO_URL (и при желании RUNNER_INSTALL_SERVICE=1)."
fi

log "готово."
