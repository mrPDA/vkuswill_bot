# Integration Setup — MCP-сервисы для агентов

Руководство по подключению `notesforllm` и `Git/MCP Workbench` к проекту VkusVill Bot.

---

## 1. notesforllm — persistent memory

### Что это

MCP-сервер для хранения checkpoints, decisions и handoffs между сессиями агентов. Позволяет восстановить контекст при старте новой сессии и не терять решения между чатами.

### Подключение

#### Cursor IDE

Добавить в настройки MCP (Settings → MCP Servers) или в `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "notesforllm": {
      "command": "uvx",
      "args": ["notesforllm", "--storage-dir", "~/.notesforllm"]
    }
  }
}
```

#### VS Code (Copilot)

Добавить в `.vscode/mcp.json`:

```json
{
  "servers": {
    "notesforllm": {
      "command": "uvx",
      "args": ["notesforllm", "--storage-dir", "~/.notesforllm"]
    }
  }
}
```

### Обязательные env-переменные

| Переменная | Описание | Пример |
|-----------|----------|--------|
| `NOTESFORLLM_SPACE_ID` | UUID product space | `a1b2c3d4-...` |
| `NOTESFORLLM_VERIFICATION_SPACE_ID` | UUID verification space | `e5f6a7b8-...` |
| `NOTESFORLLM_AGENT_ID` | Идентификатор агента | `vkuswill-bot-agent` |

### Создание spaces

При первом запуске нужно создать два space:

```
# Через MCP tool spaces_create:
Product space:    name="vkuswill_bot"
Verification space: name="vkuswill_bot-verification"
```

Записать полученные UUID в `.env` как `NOTESFORLLM_SPACE_ID` и `NOTESFORLLM_VERIFICATION_SPACE_ID`.

### MCP alias

В этом проекте alias — **`user-notesforllm`** (Cursor) или **`notesforllm`** (generic).

### Доступные инструменты

| Tool | Когда использовать |
|------|-------------------|
| `notes_resume_context` | Старт сессии — восстановить контекст по task_id |
| `notes_search` | Найти контекст, когда task_id неизвестен |
| `notes_list` | Список всех заметок в space |
| `notes_query` | Structured lookup по workflow, provenance и bridge metadata |
| `notes_compare` | Сравнить два workflow round вместо ручного diff Markdown |
| `notes_checkpoint_save` | После meaningful milestone |
| `notes_test_run_checkpoint` | Для recurring verification / smoke прогонов |
| `notes_decision_save` | При durable technical decision |
| `notes_handoff_save` | Конец сессии с незавершённой работой |
| `notes_task_timeline` | Полная история задачи через несколько сессий |
| `spaces_list` | Список доступных spaces |

### Operational-memory payload

Чтобы агенты реально использовали новый workflow, сохранять facts не только в prose, но и в structured payload:

- `provenance`: `repo`, `branch`, `head_sha`, `pr_number`, `run_id`, `run_url`, `environment`, `verification_scope`
- `operational`: `last_good_state`, `unresolved_risks`, `exact_next_command`, `files_in_play`, `known_issues`, `blocked_by`
- `bridge`: `role`, `target_page_ids`

Рекомендации для `vkuswill_bot`:

- product work: `provenance.repo="vkuswill_bot"`
- verification work: отдельный verification space и `task_id` формата `vkuswill_bot:verification/<topic>`
- recurring test loop: `notes_test_run_checkpoint(...)`
- bugfix loop: `verification_finding -> product_fix -> verification_follow_up` через `bridge`

### Routing hygiene

Перед любым `notes_checkpoint_save(...)`, `notes_decision_save(...)`, `notes_handoff_save(...)` или `notes_test_run_checkpoint(...)` агент должен проверить соответствие `task_id` и target space:

