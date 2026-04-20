# Архитектура VkusVill Bot

Актуальное описание runtime-потока и ключевых подсистем для текущей версии бота.

Документ сфокусирован на том, как система реально работает в коде (`src/vkuswill_bot/**`), без исторических/legacy путей.

---

## 1. Назначение системы

VkusVill Bot принимает пользовательские запросы из Telegram, маршрутизирует их в `ShoppingAgent`, вызывает MCP-инструменты ВкусВилл и возвращает ответ в Telegram-формате (с HTML-санитизацией, разбиением на чанки и inline-кнопкой корзины).

Основной интерфейс движка задаётся `ChatEngineProtocol`:

- `process_message(user_id, text, on_progress=None) -> str`
- `reset_conversation(user_id)`
- `close()`
- `get_last_cart_snapshot(user_id)`
- `get_last_trace_id(user_id)`

Код: `src/vkuswill_bot/services/chat_engine.py`.

---

## 2. Runtime-контур (вход/выход)

### 2.1 Точка входа

`src/vkuswill_bot/__main__.py`

Поддерживаются два режима:

- `USE_WEBHOOK=false` -> long polling;
- `USE_WEBHOOK=true` -> aiohttp server (`/webhook`, `/health`, `/voice-link/*`).

В non-production окружении при наличии `DEBUG_API_KEY` дополнительно включаются stage debug endpoint-ы:

- `POST /debug/run-shopping`
- `POST /debug/reset-history`

Код: `src/vkuswill_bot/services/debug_api.py`.

### 2.2 Telegram слой

- Handlers: `src/vkuswill_bot/bot/handlers.py`
- Middlewares: `src/vkuswill_bot/bot/middlewares.py`
- Delivery helpers: `src/vkuswill_bot/bot/telegram_delivery.py`

Ключевые middleware:

1. `UserMiddleware` (outer middleware): upsert пользователя, блокировки, инъекция `db_user`.
2. `ThrottlingMiddleware`: rate limit по пользователю.

---

## 3. LLM runtime и ограничения

Фабрика движка: `src/vkuswill_bot/services/chat_engine_factory.py`.

На текущем этапе поддерживается только:

- `CHAT_ENGINE=shopping_agent`
- `LLM_PROVIDER=qwen_openai`
- `LLM_ROUTING_STRATEGY=single_provider`

Любые другие комбинации завершаются ошибкой валидации/инициализации.

Параметры и валидация: `src/vkuswill_bot/config.py`.

---

## 4. Обработка сообщения (основной путь)

### Шаг 1. Handler

`handle_text()`:

1. передаёт запрос в `chat_engine.process_message(...)`;
2. получает финальный текст;
3. прогоняет через `build_telegram_delivery_preview(...)`;
4. отправляет ответ чанками; клавиатура (кнопка корзины/feedback) добавляется в последний чанк.

### Шаг 2. Routing внутри ShoppingAgent

Основной исполнитель turn-а: `run_locked_turn()`  
Код: `src/vkuswill_bot/agents/shopping_turn_executor.py`.

Порядок ветвления:

1. **Meal-plan executor path** (если профиль `meal_plan` и пройдены rollout/gate условия).
2. **Multi-course executor path** (для многоблюдных recipe/cart сценариев).
3. **Fast paths**:
   - status-cart short-circuit;
   - explicit cart fast path.
4. **Стандартный tool loop**:
   - LLM шаг -> tool calls -> выполнение инструментов -> следующий шаг;
   - ограничение `max_tool_calls`;
   - лимит входного prompt budget на turn.

### Шаг 3. Tool processing

`DefaultToolStepProcessor` вызывает:

- `execute_tool_calls(...)` (MCP/local инструменты),
- recovery hints для следующих шагов.

Код: `src/vkuswill_bot/agents/shopping_tool_step_processor.py`.

---

## 5. Meal-plan executor (выделенный конвейер)

Код: `src/vkuswill_bot/agents/meal_plan_executor.py`.

`run_meal_plan_turn()` выполняет последовательные фазы:

1. Parse meal-plan запроса через LLM extractor.
2. Генерация плана блюд.
3. Сбор ингредиентов.
4. Проверка phase2 safety policy.
5. Поиск товаров по дням.
6. Создание сгруппированных корзин.
7. Детерминированный рендер финального ответа.

