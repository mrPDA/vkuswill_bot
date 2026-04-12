# AGENTS.md — vkuswill_bot

## Start here

- Repo: `readlink -f repo` — resolve before every action. Runtime over memory.
- Forbidden: `/home/denis/projects/vkuswill_bot`.

### Local services — use them, don't say "I don't have a tool"

| Service | Access |
|---------|--------|
| **Hub** | `/home/denis/.local/bin/oc-hub <cmd>` or `curl http://127.0.0.1:8080/api/...` |
| **n4l** | `/home/denis/.local/bin/n4l <tool>` |
| **dispatch** | `/home/denis/.local/bin/oc-dev-dispatch <cmd>` |

Hub essentials: `oc-hub list`, `oc-hub status <id>`, `oc-hub dashboard`, `oc-hub tree <id>`, `oc-hub context <id>`
Hub lifecycle: `oc-hub approve <id>`, `oc-hub start <id> --plan "..."`, `oc-hub question <id>`, `oc-hub answer <id>`
Hub hierarchy: `oc-hub epic`, `oc-hub feature --parent <epic_id>`, `oc-hub task --parent <feature_id>`, `oc-hub subtask --parent <task_id>`
List filters: `oc-hub list --type epic`, `oc-hub list --parent <id>`, `oc-hub list --priority high`

## Working rules

- Resolve repo: `readlink -f repo` then `git -C repo rev-parse --show-toplevel`.

### CRITICAL: Git rules

- **ЗАПРЕЩЕНО** делать `git commit`, `git push`, `git merge`, `git rebase`. Hub делает ВСЁ это автоматически. Просто вноси изменения в файлы.
- Hub создаёт ветку `task-<id>/<slug>` автоматически. Проверь текущую ветку: `git branch --show-current`.
- Hub автоматически: коммитит → squash → push → создаёт PR → проверяет CI → отправляет на ревью → мержит.

### CRITICAL: File editing rules

- **ЗАПРЕЩЕНО** использовать `write` tool для перезаписи существующих файлов целиком. Используй `edit` (patch) для точечных правок.
- `write` допускается ТОЛЬКО для создания НОВЫХ файлов, которых ещё нет в репозитории.
- После каждого изменения файла проверь его валидность: `python -c "import ast; ast.parse(open('file.py').read())"`.
- Если файл стал невалидным — отмени изменения: `git checkout -- <file>` и попробуй заново через `edit`.

### Pre-completion checklist

**ОБЯЗАТЕЛЬНО перед завершением** — запусти и убедись, что всё проходит:
1. `uv run ruff check src/ tests/ --fix` — lint
2. `uv run ruff format src/ tests/` — форматирование
3. `uv run pytest tests/ -x -q` — тесты

Если есть ошибки lint/тестов — исправь их ДО завершения задачи. Hub проверяет CI автоматически и вернёт задачу если CI не проходит.

### CI checks troubleshooting

Hub автоматически проверяет CI после каждого push. Если CI не проходит — тебе придёт задание с логами ошибок. Справочник по исправлению:

| CI Check | Как исправить |
|----------|--------------|
| **Lint & Format** | `uv run ruff check src/ tests/ --fix && uv run ruff format src/ tests/` |
| **Tests** | `uv run pytest tests/ -x -q` — исправь упавшие тесты |
| **Security: pip-audit** | CVE в зависимостях → `uv lock --upgrade-package <имя_пакета>` (имя и версия в логе) |
| **Security: bandit** | SAST-замечания → исправь код по рекомендациям |
| **Security: gitleaks** | Секреты в коде → вынеси в переменные окружения |
| **Validate commits** | Hub коммитит сам, тебе НЕ нужно коммитить. Если ошибка — не делай git commit. |
| **Architecture gate** | `uv run pytest -q tests/test_agents_architecture.py tests/test_services_architecture.py tests/test_shopping_contract_flows.py` |

### General

- Keep token spend low: targeted reads, narrow grep, focused tests.
- Use `.venv/bin/pytest` or `uv run pytest` — never `python -m pytest`.
- One tool call per turn. After editing, validate first, then accept.
- If tool call `terminated` with partial args, retry once.
- Don't end tasks with plan when execution was requested.

## Roles

- **main**: resolve repo, delegate via Hub. Use `oc-hub propose` for new work.
- **python-senior-developer**: minimal edits, validate from repo root, report changes.
- **code-reviewer**: findings from repo root only.
- **testing-agent**: focused checks from repo root.
- **appsec-agent / secops-agent**: inspect repo root; cite files/commands.

## Hub task updates

When working on a task, write updates via `oc-hub update <task_id>`:
- Start: `--kind status --message "Plan: <approach> (chose over <alternative> because <reason>)"`
- Milestone: `--kind status --message "Done X (approach: <what and why>), moving to Y"`
- Blocker: `--kind blocker --message "Blocked by: <issue>. Tried: <what>. Need: <what>"`
- Question: `oc-hub question <task_id> --message "..." --agent "<role>"` — task pauses
- Finish: `--kind done --message "Changed: <files>. Approach: <why this way>. Validation: <how tested>"`
- Extra work needed? `oc-hub propose ...` — don't start it, propose first.

**Every update MUST include rationale** — not just "done X, moving to Y" but WHY this approach was chosen. For code-reviewer: include severity (high/medium/low) and reasoning for each finding.

### Lifecycle enforcement

Hub enforces two rules at the data level:
1. **Plan before start**: `oc-hub start <id>` requires `--plan "..."` or a prior `Plan:` update. Without it, start fails with 400.
2. **Done report before completion**: When your task finishes, Hub sets `pending_report` instead of `completed` until you submit `--kind done --message "..."`. If you don't report — the task stays in inbox.
3. **Stale detection**: Tasks with no updates for 30+ min get an automatic alert. Keep updating.

### Hierarchy context

Before starting work on a task, get its context: `oc-hub context <task_id>`. This shows:
- Breadcrumb path (epic > feature > task)
- Sibling tasks (to understand scope)
- Children and progress (for parent tasks)

Use this context in your updates and when making architectural decisions.

For n4l integration, use `task_id: "hub:<id>"` convention, e.g. `n4l checkpoint --task_id "hub:42"`.

Full Hub reference: read `HUB.md`.

## notesforllm

For multi-session work only. Details: read `NOTESFORLLM.md`.

Core commands:
- `n4l resume` — at start of continuing tasks.
- `n4l checkpoint` — after validated milestones.
- `n4l decision` — for durable choices.
- `n4l handoff` — when work stops unfinished.

Analysis commands:
- `n4l timeline` — chronological history of a task (use before resuming complex tasks).
- `n4l compare` — diff two checkpoints or pages.
- `n4l test_run` — structured test results with pass/fail counts (`testing-agent`).
- `n4l search` — text search across all pages.
- `n4l query` — structured query with filters (kind, workflow, stage, etc.).

Structured fields (optional, use on multi-session tasks):
- `provenance`: `{repo, branch, head_sha, environment}` — where work happened.
- `operational`: `{last_good_state, exact_next_command, files_in_play, unresolved_risks}` — bootstrap for next session.
- `bridge`: `{role, target_page_id}` — link verification → fix → follow-up pages.

## Reference files (read on demand)

- `HUB.md` — Hub CLI, API, lifecycle, Q&A, proposal workflow
- `TOOLS.md` — paths, commands, acceptance helpers
- `NOTESFORLLM.md` — n4l commands and templates
- `BOOTSTRAP.md` — core path discipline
