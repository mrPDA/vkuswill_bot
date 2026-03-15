# Agent Kickoff — начало работы

Порядок запуска агентного workflow для VkusVill Bot.

---

## Bootstrap Task

### Цель

Настроить persistent memory и Git-aware контекст для всех агентов проекта.

### Предусловия

- [ ] Python 3.11+ установлен
- [ ] `uv` установлен (`pip install uv`)
- [ ] Cursor IDE с поддержкой MCP
- [ ] `.env` заполнен (см. `.env.example`)

### Шаги

```
1. Установить зависимости:
   uv sync

2. Создать .env из шаблона:
   cp .env.example .env

3. Подключить MCP-серверы в IDE:
   → notesforllm (обязательно)
   → Git/MCP Workbench (когда доступен)

4. Создать spaces в notesforllm:
   → Product space: "vkuswill_bot"
   → Verification space: "vkuswill_bot-verification"
   → Записать UUID в .env

5. Проверить конфигурацию:
   bash scripts/check-agent-config.sh

6. Первый checkpoint:
   → notes_checkpoint_save(space_id, "Bootstrap complete", "Agent infrastructure configured")
```

---

## Recommended Execution Order

Для первой сессии после bootstrap:

### Фаза 1: Context Restore

```
1. spaces_list() → найти свои spaces
2. notes_list(space_id) → проверить, есть ли prior context
3. git status + git log --oneline -5 → текущее состояние репозитория
```

### Фаза 2: Orientation

```
1. Прочитать AGENTS.md → понять правила
2. Прочитать docs/integration-setup.md → понять конфигурацию
3. git diff main...HEAD → что изменилось с main
```

### Фаза 3: Work

```
1. Определить task_id (из ветки или issue)
2. Начать работу по задаче
3. checkpoint после каждого meaningful milestone
4. decision при архитектурных выборах
```

### Фаза 4: Wrap-up

```
1. Если не закончено → notes_handoff_save(...)
2. Если закончено → финальный checkpoint со status="done"
3. git status → убедиться, что всё чисто
```

---

## Immediate Next Milestone

### M1: Полная интеграция notesforllm

**Статус:** готово к использованию

**Что сделать:**
1. Создать product и verification spaces
2. Заполнить `NOTESFORLLM_SPACE_ID` и `NOTESFORLLM_VERIFICATION_SPACE_ID` в `.env`
3. Первый `notes_checkpoint_save` — зафиксировать bootstrap

**Критерий готовности:** `notes_resume_context(space_id, "vkuswill_bot:bootstrap")` возвращает checkpoint.

### M2: Подключение Git/MCP Workbench

**Статус:** ожидает доступности сервера

**Что сделать:**
1. Установить Workbench MCP-сервер
2. Добавить в конфигурацию IDE
3. Скопировать `workbench-mcp.json.example` в локальный `.cursor/mcp.json` и заменить плейсхолдеры путей
4. Опционально заполнить `WORKBENCH_REPO_PATH` и `WORKBENCH_MCP_JSON_PATH` в `.env` для helper-скриптов
5. Проверить: `git_status` через MCP возвращает корректный результат

**Критерий готовности:** `task_snapshot` через MCP возвращает валидные данные.

### M3: Первый рабочий цикл с memory

**Статус:** после M1

**Что сделать:**
1. Взять реальную задачу из backlog
2. Пройти полный цикл: resume → work → checkpoint → handoff
3. Верифицировать, что следующая сессия корректно восстанавливает контекст

**Критерий готовности:** `notes_task_timeline` показывает 2+ записей для одного task_id.
