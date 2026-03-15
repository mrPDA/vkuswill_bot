#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

ERRORS=0
WARNINGS=0

ok()      { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "  ${YELLOW}[WARN]${NC} $1"; WARNINGS=$((WARNINGS + 1)); }
fail()    { echo -e "  ${RED}[FAIL]${NC} $1"; ERRORS=$((ERRORS + 1)); }
section() { echo -e "\n${CYAN}── $1 ──${NC}"; }

echo "=== VkusVill Bot — Agent Config Check ==="

# ── 1. AGENTS.md ──
section "AGENTS.md"
if [[ -f "AGENTS.md" ]]; then
  ok "AGENTS.md exists"
  grep -q "notesforllm" AGENTS.md 2>/dev/null && ok "mentions notesforllm" || fail "does not mention notesforllm"
  grep -q "Workbench" AGENTS.md 2>/dev/null && ok "mentions Git/MCP Workbench" || fail "does not mention Git/MCP Workbench"
  grep -q "task_id" AGENTS.md 2>/dev/null && ok "defines task_id convention" || fail "does not define task_id convention"
else
  fail "AGENTS.md not found"
fi

# ── 2. .env ──
section "Environment (.env)"

is_uuid() {
  [[ "$1" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]]
}

read_env_var() {
  local var_name="$1"
  local env_file="${2:-.env}"
  if [[ ! -f "$env_file" ]]; then
    return 1
  fi

  python3 - "$var_name" "$env_file" <<'PY'
import sys
from pathlib import Path

var_name, env_file = sys.argv[1], sys.argv[2]
for raw_line in Path(env_file).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key == var_name:
        print(value.strip())
        sys.exit(0)
sys.exit(1)
PY
}

if [[ -f ".env" ]]; then
  ok ".env exists"

  check_env_var() {
    local var_name="$1"
    local required="${2:-true}"
    local validate_uuid="${3:-false}"
    if grep -q "^${var_name}=" .env 2>/dev/null; then
      local val
      val=$(grep "^${var_name}=" .env | head -1 | cut -d'=' -f2-)
      if [[ -n "$val" ]]; then
        ok "${var_name} is set"
        if [[ "$validate_uuid" == "true" ]]; then
          if is_uuid "$val"; then
            ok "${var_name} is a valid UUID"
          else
            fail "${var_name} value is not a valid UUID: ${val}"
          fi
        fi
      elif [[ "$required" == "true" ]]; then
        fail "${var_name} is empty"
      else
        warn "${var_name} is empty (optional)"
      fi
    elif [[ "$required" == "true" ]]; then
      fail "${var_name} not found in .env"
    else
      warn "${var_name} not found in .env (optional)"
    fi
  }

  check_env_var "NOTESFORLLM_SPACE_ID" "true" "true"
  check_env_var "NOTESFORLLM_VERIFICATION_SPACE_ID" "true" "true"
  check_env_var "NOTESFORLLM_AGENT_ID"
  check_env_var "TARGET_REPO_SLUG" "false"
else
  fail ".env not found (copy from .env.example)"
fi

# ── 3. Workbench IDE config (local, static) ──
section "Workbench IDE config (local static checks)"

MCP_JSON=$(read_env_var "WORKBENCH_MCP_JSON_PATH" ".env" 2>/dev/null || true)
MCP_JSON="${MCP_JSON:-.cursor/mcp.json}"
WORKBENCH_REPO_PATH_ENV=$(read_env_var "WORKBENCH_REPO_PATH" ".env" 2>/dev/null || true)

if [[ -f "$MCP_JSON" ]]; then
  ok "$MCP_JSON exists"
  if command -v python3 &>/dev/null; then
    if python3 -c "import json; json.load(open('$MCP_JSON'))" 2>/dev/null; then
      ok "$MCP_JSON is valid JSON"
    else
      fail "$MCP_JSON is not valid JSON"
    fi

    if python3 -c "import json,sys; d=json.load(open('$MCP_JSON')); sys.exit(0 if 'git_mcp_workbench' in d.get('mcpServers',{}) else 1)" 2>/dev/null; then
      ok "git_mcp_workbench is configured in mcp.json"

      WORKBENCH_DIR_FROM_MCP=$(python3 -c "
import json, sys
d = json.load(open('$MCP_JSON'))
wb = d.get('mcpServers', {}).get('git_mcp_workbench', {})
args = wb.get('args', [])
for i, a in enumerate(args):
    if a == '--directory' and i + 1 < len(args):
        print(args[i + 1])
        sys.exit(0)
sys.exit(1)
" 2>/dev/null) && {
        if [[ -d "$WORKBENCH_DIR_FROM_MCP" ]]; then
          ok "Workbench repo path from mcp.json exists: $WORKBENCH_DIR_FROM_MCP"
        else
          warn "Workbench repo path from mcp.json does not exist: $WORKBENCH_DIR_FROM_MCP"
        fi
      } || warn "Could not extract Workbench repo path from mcp.json"

      if [[ -n "$WORKBENCH_REPO_PATH_ENV" ]]; then
        if [[ -d "$WORKBENCH_REPO_PATH_ENV" ]]; then
          ok "Workbench repo path from .env exists: $WORKBENCH_REPO_PATH_ENV"
        else
          warn "Workbench repo path from .env does not exist: $WORKBENCH_REPO_PATH_ENV"
        fi

        if [[ -n "${WORKBENCH_DIR_FROM_MCP:-}" && "$WORKBENCH_DIR_FROM_MCP" != "$WORKBENCH_REPO_PATH_ENV" ]]; then
          warn "Workbench repo path differs between .env and mcp.json"
        fi
      else
        warn "WORKBENCH_REPO_PATH not set in .env (optional helper for local checks)"
      fi
    else
      warn "git_mcp_workbench is NOT in mcp.json (see workbench-mcp.json.example)"
    fi
  fi
