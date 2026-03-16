# AGENTS.md — VkusVill Bot

> Единый источник правды для всех ИИ-агентов, работающих с этим репозиторием.

---

## Mission

Telegram-бот ВкусВилл — ИИ-ассистент для покупок. Бот понимает запрос на естественном языке, находит товары через MCP-сервер ВкусВилл, собирает корзину и отдаёт ссылку. Поддерживает голосовой канал через Алису и Яндекс Станцию.

**Репозиторий:** `vkuswill_bot`
**Стек:** Python 3.11+, aiogram 3, Qwen (OpenAI-compatible), MCP, PostgreSQL, Redis, Langfuse.

---

## Экипаж «Испаньолы» — реестр агентов

| Агент | Пиратское имя | Специализация | Триггеры |
|-------|--------------|--------------|----------|
| **teamlead** | Капитан Смоллетт | Координация, декомпозиция, делегирование | `тимлид`, `капитан`, `Смоллетт` |
| **architect-analyst** | Доктор Ливси | Архитектура, ADR, trade-offs | `архитектура`, `ADR`, `Доктор Ливси` |
| **code-reviewer** | Гектор Барбосса | Code review, качество, стандарты | `ревью`, `review`, `Барбосса` |
| **refactoring-agent** | Уилл Тёрнер | Рефакторинг, code smells | `рефакторинг`, `упрости`, `Уилл Тёрнер` |
| **appsec-agent** | Дэйви Джонс | SAST, DAST, OWASP, AI safety | `безопасность`, `SAST`, `Дэйви Джонс` |
| **prompt-engineer** | Тиа Дальма | Промпты, A/B-тесты, prompt injection | `промпт`, `Тиа Дальма` |
| **dialog-analyst** | Джек Воробей | Сценарии диалогов, UX | `сценарий`, `диалог`, `Джек Воробей` |
| **secops-agent** | Израэль Хэндс | Деплой, K8s, CI/CD, Yandex Cloud | `деплой`, `kubernetes`, `Израэль Хэндс` |
| **devops-agent** | Джон Сильвер | Git, ветки, PR, релизы, CHANGELOG | `коммит`, `PR`, `релиз`, `Джон Сильвер` |
| **testing-agent** | Билли Бонс | pytest, покрытие, SAST, AI Safety | `тесты`, `pytest`, `Билли Бонс` |
| **habr-writer** | Джошами Гиббс | Статьи на Хабр | `хабр`, `статья`, `Гиббс` |
| **doc-writer** | Бен Ганн | Документация, README, руководства | `документация`, `docs`, `Бен Ганн` |
| **python-senior-developer** | — | Реализация фич, баг-фиксы, рефакторинг | прямая разработка в `src/` |

Подробные описания ролей и характеров — в [docs/TEAM_HISPANIOLA.md](docs/TEAM_HISPANIOLA.md).

---

## Session Memory via notesforllm

MCP-сервер `user-notesforllm` (alias: `notesforllm`) — persistent memory layer для всех агентов.

### Обязательные правила

1. **Старт сессии (context restore):**
   - Если `task_id` известен — вызвать `notes_resume_context(space_id, task_id)` **до начала изменений кода**.
   - Если `task_id` неизвестен — вызвать `notes_search(query)` или `notes_list(space_id)` для поиска контекста.
   - Проверить наличие decisions, handoffs и checkpoints по задаче.

2. **Во время работы (checkpoints & decisions):**
   - После meaningful milestone — `notes_checkpoint_save(space_id, title, summary, task_id=..., agent_id=..., status="in_progress", findings=[...], artifacts=[...], next_steps=[...])`.
   - При durable technical decision (архитектура, схема, API, workflow) — `notes_decision_save(space_id, title, decision, why, task_id=..., agent_id=..., alternatives=[...], consequences=[...])`.
   - Предпочитать краткие, фактологические записи. Не дублировать весь чат.

