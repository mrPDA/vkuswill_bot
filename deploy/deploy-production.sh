#!/usr/bin/env bash
# ============================================================
# Production deploy wrapper для VkusVill Bot
# Жестко фиксирует production-ресурсы и запускает общий deploy.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

readonly PROD_DEPLOY_ROOT="/opt/vkuswill-bot"
readonly PROD_CONTAINER_NAME="vkuswill-bot"
readonly PROD_MCP_CONTAINER_NAME="vkuswill-mcp-server"
readonly PROD_LANGFUSE_CONTAINER_NAME="vkuswill-langfuse"
readonly PROD_METABASE_CONTAINER_NAME="vkuswill-metabase"
readonly PROD_HEALTH_PORT="8080"
readonly PROD_MCP_DEFAULT_PORT="8081"
readonly PROD_LANGFUSE_PORT="3000"
readonly PROD_METABASE_PORT="3001"

fail() {
  echo "[deploy-production] ERROR: $*" >&2
  exit 1
}

validate_production_config() {
  [[ "${PROD_DEPLOY_ROOT}" == "/opt/vkuswill-bot" ]] || fail "PROD_DEPLOY_ROOT must be /opt/vkuswill-bot"
  [[ "${PROD_CONTAINER_NAME}" == "vkuswill-bot" ]] || fail "PROD_CONTAINER_NAME must be vkuswill-bot"
  [[ "${PROD_CONTAINER_NAME}" != *-stg ]] || fail "PROD_CONTAINER_NAME must NOT end with -stg"
  [[ "${PROD_MCP_CONTAINER_NAME}" != *-stg ]] || fail "PROD_MCP_CONTAINER_NAME must NOT end with -stg"
}

print_production_config() {
  cat <<EOF
[deploy-production] DEPLOY_ROOT=${PROD_DEPLOY_ROOT}
[deploy-production] CONTAINER_NAME=${PROD_CONTAINER_NAME}
[deploy-production] MCP_CONTAINER_NAME=${PROD_MCP_CONTAINER_NAME}
[deploy-production] LANGFUSE_CONTAINER_NAME=${PROD_LANGFUSE_CONTAINER_NAME}
[deploy-production] METABASE_CONTAINER_NAME=${PROD_METABASE_CONTAINER_NAME}
[deploy-production] HEALTH_PORT=${PROD_HEALTH_PORT}
[deploy-production] MCP_DEFAULT_PORT=${PROD_MCP_DEFAULT_PORT}
[deploy-production] LANGFUSE_PORT=${PROD_LANGFUSE_PORT}
[deploy-production] METABASE_PORT=${PROD_METABASE_PORT}
EOF
}

VALIDATE_ONLY=false
FORWARD_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--validate-only" ]]; then
    VALIDATE_ONLY=true
    continue
  fi
  FORWARD_ARGS+=("$arg")
done

validate_production_config
print_production_config

if [[ "${VALIDATE_ONLY}" == "true" ]]; then
  exit 0
fi

export DEPLOY_ROOT="${PROD_DEPLOY_ROOT}"
export CONTAINER_NAME="${PROD_CONTAINER_NAME}"
export MCP_CONTAINER_NAME="${PROD_MCP_CONTAINER_NAME}"
export LANGFUSE_CONTAINER_NAME="${PROD_LANGFUSE_CONTAINER_NAME}"
export METABASE_CONTAINER_NAME="${PROD_METABASE_CONTAINER_NAME}"
export HEALTH_PORT="${PROD_HEALTH_PORT}"
export MCP_DEFAULT_PORT="${PROD_MCP_DEFAULT_PORT}"
export LANGFUSE_PORT="${PROD_LANGFUSE_PORT}"
export METABASE_PORT="${PROD_METABASE_PORT}"

# Production-специфичные переменные окружения
export DEPLOY_EXTRA_ENV="\
-e PROMPT_LABEL=production \
-e PROMPT_CACHE_TTL_SECONDS=300 \
-e LLM_PROMPT_PROFILES_ENABLED=true \
-e LLM_COMPACT_FOLLOWUP_PROMPT_ENABLED=true"

exec bash "${SCRIPT_DIR}/deploy.sh" "${FORWARD_ARGS[@]}"
