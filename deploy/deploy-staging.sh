#!/usr/bin/env bash
# ============================================================
# Staging deploy wrapper для VkusVill Bot
# Жестко фиксирует staging-ресурсы и запускает общий deploy.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

readonly STG_DEPLOY_ROOT="/opt/vkuswill-bot-stg"
readonly STG_CONTAINER_NAME="vkuswill-bot-stg"
readonly STG_MCP_CONTAINER_NAME="vkuswill-mcp-server-stg"
readonly STG_LANGFUSE_CONTAINER_NAME="vkuswill-langfuse-stg"
readonly STG_METABASE_CONTAINER_NAME="vkuswill-metabase-stg"
readonly STG_HEALTH_PORT="18080"
readonly STG_MCP_DEFAULT_PORT="18081"

fail() {
  echo "[deploy-staging] ERROR: $*" >&2
  exit 1
}

validate_staging_config() {
  [[ "${STG_DEPLOY_ROOT}" == *-stg ]] || fail "STG_DEPLOY_ROOT must end with -stg"
  [[ "${STG_CONTAINER_NAME}" == *-stg ]] || fail "STG_CONTAINER_NAME must end with -stg"
  [[ "${STG_MCP_CONTAINER_NAME}" == *-stg ]] || fail "STG_MCP_CONTAINER_NAME must end with -stg"
  [[ "${STG_LANGFUSE_CONTAINER_NAME}" == *-stg ]] || fail "STG_LANGFUSE_CONTAINER_NAME must end with -stg"
  [[ "${STG_METABASE_CONTAINER_NAME}" == *-stg ]] || fail "STG_METABASE_CONTAINER_NAME must end with -stg"
}

print_staging_config() {
  cat <<EOF
[deploy-staging] DEPLOY_ROOT=${STG_DEPLOY_ROOT}
[deploy-staging] CONTAINER_NAME=${STG_CONTAINER_NAME}
[deploy-staging] MCP_CONTAINER_NAME=${STG_MCP_CONTAINER_NAME}
[deploy-staging] LANGFUSE_CONTAINER_NAME=${STG_LANGFUSE_CONTAINER_NAME}
[deploy-staging] METABASE_CONTAINER_NAME=${STG_METABASE_CONTAINER_NAME}
[deploy-staging] HEALTH_PORT=${STG_HEALTH_PORT}
[deploy-staging] MCP_DEFAULT_PORT=${STG_MCP_DEFAULT_PORT}
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

validate_staging_config
print_staging_config

if [[ "${VALIDATE_ONLY}" == "true" ]]; then
  exit 0
fi

export DEPLOY_ROOT="${STG_DEPLOY_ROOT}"
export CONTAINER_NAME="${STG_CONTAINER_NAME}"
export MCP_CONTAINER_NAME="${STG_MCP_CONTAINER_NAME}"
export LANGFUSE_CONTAINER_NAME="${STG_LANGFUSE_CONTAINER_NAME}"
export METABASE_CONTAINER_NAME="${STG_METABASE_CONTAINER_NAME}"
export HEALTH_PORT="${STG_HEALTH_PORT}"
export MCP_DEFAULT_PORT="${STG_MCP_DEFAULT_PORT}"

# Staging-специфичные переменные окружения, которые deploy.sh
# добавит к docker run через DEPLOY_EXTRA_ENV (если Lockbox не содержит их)
export DEPLOY_EXTRA_ENV="\
-e PROMPT_LABEL=staging \
-e PROMPT_CACHE_TTL_SECONDS=30 \
-e LLM_PROMPT_PROFILES_ENABLED=true \
-e LLM_COMPACT_FOLLOWUP_PROMPT_ENABLED=true"

exec bash "${SCRIPT_DIR}/deploy.sh" "${FORWARD_ARGS[@]}"