3. **Конец сессии (handoff):**
   - Если работа **не завершена** — обязательно `notes_handoff_save(space_id, title, goal, current_state, first_next_step, task_id=..., agent_id=..., verified=[...], risks=[...], after_that=[...])`.
   - Если работа **завершена** — handoff не нужен, кроме случаев ожидаемого follow-up.

4. **Timeline review:**
   - Если задача прошла несколько сессий/агентов — `notes_task_timeline(space_id, task_id)` для полной истории.
   - Перед изменением архитектуры — проверить prior decisions.

### Запрещено

- Пропускать context restore на multi-session задачах.
- Создавать handoff для полностью завершённой работы без follow-up.
- Писать low-signal checkpoints на тривиальные правки.
- Менять `task_id` в рамках одного непрерывного workstream.
- **Создавать JSON/MD файлы в `docs/` для хранения контекста, handoffs, decisions.** Всё это должно идти в notesforllm. Файлы `docs/MEAL_PLAN_HANDOFF.json`, `docs/DEBUG_API_KEY_SOURCE.json`, `docs/STAGE_ACCESS.json` — legacy, их данные мигрированы в notesforllm.

---

## Repository Context via Git/MCP Workbench

> **Статус:** setup-dependent. Workbench доступен только если конкретный разработчик или агент локально настроил `.cursor/mcp.json` (gitignored). Репозиторий хранит только переносимый шаблон `workbench-mcp.json.example`, поэтому нельзя писать, что Workbench "уже подключён" без локальной проверки. См. [docs/integration-setup.md](docs/integration-setup.md).

### Если Workbench настроен локально: MCP tools вместо shell

Использовать MCP-инструменты Workbench **в первую очередь** для получения Git-контекста. Shell — fallback **только** если Workbench не настроен или недоступен.

| Задача | MCP tool (primary) | Shell fallback |
|--------|-------------------|----------------|
| Статус репозитория | `git_status` | `git status` |
| Изменённые файлы | `changed_files` | `git diff --name-only` |
| Сводка diff | `git_diff_summary` | `git diff --stat` |
| Последние коммиты | `recent_commits` | `git log --oneline -10` |
| Сводка коммита | `commit_summary` | `git show --stat <sha>` |
| Снимок задачи | `task_snapshot` | git status + diff + log |
| Подготовка PR | `pr_prep` | ручная сборка |
| Восстановление контекста | `restore_task_context` | `notes_resume_context` |
| Checkpoint через Workbench | `save_session_checkpoint` | `notes_checkpoint_save` |
| Decision через Workbench | `save_decision` | `notes_decision_save` |
| Handoff через Workbench | `save_session_handoff` | `notes_handoff_save` |

### Когда Workbench временно недоступен

Если MCP-сервер `git_mcp_workbench` не отвечает — используй Shell для git-операций и прямые `notes_*` tools для memory. Но зафиксируй проблему как `[WARN]` в checkpoint.

---

## Task ID и Space Policy

### Формат task_id

| Тип | Формат | Пример |
|-----|--------|--------|
| Product | `<repo>:<issue-or-branch>` | `vkuswill_bot:feature/meal-plan` |
| Verification | `<repo>:verification/<topic>` | `vkuswill_bot:verification/pg-migration` |

### Правила

- Один `task_id` на весь непрерывный workstream — не создавать новый при рестарте сессии.
- Предпочитать имя ветки или номер issue.
- Verification-задачи (smoke, debug, integration validation) — **всегда** в формате `verification/<topic>`.

### Spaces

| Space | Назначение | Что хранить |
|-------|-----------|-------------|
| **Product space** (`NOTESFORLLM_SPACE_ID`) | Основная работа по репозиторию | Checkpoints, decisions, handoffs по фичам и багам |
| **Verification space** (`NOTESFORLLM_VERIFICATION_SPACE_ID`) | Smoke, debug, integration validation | Результаты проверок, debug-находки |

Не смешивать product и verification notes в одном space.

---

## Workflow

### 1. Startup (начало сессии)

