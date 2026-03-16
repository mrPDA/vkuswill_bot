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
   - Если `notes_resume_context(...)` вернул `synthesis` — использовать его как основной structured snapshot состояния задачи.
   - Если `task_id` неизвестен — вызвать `notes_search(query)` или `notes_list(space_id)` для поиска контекста.
   - Проверить наличие decisions, handoffs и checkpoints по задаче.

2. **Во время работы (checkpoints & decisions):**
   - Для structured lookup использовать `notes_query(...)`, когда нужны фильтры по `task_id`, `kind`, `status`, `author_agent`, `workflow`, `stage`, `environment`, `verification_scope`, `known_issue`, `bridge_role`, датам или сортировке.
   - Для сравнения итераций использовать `notes_compare(...)` вместо ручного сравнения двух Markdown checkpoint notes.
   - До `notes_checkpoint_save(...)`, `notes_decision_save(...)`, `notes_handoff_save(...)` и `notes_test_run_checkpoint(...)` проверять routing hygiene:
     - `task_id` с `:verification/` писать только в verification space;
     - space со slug, оканчивающимся на `-verification`, не использовать для обычного product `task_id`;
     - не делать silent reroute, если routing не совпадает.
   - После meaningful milestone — `notes_checkpoint_save(space_id, title, summary, task_id=..., agent_id=..., status="in_progress", findings=[...], artifacts=[...], next_steps=[...])`.
   - Для повторяющихся прогонов тестов использовать `notes_test_run_checkpoint(...)` как основной template поверх checkpoint layer.
   - При durable technical decision (архитектура, схема, API, workflow) — `notes_decision_save(space_id, title, decision, why, task_id=..., agent_id=..., alternatives=[...], consequences=[...])`.
   - Когда известны git или CI факты, заполнять `provenance`: `repo`, `branch`, `head_sha`, `pr_number`, `run_id`, `run_url`, `environment`, `verification_scope`.
   - Для bootstrap между сессиями заполнять `operational`: `last_good_state`, `unresolved_risks`, `exact_next_command`, `files_in_play`, `known_issues`, `blocked_by`.
   - Для цепочки `problem -> fix -> recheck` использовать `bridge` с `role` и `target_page_ids`, а не свободный prose.
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
- Перед записью в notesforllm сначала выбрать правильный `space_id` по типу задачи, а не полагаться на неявный перенос между spaces.

### Spaces

| Space | Назначение | Что хранить |
|-------|-----------|-------------|
| **Product space** (`NOTESFORLLM_SPACE_ID`) | Основная работа по репозиторию | Checkpoints, decisions, handoffs по фичам и багам |
| **Verification space** (`NOTESFORLLM_VERIFICATION_SPACE_ID`) | Smoke, debug, integration validation | Результаты проверок, debug-находки |

Не смешивать product и verification notes в одном space. Если `task_id` содержит `:verification/`, использовать только `NOTESFORLLM_VERIFICATION_SPACE_ID`. Если space относится к verification, не писать туда обычные product task_id.

---

## Workflow

### 1. Startup (начало сессии)

```
1. Определить space_id (из env или через spaces_list)
2. Определить task_id:
   → Из .workbench/task-branches.json (по текущей ветке)
   → Или из issue / контекста запроса
   → Привязать ветку: bash scripts/bind-task-branch.sh <task_id>
   → Если `task_id` содержит `:verification/`, выбрать verification space, иначе product space
3. Если task_id известен:
   → при локально настроенном Workbench можно вызвать restore_task_context
   → иначе вызвать notes_resume_context(space_id, task_id)
4. Если task_id неизвестен:
   → notes_search(query) или notes_list(space_id)
5. Если resume вернул synthesis:
   → использовать его как primary workflow snapshot
6. Прочитать prior decisions и handoffs
7. Получить Git-контекст:
   → git_status, changed_files, recent_commits (через Workbench)
   → Fallback: git status, git diff, git log (Shell)
8. Начать работу
```

### 2. During Work (выполнение)

```
1. Для structured workflow lookup:
   → notes_query(...)
2. Перед любым pattern save:
   → проверить, что `task_id` и target space совпадают по product/verification routing
2. Для сравнения повторных раундов:
   → notes_compare(base_page_id, candidate_page_id)
3. После meaningful milestone:
   → notes_checkpoint_save(...)
4. Для повторяющихся тестовых прогонов:
   → notes_test_run_checkpoint(...)
5. При наличии repo/run/verification фактов:
   → заполнять provenance / operational / bridge
6. При durable technical decision:
   → notes_decision_save(...)
7. При фрагментации контекста:
   → notes_task_timeline(space_id, task_id)
8. Для Git-контекста:
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

### related_page_ids (checkpoint, decision)

`related_page_ids` поддерживается в `notes_checkpoint_save` и `notes_decision_save`.

- В `notes_checkpoint_save` — для связи текущего раунда с предыдущим checkpoint или baseline page.
- В `notes_decision_save` — для связи decision с предшествующими checkpoint/decision.

```
notes_checkpoint_save(
  ...,
  related_page_ids=["<uuid-предыдущего-checkpoint>"]
)

notes_decision_save(
  ...,
  related_page_ids=["<uuid-предыдущего-checkpoint>"]
)
```

Для handoff связи по-прежнему передаются через `task_id`, `session_id`, `verified`, `risks` и `first_next_step`, а не через `related_page_ids`.

### test_run template

Для повторяющихся прогонов тестов предпочитать `notes_test_run_checkpoint(...)` вместо generic checkpoint.

Он сохраняет:

- structured `template_data` для suite/result/counts/failures;
- human-readable checkpoint content;
- stable tag `test_run`;
- совместимость с `notes_query`, `notes_compare` и `notes_resume_context.synthesis`.

### provenance / operational / bridge

Для нового operational-memory workflow:

- `provenance` хранит git/run/env факты, которые нужны для последующей верификации и resume;
- `operational` хранит bootstrap-факты для следующей сессии;
- `bridge` связывает verification finding, product fix и verification follow-up.

Минимальная практика для этого репозитория:

- product checkpoint после разработки: `provenance.repo="vkuswill_bot"` и `operational.files_in_play=[...]`
- verification checkpoint: `provenance.environment="staging"` или `provenance.environment="local"`, `provenance.verification_scope="<suite-or-surface>"`
- remediation decision/checkpoint: `bridge.role="product_fix"` и `bridge.target_page_ids=["<uuid-verification-finding>"]`
- follow-up verification: `bridge.role="verification_follow_up"` и `bridge.target_page_ids=["<uuid-product-fix>"]`

### Verification → Product bridge

Когда verification-находка (из verification space) требует продуктовых изменений:

1. Создать checkpoint в verification space с `findings`, `status="done"` и `bridge.role="verification_finding"`.
2. Создать decision или checkpoint в product space с `bridge.role="product_fix"` и `bridge.target_page_ids=["<verification-page-uuid>"]`.
3. После фикса создать verification follow-up с `bridge.role="verification_follow_up"` и `bridge.target_page_ids=["<product-fix-page-uuid>"]`.

Это позволяет построить наблюдаемую цепочку: verification finding → product fix/decision → verification recheck.

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