Если фаза завершается с критической ошибкой:

- либо мягкий fail-soft ответ;
- либо fallback в стандартный turn (`run_locked_turn(..., _skip_meal_plan_executor=True)`), если разрешено.

---

## 6. Rollout/gating meal-plan executor

Решения по маршрутизации принимаются в `run_locked_turn()` с использованием:

- `resolve_rollout_percent(...)`
- `evaluate_non_prod_rollout_bypass(...)`
- `resolve_executor_gate_reason(...)`

Код: `src/vkuswill_bot/services/meal_plan_rollout_policy.py`.

Факторы gate:

- prompt profile (`meal_plan` / не `meal_plan`);
- флаг `meal_plan_executor_enabled`;
- shadow mode;
- rollout процент и bucket пользователя;
- KPI gates;
- bypass только для non-production и только при валидных reason/actor/expires_at/TTL.

---

## 7. Нормализация tool-аргументов и cart safety

Нормализация выполняется до вызова MCP-инструментов:

- `preprocess_tool_args(...)`
- `preprocess_cart_link_args(...)`
- `fix_cart_args(...)`

Код:

- `src/vkuswill_bot/agents/tool_preprocessor.py`
- `src/vkuswill_bot/agents/tool_preprocessor_cart.py`
- `src/vkuswill_bot/services/tool_input_normalizers.py`

Что делает слой:

- проставляет/нормализует `q` (включая строковые числа вроде `"1,5"` -> `1.5`);
- объединяет дубли по `xml_id`;
- ограничивает максимальное количество позиции;
- для аддитивных апдейтов корзины восстанавливает предыдущее количество, чтобы не раздувать quantity.

---

## 8. Telegram delivery pipeline (user-visible контракт)

`build_telegram_delivery_preview()` (код: `src/vkuswill_bot/bot/telegram_delivery.py`) выполняет те же преобразования, что и runtime-доставка:

1. sanitization HTML (только whitelist Telegram-тегов);
2. извлечение ссылки корзины в inline-кнопку;
3. разбиение длинного ответа на куски (`MAX_TELEGRAM_MESSAGE_LENGTH = 4096`).

Это важно для stage response contracts, потому что тестируется именно то, что реально увидит пользователь в Telegram.

---

## 9. Stage response contracts и живые проверки

Единый набор контрактных сценариев хранится в:

- `src/vkuswill_bot/testing/response_contract_cases.py`

Он переиспользуется в двух раннерах:

1. Stage pytest: `tests/test_stage_response_contracts.py`
2. Live runtime script: `scripts/run_live_response_contracts.py`

Совместимость старого импорта сохранена через thin-wrapper:

- `tests/stage_response_contract_cases.py`

Подробный runbook: `docs/stage-response-contracts.md`.

---

## 10. Данные и хранилища

- **PostgreSQL**: `UserStore`, роли, блокировки, события, rollout-метрики.
- **SQLite**: `PreferencesStore` (локальные предпочтения).
- **Redis**: используется как внешний dependency в health-check и отдельных сервисах, но диалоговый runtime в текущей конфигурации — memory-first.

Практическое ограничение: в `Config` `storage_backend` сейчас валидируется только в `memory`.

---

## 11. Операционные ограничения и риски

- Смена `llm_provider`/`routing_strategy` на другие значения не поддерживается.
- Debug API не должен включаться в production (проверяется кодом `should_enable_debug_api`).
- При обновлении Telegram delivery правил нужно синхронно обновлять response contracts.
- При изменении rollout-политики meal-plan нужно учитывать KPI gates и bypass TTL, иначе возможна нежелательная активация executor path.

---

## 12. Где смотреть дальше

- Runtime entrypoint: `src/vkuswill_bot/__main__.py`
- Message handling: `src/vkuswill_bot/bot/handlers.py`
- Turn execution: `src/vkuswill_bot/agents/shopping_turn_executor.py`
- Meal-plan flow: `src/vkuswill_bot/agents/meal_plan_executor.py`
- Config contract: `src/vkuswill_bot/config.py`
- Stage contracts runbook: `docs/stage-response-contracts.md`
