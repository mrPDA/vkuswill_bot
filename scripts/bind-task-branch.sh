#!/usr/bin/env bash
#
# Bind current git branch to a task_id in .workbench/task-branches.json
#
# Usage:
#   bash scripts/bind-task-branch.sh <task_id>
#   bash scripts/bind-task-branch.sh                 # auto-resolve from branch name
#   bash scripts/bind-task-branch.sh --show          # show current binding
#   bash scripts/bind-task-branch.sh --list          # list all bindings
#
set -euo pipefail

BINDINGS_FILE=".workbench/task-branches.json"

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

resolve_repo_slug() {
  if [[ -n "${TARGET_REPO_SLUG:-}" ]]; then
    echo "$TARGET_REPO_SLUG"
    return
  fi

  local env_slug=""
  env_slug=$(read_env_var "TARGET_REPO_SLUG" ".env" 2>/dev/null || true)
  if [[ -n "$env_slug" ]]; then
    echo "$env_slug"
    return
  fi

  local repo_root=""
  repo_root=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
  basename "$repo_root"
}

REPO_SLUG=$(resolve_repo_slug)

ensure_file() {
  if [[ ! -f "$BINDINGS_FILE" ]]; then
    mkdir -p "$(dirname "$BINDINGS_FILE")"
    cat > "$BINDINGS_FILE" <<'SEED'
{
  "bindings": {},
  "updated_at": null,
  "version": 1
}
SEED
  fi
}

get_branch() {
  git branch --show-current 2>/dev/null || echo ""
}

auto_task_id() {
  local branch="$1"
  if [[ -z "$branch" || "$branch" == "main" || "$branch" == "master" ]]; then
    echo ""
    return
  fi
  echo "${REPO_SLUG}:${branch}"
}

show_binding() {
  ensure_file
  local branch
  branch=$(get_branch)
  if [[ -z "$branch" ]]; then
    echo "Not on a branch (detached HEAD)."
    exit 1
  fi
  python3 -c "
import json, sys
d = json.load(open('$BINDINGS_FILE'))
b = d.get('bindings', {}).get('$branch')
if b:
    print(f'Branch: $branch -> task_id: {b[\"task_id\"]}  (bound at {b[\"updated_at\"]})')
else:
    print(f'Branch: $branch -> no binding')
    sys.exit(1)
"
}

list_bindings() {
  ensure_file
  python3 -c "
import json
d = json.load(open('$BINDINGS_FILE'))
bindings = d.get('bindings', {})
if not bindings:
    print('No bindings.')
else:
    for branch, info in sorted(bindings.items()):
        print(f'  {branch} -> {info[\"task_id\"]}')
"
}

bind() {
  local task_id="$1"
  local branch
  branch=$(get_branch)
  if [[ -z "$branch" ]]; then
    echo "Error: not on a branch (detached HEAD)."
    exit 1
  fi

  ensure_file

  python3 -c "
import json
from datetime import datetime, timezone

path = '$BINDINGS_FILE'
d = json.load(open(path))
now = datetime.now(timezone.utc).isoformat()
d.setdefault('bindings', {})
d['bindings']['$branch'] = {
    'task_id': '$task_id',
    'updated_at': now
}
d['updated_at'] = now
d['version'] = d.get('version', 0) + 1
json.dump(d, open(path, 'w'), indent=2, ensure_ascii=False)
print(f'Bound: $branch -> $task_id')
"
}

case "${1:-}" in
  --show)
    show_binding
    ;;
  --list)
    list_bindings
    ;;
  "")
    branch=$(get_branch)
    task_id=$(auto_task_id "$branch")
    if [[ -z "$task_id" ]]; then
      echo "Cannot auto-resolve task_id for branch '$branch'."
      echo "Usage: bash scripts/bind-task-branch.sh <task_id>"
      exit 1
    fi
    bind "$task_id"
    ;;
  *)
    bind "$1"
    ;;
esac
