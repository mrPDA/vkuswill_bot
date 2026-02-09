# Рекомендации по улучшению vkuswill-bot

> На основе code review от 09.02.2026 (ревью #4)
>
> **Текущее состояние:** 984 теста, 99% покрытие, 0 блокеров, версия 0.3.0
>
> **Оценка:** 💬 Comment — качество кода высокое, есть одна системная проблема

---

## Статус предыдущих рекомендаций

### Ревью #1–#3 (12 рекомендаций)

| # | Рекомендация | Статус |
|---|---|---|
| 1 | Rate-limiting пользователей | ✅ `ThrottlingMiddleware` |
| 2 | LRU-вытеснение диалогов | ✅ `DialogManager` + `OrderedDict` |
| 3 | Ограничение длины сообщения | ✅ `MAX_USER_MESSAGE_LENGTH = 4096` |
| 4 | Graceful shutdown | ✅ `asyncio.Event` + сигналы |
| 5 | SSL-верификация GigaChat | ⏳ Ожидание SDK |
| 6 | Логирование исключений | ✅ `logger.debug` вместо `pass` |
| 7 | Дедупликация тестовых хелперов | ✅ `tests/helpers.py` |
| 8 | Синхронизация версии MCP | ✅ `importlib.metadata` |
| 9 | Расширить `.gitignore` | ✅ `data/`, `*.db`, `*.log`, `.cursor/` |
| 10 | Персистентные диалоги | ✅ `RedisDialogManager` + `redis_client.py` |
| 11 | Пул потоков GigaChat | ✅ `ThreadPoolExecutor(max_workers=50)` |
| 12 | Покрытие кода 97%+ | ✅ 99% |

### Ревью #3 (8 рекомендаций)

| # | Рекомендация | Статус |
|---|---|---|
| 1 | `.gitignore`: `data/`, `*.db` | ✅ Выполнено |
| 2 | `.env.example`: `DATABASE_PATH` | ✅ Выполнено |
| 3 | TTL/лимит для `_price_cache` | ✅ `PriceCache` с FIFO-вытеснением (`MAX_PRICE_CACHE_SIZE = 5000`) |
| 4 | Проверка `"data"` в cart/verify | Требует проверки в `CartProcessor` |
| 5 | Импорты `copy`, `math` в начало файла | ✅ В `cart_processor.py` импорты на своём месте |
| 6 | Рефакторинг `gigachat_service.py` | ✅ Разделён на 10 модулей (349 из 859 строк) |
| 7 | Персистентные диалоги (SQLite) | ✅ Redis-бэкенд |
| 8 | SSL-верификация GigaChat | ⏳ Ожидание SDK |

---

## Новая архитектура (после рефакторинга)

```
src/vkuswill_bot/services/     (16 модулей, 1302 statements)
├── gigachat_service.py         # 177 stmts — оркестрация, цикл function calling
├── cart_processor.py           # 148 stmts — корзина, расчёт, верификация
├── tool_executor.py            # 150 stmts — вызов инструментов, маршрутизация
├── mcp_client.py               # 160 stmts — JSON-RPC к MCP-серверу
├── search_processor.py         #  68 stmts — обрезка/обогащение результатов поиска
├── dialog_manager.py           #  45 stmts — in-memory LRU-диалоги
├── redis_dialog_manager.py     #  89 stmts — Redis-бэкенд диалогов
├── redis_client.py             #  29 stmts — Redis-обёртка
├── preferences_store.py        #  66 stmts — SQLite-хранилище предпочтений
├── recipe_service.py           #  82 stmts — генерация рецептов через GigaChat
├── recipe_store.py             #  59 stmts — SQLite-хранилище рецептов
├── price_cache.py              #  47 stmts — PriceCache с FIFO-вытеснением
├── prompts.py                  #   6 stmts — системный промпт и описания инструментов
└── config.py                   #  21 stmts — pydantic-settings
```

---

## Критические (устранить в ближайший спринт)

### 1. Рефакторинг тестов — удалить дублирование и делегаты совместимости

**Проблема:** `test_gigachat_service.py` содержит **2664 строки и 30 классов** — это самый большой файл проекта. Из них **15 классов дублируют** тесты, которые уже есть в выделенных тестовых файлах. Кроме того, в production-коде `gigachat_service.py` (строки 171-213) живут **делегаты обратной совместимости**, единственная цель которых — чтобы старые тесты продолжали работать через `GigaChatService._parse_preferences(...)` вместо `ToolExecutor._parse_preferences(...)`.

Это анти-паттерн: **production-код несёт мёртвый груз ради тестов**.

**Дублирующие классы (удалить из `test_gigachat_service.py`):**

| Класс | Строки | Дубликат в |
|---|---|---|
| `TestHistory` | 154 | `test_dialog_manager.py::TestGetHistory` |
| `TestLRUEviction` | 222 | `test_dialog_manager.py::TestLRUEviction` |
| `TestParsePreferences` | 647 | `test_tool_executor.py::TestParsePreferences` |
| `TestParsePreferencesEdgeCases` | 875 | `test_tool_executor.py::TestParsePreferences` |
| `TestApplyPreferencesToQuery` | 703 | `test_tool_executor.py::TestApplyPreferencesToQuery` |
| `TestParseToolArguments` | 913 | `test_tool_executor.py::TestParseArguments` |
| `TestAppendAssistantMessage` | 950 | `test_tool_executor.py::TestBuildAssistantMessage` |
| `TestPreprocessToolArgs` | 1016 | `test_tool_executor.py::TestPreprocessArgs` |
| `TestIsDuplicateCall` | 1065 | `test_tool_executor.py::TestIsDuplicateCall` |
| `TestExecuteTool` | 1170 | `test_tool_executor.py::TestExecute` |
| `TestPostprocessToolResult` | 1205 | `test_tool_executor.py::TestPostprocessResult` |
| `TestCallLocalTool` | 790 | `test_tool_executor.py::TestCallLocalTool` |
| `TestParseJsonFromLLM` | 1688 | `test_recipe_service.py::TestParseJson` |
| `TestEnrichWithKg` | 2006 | `test_recipe_service.py::TestEnrichWithKg` |
| `TestFormatRecipeResult` | 2156 | `test_recipe_service.py::TestFormatResult` |

**Классы, которые должны ОСТАТЬСЯ (тестируют оркестрацию `GigaChatService`):**

| Класс | Что тестирует |
|---|---|
| `TestMessageTruncation` | Обрезка входящего сообщения |
| `TestProcessMessage` | Основной цикл function calling |
| `TestProcessMessageWithPrefs` | Цикл с предпочтениями |
| `TestSearchTrimCacheCartFlow` | Интеграция: поиск → кеш → корзина |
| `TestClose` | Закрытие GigaChat-клиента |
| `TestGetFunctions` / `WithPrefs` / `WithRecipes` | Загрузка инструментов |
| `TestRecipeToolRouting` | Маршрутизация `recipe_ingredients` |
| `TestHandleRecipeIngredients` | Обработка рецептов |
| `TestHandleRecipeIngredientsEdgeCases` | Edge cases рецептов |
| `TestIsRateLimitError` | Определение 429 |
| `TestCallGigachat` | Семафор + retry |
| `TestModuleConstants` | Проверка констант |
| `TestSyncDelegatesWithRedisBackend` | Совместимость делегатов |

**Делегаты для удаления из `gigachat_service.py` (строки 171-213):**

```python
# Удалить полностью — строки 171-213:
_parse_preferences = staticmethod(ToolExecutor._parse_preferences)
_apply_preferences_to_query = staticmethod(ToolExecutor._apply_preferences_to_query)
_parse_tool_arguments = staticmethod(ToolExecutor.parse_arguments)
_append_assistant_message = staticmethod(ToolExecutor.build_assistant_message)
_enrich_with_kg = staticmethod(RecipeService._enrich_with_kg)
_format_recipe_result = staticmethod(RecipeService._format_result)
_parse_json_from_llm = staticmethod(RecipeService._parse_json)

def _preprocess_tool_args(self, ...): ...
def _is_duplicate_call(self, ...): ...
async def _execute_tool(self, ...): ...
def _postprocess_tool_result(self, ...): ...
async def _call_local_tool(self, ...): ...
```

**Что сделать:**
- [ ] Убедиться, что все кейсы из 15 дублирующих классов покрыты в новых тестовых файлах (перенести уникальные, если есть)
- [ ] Удалить 15 дублирующих классов из `test_gigachat_service.py`
- [ ] Удалить делегаты совместимости из `gigachat_service.py` (строки 171-213)
- [ ] Обновить `TestSyncDelegatesWithRedisBackend` — удалить или переписать без делегатов
- [ ] Запустить `pytest --cov` — покрытие не должно упасть

**Ожидаемый результат:**

| Метрика | Сейчас | После |
|---|---|---|
| `test_gigachat_service.py` | 2664 строк, 30 классов | ~1000-1200 строк, ~15 классов |
| `gigachat_service.py` | 349 строк (43 строки — делегаты) | ~300 строк |
| Тестов (всего) | 984 | ~984 (дубликаты покрыты) |
| Покрытие | 99% | 99% |

**Порядок работы:**

```bash
# 1. Проверить покрытие перед удалением
uv run pytest --cov -q

# 2. Сравнить тесты: какие кейсы уникальны для test_gigachat_service.py
# Для каждого дублирующего класса — проверить, что ВСЕ тест-методы
# имеют аналоги в целевом файле

# 3. Перенести уникальные кейсы (если есть) в целевой файл

# 4. Удалить дублирующие классы из test_gigachat_service.py

# 5. Удалить делегаты из gigachat_service.py

# 6. Проверить, что ничего не сломалось
uv run pytest --cov -q
```

**Файлы:** `test_gigachat_service.py`, `gigachat_service.py`
**Оценка:** 2-3 часа (основное время — на сверку кейсов)

---

## Важные (желательно в ближайшем спринте)

### 2. SSL-верификация GigaChat (отложено)

**Статус:** ожидание поддержки CA-сертификата Минцифры в GigaChat SDK

**Текущее состояние:** `verify_ssl_certs=False` в `gigachat_service.py:80`

**Что отслеживать:**
- [ ] [GigaChat SDK issues](https://github.com/ai-forever/gigachat/issues)
- [ ] Обновления SDK: `uv update gigachat`

**Когда будет готово:**
- [ ] Установить `verify_ssl_certs=True` + `ca_bundle_file` (если нужен кастомный CA)
- [ ] Убрать `xfail` с теста `TestSSLSecurity::test_ssl_verification_settings`

---

### 3. ResourceWarning в тестах

**Текущее состояние:** 2 warnings в выводе pytest:

```
Enable tracemalloc to get traceback where the object was allocated.
See https://docs.pytest.org/en/stable/how-to/capture-warnings.html#resource-warnings
```

**Что сделать:**
- [ ] Запустить `uv run pytest -W error::ResourceWarning` для выявления конкретных файлов
- [ ] Добавить `await client.aclose()` или `async with` для незакрытых httpx/aiosqlite соединений в фикстурах
- [ ] Или добавить в `pyproject.toml`:

```toml
filterwarnings = [
    "ignore::DeprecationWarning",
    "ignore::ResourceWarning",  # если не критично
]
```

**Оценка:** 15-30 минут

---

## Желательные (бэклог)

### 4. Тип возврата `_call_gigachat` — `object` вместо конкретного типа

**Файл:** `gigachat_service.py`, строка 240-244

```python
async def _call_gigachat(
    self,
    history: list[Messages],
    functions: list[dict],
) -> object:
```

**Проблема:** возвращаемый тип `object` слишком общий — теряется автодополнение и проверка типов

**Что сделать:**
- [ ] Заменить на конкретный тип из GigaChat SDK:

```python
from gigachat.models import ChatCompletion

async def _call_gigachat(
    self,
    history: list[Messages],
    functions: list[dict],
) -> ChatCompletion:
```

**Оценка:** 2 минуты

---

### 5. Константа `MAX_CONVERSATIONS` дублируется

**Файл:** `gigachat_service.py`, строка 35

```python
MAX_CONVERSATIONS = 1000
```

**Проблема:** Константа `MAX_CONVERSATIONS` определена в `gigachat_service.py`, но фактически используется в `DialogManager` (который принимает её как параметр `max_conversations`). При изменении значения можно забыть обновить одно из мест.

**Что сделать:**
- [ ] Определить `MAX_CONVERSATIONS` в `dialog_manager.py` и импортировать при необходимости
- [ ] Или: передавать через `config.py` как `max_conversations: int = 1000`

**Оценка:** 5 минут

---

### 6. `_is_rate_limit_error` — строковая эвристика вместо типа

**Файл:** `gigachat_service.py`, строки 285-293

```python
@staticmethod
def _is_rate_limit_error(exc: Exception) -> bool:
    exc_str = str(exc).lower()
    return "429" in exc_str or "rate" in exc_str or "too many" in exc_str
```

**Проблема:** хрупкая эвристика по строке исключения. Может ложно срабатывать (например, текст ошибки «rate of fire too many items found»). Есть TODO-комментарий.

**Что сделать:**
- [ ] Изучить иерархию исключений GigaChat SDK
- [ ] Заменить на проверку типа/атрибута:

```python
@staticmethod
def _is_rate_limit_error(exc: Exception) -> bool:
    # httpx.HTTPStatusError (если SDK пробрасывает)
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        return exc.response.status_code == 429
    # Fallback: строковая эвристика
    exc_str = str(exc).lower()
    return "429" in exc_str or "too many" in exc_str
```

**Оценка:** 30 минут (включая исследование SDK)

---

## Инструменты — текущее состояние

| Инструмент | Статус |
|---|---|
| CI/CD (тесты, lint) | ✅ `.github/workflows/ci.yml` |
| Release workflow | ✅ `.github/workflows/release.yml` |
| Git hooks | ✅ `.githooks/commit-msg`, `pre-push` |
| SAST-тесты | ✅ `test_security_sast.py` |
| AI Safety тесты | ✅ `test_ai_safety.py` |
| Input validation | ✅ `test_input_validation.py` |
| Config security | ✅ `test_config_security.py` |
| Ruff (lint + format) | ✅ `pyproject.toml [dev]` |
| Makefile | ✅ `Makefile` |
| Issue/PR templates | ✅ `.github/` |
| Redis-бэкенд | ✅ `redis_dialog_manager.py` |
| Кеш рецептов | ✅ `recipe_store.py` |
| PriceCache с FIFO | ✅ `price_cache.py` |

---

## Метрики проекта

| Метрика | Ревью #1 | Ревью #3 | Ревью #4 (текущее) |
|---|---|---|---|
| Тестов | 292 | 469 | **984** |
| Покрытие | 94% | 98% | **99%** |
| Модулей (src) | 6 | 9 | **16** |
| `gigachat_service.py` | 326 строк | 859 строк | **349 строк** |
| Версия | 0.1.0 | 0.1.0 | **0.3.0** |
| xfailed | 3 | 4 | **4** |
| Блокеров | 0 | 0 | **0** |

---

## Сводная таблица

| # | Рекомендация | Приоритет | Сложность | Время |
|---|---|---|---|---|
| 1 | Рефакторинг тестов + удаление делегатов | 🔴 Важный | Средняя | 2-3 ч |
| 2 | SSL-верификация GigaChat | 🟡 Средний | Средняя | Ожидание SDK |
| 3 | ResourceWarning в тестах | 🟡 Средний | Низкая | 15-30 мин |
| 4 | Тип возврата `_call_gigachat` | 🟢 Низкий | Низкая | 2 мин |
| 5 | `MAX_CONVERSATIONS` — единый источник | 🟢 Низкий | Низкая | 5 мин |
| 6 | `_is_rate_limit_error` — типизация | 🟢 Низкий | Низкая | 30 мин |