- если `task_id` содержит `:verification/`, использовать только `NOTESFORLLM_VERIFICATION_SPACE_ID`
- если target space относится к verification и его slug оканчивается на `-verification`, не писать туда обычный product `task_id`
- не делать silent reroute между product и verification spaces; сначала выбрать правильный `space_id`, а при mismatch падать явно

---

## 2. Git/MCP Workbench — Git-aware контекст

### Что это

MCP-сервер, предоставляющий Git-контекст через структурированные инструменты: статус, diff, коммиты, подготовка PR. Заменяет ad-hoc `git` shell-команды. Также проксирует `notesforllm` tools — restore, checkpoint, decision и handoff — привязанные к текущему репозиторию.

### Статус

> **Требует local IDE setup.** Каждый разработчик настраивает `.cursor/mcp.json` локально. Репозиторий хранит только переносимый шаблон `workbench-mcp.json.example`, поэтому нельзя считать Workbench "уже подключённым" для всех агентов.

### Настройка

1. Склонировать [Git/MCP Workbench](https://github.com/user/MCP_Workbench) на свою машину.
2. Скопировать `workbench-mcp.json.example` → `.cursor/mcp.json` (или merge в существующий).
3. Заменить плейсхолдеры `<PATH_TO_...>` на реальные пути.
4. Заполнить `NOTESFORLLM_SPACE_ID` и `NOTESFORLLM_VERIFICATION_SPACE_ID` из `.env`.
5. Перезапустить MCP-серверы: ⌘+Shift+P → Reload Window.

Опционально можно заполнить в `.env` helper-переменные `WORKBENCH_REPO_PATH` и `WORKBENCH_MCP_JSON_PATH`, чтобы `scripts/check-agent-config.sh` проверял именно твой локальный путь и конфиг. Эти переменные не подключают Workbench сами по себе.

### Шаблон (`workbench-mcp.json.example`)

```json
{
  "mcpServers": {
    "git_mcp_workbench": {
      "command": "uv",
      "args": [
        "run", "--directory", "<PATH_TO_MCP_WORKBENCH_REPO>",
        "python", "-m", "git_mcp_workbench.interfaces.mcp_server"
      ],
      "env": {
        "TARGET_REPO_PATH": "<PATH_TO_THIS_REPO>",
        "NOTESFORLLM_COMMAND": "uv",
        "NOTESFORLLM_ARGS": "run --directory <PATH_TO_NOTESFORLLM_REPO> --extra mcp python -m notesforllm.mcp_server.run",
        "NOTESFORLLM_SPACE_ID": "<PRODUCT_SPACE_UUID>",
        "NOTESFORLLM_VERIFICATION_SPACE_ID": "<VERIFICATION_SPACE_UUID>",
        "NOTESFORLLM_AGENT_ID": "vkuswill-bot-workbench"
      }
    }
  }
}
```

### MCP alias

**`git_mcp_workbench`** — в `.cursor/mcp.json` и в IDE.

### Инструменты

| Tool | Описание | Shell fallback |
|------|----------|---------------|
| `git_status` | Статус репозитория | `git status` |
| `changed_files` | Список изменённых файлов | `git diff --name-only` |
| `git_diff_summary` | Сводка изменений | `git diff --stat` |
| `recent_commits` | Последние коммиты | `git log --oneline -10` |
| `commit_summary` | Сводка конкретного коммита | `git show --stat <sha>` |
| `task_snapshot` | Полный снимок задачи | status + diff + log |
| `pr_prep` | Подготовка PR | ручная сборка |
| `restore_task_context` | Восстановление контекста из notesforllm | `notes_resume_context` |
| `save_session_checkpoint` | Checkpoint через Workbench | `notes_checkpoint_save` |
| `save_decision` | Decision через Workbench | `notes_decision_save` |
| `save_session_handoff` | Handoff через Workbench | `notes_handoff_save` |

### Зависимости

Git/MCP Workbench запускает `notesforllm` как subprocess для memory-операций. Env-переменные `NOTESFORLLM_*` в mcp.json передаются Workbench-процессу, который через них конфигурирует подключение к notesforllm.

### Проверка локальной конфигурации

```bash
bash scripts/check-agent-config.sh
```

Скрипт делает static config checks: проверяет наличие tracked шаблонов, локального `mcp.json`, корректность JSON и существование путей из конфигурации. Это не runtime health-check MCP-сервера.

---

## 3. Branch-to-task binding

### Что это

Маппинг `git branch → task_id` в `.workbench/task-branches.json`. Позволяет агентам автоматически определить `task_id` по текущей ветке и не спрашивать пользователя.

### Использование

```bash
# Привязать текущую ветку к task_id
bash scripts/bind-task-branch.sh vkuswill_bot:feature/meal-plan

# Автоматически (task_id = vkuswill_bot:<branch-name>)
bash scripts/bind-task-branch.sh

# Показать привязку текущей ветки
bash scripts/bind-task-branch.sh --show

# Все привязки
bash scripts/bind-task-branch.sh --list
```

### Когда привязывать

- При создании feature branch — сразу после `git checkout -b`.
- При начале работы над issue — привязать `vkuswill_bot:issue-<N>`.
- Для verification — `vkuswill_bot:verification/<topic>`.

### Формат `.workbench/task-branches.json`

```json
{
  "bindings": {
    "feature/meal-plan": {
      "task_id": "vkuswill_bot:feature/meal-plan",
      "updated_at": "2026-03-15T10:00:00+00:00"
    }
  },
  "updated_at": "2026-03-15T10:00:00+00:00",
  "version": 2
}
```

---

## 4. Проверка конфигурации

Запустить bootstrap-скрипт:

```bash
bash scripts/check-agent-config.sh
```

Скрипт проверяет:

| Секция | Что проверяется |
|--------|----------------|
| AGENTS.md | Наличие, упоминание notesforllm / Workbench / task_id |
| Environment (.env) | Переменные `NOTESFORLLM_SPACE_ID`, `NOTESFORLLM_VERIFICATION_SPACE_ID`, UUID-валидность |
| Workbench IDE config | `.cursor/mcp.json` (local), `git_mcp_workbench` entry, путь из конфига, shareable template |
| notesforllm storage | БД существует, `uvx`/`uv` доступен, spaces существуют в БД |
| Branch binding | `.workbench/task-branches.json` существует, есть ли привязки |
| Onboarding docs | `integration-setup.md`, `agent-kickoff.md`, `TEAM_HISPANIOLA.md`, `ARCHITECTURE.md` |
| Git | Репозиторий, текущая ветка |

---

## 5. Checklist

Конфигурация считается рабочей, когда:

- [ ] `AGENTS.md` содержит секции про notesforllm и Git/MCP Workbench
- [ ] `.env` заполнен: `NOTESFORLLM_SPACE_ID`, `NOTESFORLLM_VERIFICATION_SPACE_ID`
- [ ] MCP-сервер `notesforllm` доступен в IDE (user-level)
- [ ] MCP-сервер `git_mcp_workbench` сконфигурирован в локальном `.cursor/mcp.json`
- [ ] Product и verification spaces созданы в notesforllm
- [ ] `task_id` convention определён: `vkuswill_bot:<issue-or-branch>`
- [ ] Routing hygiene определён: verification `task_id` идут только в verification space, product `task_id` не идут в verification space
- [ ] Агенты используют `notes_query(...)` для structured filters вместо broad text search
- [ ] Checkpoint / decision / handoff заполняют `provenance` и `operational`, когда факты известны
- [ ] Verification finding → product fix → verification follow-up связываются через `bridge`
- [ ] Branch binding скрипт работает: `bash scripts/bind-task-branch.sh --list`
- [ ] Bootstrap-скрипт проходит без ошибок: `bash scripts/check-agent-config.sh`
