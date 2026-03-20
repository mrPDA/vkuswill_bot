#!/usr/bin/env bash
# ============================================================
# Деплой на Raspberry Pi: git checkout + docker compose (pi stack)
# Локально на Pi:  PI_COMPOSE_PROFILES=caddy bash deploy/deploy-pi.sh --root ~/vkuswill_bot --ref main
# Из CI: scp этот файл на Pi и вызвать с теми же аргументами.
# ============================================================
# Требования: клон репозитория, Docker, .env в корне репо.
# Для private GitHub: на Pi — deploy key (read-only) или credential helper.
# Профили compose: экспорт PI_COMPOSE_PROFILES=caddy и/или cf-tunnel (через запятую).
# ============================================================

set -euo pipefail

REPO_ROOT=""
REF=""
COMPOSE_FILE="docker-compose.pi.yml"

usage() {
  echo "Usage: $0 --root <path-to-repo> --ref <tag|branch|sha> [--compose-file docker-compose.pi.yml]" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root) REPO_ROOT="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    -h | --help) usage ;;
    *) echo "Unknown option: $1" >&2; usage ;;
  esac
done

[[ -n "$REPO_ROOT" && -n "$REF" ]] || usage
[[ -d "${REPO_ROOT}/.git" ]] || {
  echo "[deploy-pi] ERROR: не git-репозиторий: ${REPO_ROOT}" >&2
  exit 1
}

cd "$REPO_ROOT"
log() { echo "[deploy-pi] $*"; }

export GIT_TERMINAL_PROMPT=0

log "remote: $(git remote get-url origin 2>/dev/null || echo '?')"
log "fetch + checkout ${REF}"
git fetch origin --tags --prune
# Подтянуть ref с сервера (ветка/тег/SHA), если ещё нет локально
git fetch origin "${REF}" 2>/dev/null || true

if [[ "$(git rev-parse --is-shallow-repository 2>/dev/null)" == "true" ]]; then
  log "раскрываю shallow clone (нужен полный history для checkout)"
  git fetch origin --unshallow 2>/dev/null || git fetch origin --depth=500 2>/dev/null || true
fi

if ! git checkout -f "${REF}"; then
  log "повтор как тег refs/tags/${REF#refs/tags/}"
  if ! git checkout -f "refs/tags/${REF#refs/tags/}"; then
    log "повтор как origin/${REF}"
    git checkout -f "origin/${REF}" || {
      echo "[deploy-pi] ERROR: не удалось checkout ${REF} (проверьте fetch и deploy key)" >&2
      exit 1
    }
  fi
fi

log "HEAD: $(git rev-parse --short HEAD) ($(git describe --tags --always 2>/dev/null || true))"

profile_args=()
if [[ -n "${PI_COMPOSE_PROFILES:-}" ]]; then
  IFS=',' read -r -a _profs <<< "${PI_COMPOSE_PROFILES// /}"
  for p in "${_profs[@]}"; do
    [[ -n "${p}" ]] || continue
    profile_args+=(--profile "${p}")
  done
  log "profiles: ${PI_COMPOSE_PROFILES}"
else
  log "profiles: (none) — как в Makefile docker-up-pi"
fi

[[ -f "${COMPOSE_FILE}" ]] || {
  echo "[deploy-pi] ERROR: нет файла ${COMPOSE_FILE} в $(pwd)" >&2
  exit 1
}

log "docker compose up -d --build"
docker compose -f "${COMPOSE_FILE}" "${profile_args[@]}" up -d --build

log "стек:"
docker compose -f "${COMPOSE_FILE}" "${profile_args[@]}" ps

log "логи bot (последние 50 строк):"
docker compose -f "${COMPOSE_FILE}" "${profile_args[@]}" logs --tail 50 bot 2>/dev/null || true

log "готово."