```
1. Определить space_id (из env или через spaces_list)
2. Определить task_id:
   → Из .workbench/task-branches.json (по текущей ветке)
   → Или из issue / контекста запроса
   → Привязать ветку: bash scripts/bind-task-branch.sh <task_id>
3. Если task_id известен:
   → при локально настроенном Workbench можно вызвать restore_task_context
   → иначе вызвать notes_resume_context(space_id, task_id)
4. Если task_id неизвестен:
   → notes_search(query) или notes_list(space_id)
5. Прочитать prior decisions и handoffs
6. Получить Git-контекст:
   → git_status, changed_files, recent_commits (через Workbench)
   → Fallback: git status, git diff, git log (Shell)
7. Начать работу
```

### 2. During Work (выполнение)

```
1. После meaningful milestone:
   → notes_checkpoint_save(...)
2. При durable technical decision:
   → notes_decision_save(...)
3. При фрагментации контекста:
   → notes_task_timeline(space_id, task_id)
4. Для Git-контекста:
   → Workbench tools (preferred) или git shell commands
```

### 3. End of Session (завершение)

```
1. Если работа завершена:
   → Финальный checkpoint со status="done"
   → Handoff НЕ нужен (если нет follow-up)
2. Если работа НЕ завершена:
   → notes_handoff_save(...)
     - goal: что задача должна достичь
     - current_state: что сделано
     - verified: что подтверждено
     - risks: известные риски
     - first_next_step: одно главное следующее действие
   → Использовать тот же task_id
```

---

## Memory Linkage Convention

Для восстановления контекста между сессиями агенты должны заполнять метаданные linkage, **используя только реально поддерживаемые параметры `notesforllm`**.

### session_id (checkpoint, handoff)

`session_id` поддерживается в `notes_checkpoint_save` и `notes_handoff_save`, но **не** в `notes_decision_save`.

```
notes_checkpoint_save(
  ...,
  session_id="vkuswill-bot-agent-2026-03-15",
  agent_id="teamlead"
)
```

### related_page_ids (только decision)

`related_page_ids` поддерживается **только** в `notes_decision_save`. Используй для связи decision с предшествующими checkpoint/decision.

```
notes_decision_save(
  ...,
  related_page_ids=["<uuid-предыдущего-checkpoint>"]
)
```

Для checkpoint и handoff связи передаются через `tags` и `findings`, а не через `related_page_ids`.

### Verification → Product bridge

Когда verification-находка (из verification space) требует продуктовых изменений:

1. Создать checkpoint в verification space с `findings` и `status="done"`.
2. Создать decision в product space с `tags=["from-verification"]` и `related_page_ids=["<verification-page-uuid>"]`.
3. В `findings` product checkpoint указать: `"Originated from verification: <verification-task-id>"`.

Это позволяет построить цепочку: verification finding → product decision → remediation.

---

## Правила для всех агентов

### Coding Standards

- Python 3.11+, strict type hints, async/await
- Форматирование: `ruff format`, линтер: `ruff check`
- Тесты: `pytest` + `pytest-asyncio`
- Пакетный менеджер: `uv`
- Conventional Commits (см. `.cursor/rules/git-commits.mdc`)

### Приоритеты

| Приоритет | Область |
|-----------|---------|
| P0 | Безопасность |
| P1 | Работоспособность (баги) |
| P2 | Качество кода |
| P3 | Документация |
| P4 | Оптимизация |

### Запрещено

- Хардкодить абсолютные пути из чужих машин
- Коммитить секреты, токены, пароли
- Пропускать тесты перед коммитом
- Игнорировать prior decisions из notesforllm
- Создавать агентов для разовых задач

---

## Подробные руководства

| Документ | Описание |
|----------|----------|
| [docs/integration-setup.md](docs/integration-setup.md) | Подключение notesforllm и Git/MCP Workbench |
| [docs/agent-kickoff.md](docs/agent-kickoff.md) | Bootstrap-задача и порядок запуска |
| [docs/TEAM_HISPANIOLA.md](docs/TEAM_HISPANIOLA.md) | Описание ролей экипажа |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Архитектура проекта |
