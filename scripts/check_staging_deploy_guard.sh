#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAGING_SCRIPT="${ROOT_DIR}/deploy/deploy-staging.sh"
STAGING_WORKFLOW="${ROOT_DIR}/.github/workflows/cd-staging.yml"

fail() {
  echo "[staging-guard] ERROR: $*" >&2
  exit 1
}

require_pattern() {
  local file="$1"
  local pattern="$2"
  local label="$3"
  if ! grep -Eq "$pattern" "$file"; then
    fail "${label} not found in ${file}"
  fi
}

[[ -f "${STAGING_SCRIPT}" ]] || fail "Missing deploy/deploy-staging.sh"

require_pattern "${STAGING_SCRIPT}" 'STG_DEPLOY_ROOT="/opt/vkuswill-bot-stg"' "staging deploy dir"
require_pattern "${STAGING_SCRIPT}" 'STG_CONTAINER_NAME="vkuswill-bot-stg"' "staging bot container"
require_pattern "${STAGING_SCRIPT}" 'STG_MCP_CONTAINER_NAME="vkuswill-mcp-server-stg"' "staging mcp container"
require_pattern "${STAGING_SCRIPT}" 'STG_HEALTH_PORT="18080"' "staging health port"
require_pattern "${STAGING_SCRIPT}" 'STG_MCP_DEFAULT_PORT="18081"' "staging mcp port"
require_pattern "${STAGING_SCRIPT}" 'validate_staging_config' "staging runtime guard"
require_pattern "${STAGING_SCRIPT}" 'exec bash "\$\{SCRIPT_DIR\}/deploy\.sh"' "wrapper to shared deploy"

if [[ -f "${STAGING_WORKFLOW}" ]]; then
  require_pattern "${STAGING_WORKFLOW}" 'deploy-staging\.sh' "staging workflow uses deploy-staging.sh"
  require_pattern "${STAGING_WORKFLOW}" '/opt/vkuswill-bot-stg' "staging workflow deploy dir"
fi

echo "[staging-guard] OK: staging deploy guard is configured"