else
  warn "$MCP_JSON not found — Workbench local setup not detected (see workbench-mcp.json.example)"
fi

if [[ -f "workbench-mcp.json.example" ]]; then
  ok "workbench-mcp.json.example template exists (shareable)"
else
  warn "workbench-mcp.json.example not found — no shareable Workbench template"
fi

# ── 4. notesforllm storage check ──
section "notesforllm storage"

NOTES_DB="$HOME/.notesforllm/data.db"
if [[ -f "$NOTES_DB" ]]; then
  ok "notesforllm database exists ($NOTES_DB)"
else
  warn "notesforllm database not found at $NOTES_DB (may use different storage or first run)"
fi

if command -v uvx &>/dev/null; then
  ok "uvx command available"
elif command -v uv &>/dev/null; then
  ok "uv command available"
else
  fail "neither uv nor uvx found — cannot run MCP servers"
fi

if [[ -f ".env" ]]; then
  SPACE_ID=$(grep "^NOTESFORLLM_SPACE_ID=" .env 2>/dev/null | head -1 | cut -d'=' -f2-)
  VER_SPACE_ID=$(grep "^NOTESFORLLM_VERIFICATION_SPACE_ID=" .env 2>/dev/null | head -1 | cut -d'=' -f2-)

  if [[ -n "$SPACE_ID" ]] && [[ -f "$NOTES_DB" ]] && command -v python3 &>/dev/null; then
    SPACE_EXISTS=$(python3 -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('$NOTES_DB')
    rows = conn.execute('SELECT id, name FROM spaces WHERE id = ?', ('$SPACE_ID',)).fetchall()
    conn.close()
    if rows:
        print(rows[0][1])
    else:
        sys.exit(1)
except Exception:
    sys.exit(2)
" 2>/dev/null) && ok "Product space found in DB: $SPACE_EXISTS" || warn "Product space $SPACE_ID not found in local DB"
  fi

  if [[ -n "$VER_SPACE_ID" ]] && [[ -f "$NOTES_DB" ]] && command -v python3 &>/dev/null; then
    VER_SPACE_EXISTS=$(python3 -c "
import sqlite3, sys
try:
    conn = sqlite3.connect('$NOTES_DB')
    rows = conn.execute('SELECT id, name FROM spaces WHERE id = ?', ('$VER_SPACE_ID',)).fetchall()
    conn.close()
    if rows:
        print(rows[0][1])
    else:
        sys.exit(1)
except Exception:
    sys.exit(2)
" 2>/dev/null) && ok "Verification space found in DB: $VER_SPACE_EXISTS" || warn "Verification space $VER_SPACE_ID not found in local DB"
  fi
fi

# ── 5. Branch-to-task binding ──
section "Branch-to-task binding"

if [[ -f ".workbench/task-branches.json" ]]; then
  ok ".workbench/task-branches.json exists"
  if command -v python3 &>/dev/null; then
    BINDING_COUNT=$(python3 -c "
import json, sys
try:
    d = json.load(open('.workbench/task-branches.json'))
    bindings = d.get('bindings', d.get('branches', {}))
    print(len(bindings))
except Exception:
    sys.exit(1)
" 2>/dev/null) && {
      if [[ "$BINDING_COUNT" -gt 0 ]]; then
        ok "$BINDING_COUNT active branch binding(s)"
      else
        warn "No branch bindings yet (run: bash scripts/bind-task-branch.sh <task_id>)"
      fi
    } || warn "Could not parse task-branches.json"
  fi
else
  warn ".workbench/task-branches.json not found"
fi

# ── 6. Onboarding docs ──
section "Onboarding docs"
for doc in "docs/integration-setup.md" "docs/agent-kickoff.md" "docs/TEAM_HISPANIOLA.md" "docs/ARCHITECTURE.md"; do
  if [[ -f "$doc" ]]; then
    ok "$doc exists"
  else
    warn "$doc not found"
  fi
done

# ── 7. Git ──
section "Git"
if git rev-parse --git-dir > /dev/null 2>&1; then
  ok "Git repository detected"
  BRANCH=$(git branch --show-current 2>/dev/null || echo "detached")
  ok "Current branch: ${BRANCH}"
else
  fail "Not a git repository"
fi

# ── Results ──
echo ""
echo "=== Results ==="
echo -e "Errors: ${RED}${ERRORS}${NC}  Warnings: ${YELLOW}${WARNINGS}${NC}"

if [[ $ERRORS -gt 0 ]]; then
  echo -e "${RED}Fix errors before starting agent workflow.${NC}"
  exit 1
else
  echo -e "${GREEN}Agent config OK. Ready to work.${NC}"
  exit 0
fi
