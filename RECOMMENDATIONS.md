# Рекомендации по улучшению vkuswill-bot

> На основе code review от 06.02.2026 (ревью #3)
>
> **Текущее состояние:** 469 тестов, 98% покрытие, 0 блокеров
>
> **Оценка:** 💬 Comment — код готов к production, есть улучшения

---

## Статус предыдущих рекомендаций

| # | Рекомендация | Статус |
|---|---|---|
| 1 | Rate-limiting пользователей | ✅ Выполнено — `ThrottlingMiddleware` |
| 2 | LRU-вытеснение диалогов | ✅ Выполнено — `OrderedDict` + `MAX_CONVERSATIONS` |
| 3 | Ограничение длины сообщения | ✅ Выполнено — `MAX_USER_MESSAGE_LENGTH = 4096` |
| 4 | Graceful shutdown | ✅ Выполнено — `asyncio.Event` + `SIGTERM`/`SIGINT` |
| 5 | SSL-верификация GigaChat | ⏳ Ожидание SDK — TODO-комментарий добавлен |
| 6 | Логирование исключений | ✅ Выполнено — `logger.debug` вместо `pass` |
| 7 | Дедупликация тестовых хелперов | ✅ Выполнено — `tests/helpers.py` |
| 8 | Синхронизация версии MCP | ✅ Выполнено — `importlib.metadata` |
| 9 | Расширить `.gitignore` | ✅ Выполнено — `*.log`, `*.db`, etc. |
| 10 | Персистентные диалоги | 🔄 Частично — `PreferencesStore` (SQLite) для предпочтений, диалоги в памяти |
| 11 | Пул потоков GigaChat | ✅ Выполнено — `ThreadPoolExecutor(max_workers=50)` |
| 12 | Покрытие кода 97%+ | ✅ Выполнено — 98% |

---

## Критические (устранить до production)

### 1. Добавить `data/` и `*.db` в `.gitignore`

**Риск:** файл БД предпочтений (`data/preferences.db`) с данными пользователей попадёт в Git-репозиторий

**Текущее состояние:** `config.py` задаёт `database_path = "data/preferences.db"`, но ни `data/`, ни `*.db` не указаны в `.gitignore`

**Что сделать:**
- [ ] Добавить в `.gitignore`:

```gitignore
# База данных
data/
*.db
*.db-journal
*.db-wal
```

**Файлы:** `.gitignore`
**Оценка:** 1 минута

---

### 2. Добавить `DATABASE_PATH` в `.env.example`

**Риск:** разработчик не узнает о настройке пути к БД предпочтений

**Текущее состояние:** в `config.py` добавлено поле `database_path`, но `.env.example` не обновлён

**Что сделать:**
- [ ] Добавить в `.env.example`:

```env
# Хранилище предпочтений (SQLite)
DATABASE_PATH=data/preferences.db

# Лимиты
MAX_TOOL_CALLS=15
MAX_HISTORY_MESSAGES=50
```

**Файлы:** `.env.example`
**Оценка:** 1 минута

---

## Важные (ближайший спринт)

### 3. Ограничить рост `_price_cache` (TTL + maxsize)

**Риск:** утечка памяти — кеш цен растёт неограниченно, цены устаревают

**Текущее состояние:** `self._price_cache: dict[int, dict] = {}` — без лимитов и очистки

**Что сделать:**
- [ ] Установить `cachetools`:
  ```bash
  uv add cachetools
  ```
- [ ] Заменить `dict` на `TTLCache`:

```python
from cachetools import TTLCache

# Лимит 5000 записей, TTL 30 минут
PRICE_CACHE_MAXSIZE = 5000
PRICE_CACHE_TTL = 1800  # секунды

class GigaChatService:
    def __init__(self, ...) -> None:
        ...
        self._price_cache: TTLCache[int, dict] = TTLCache(
            maxsize=PRICE_CACHE_MAXSIZE,
            ttl=PRICE_CACHE_TTL,
        )
```

- [ ] Добавить тест: записать > `PRICE_CACHE_MAXSIZE` элементов → старые вытеснены

**Альтернатива (без новой зависимости):**

```python
MAX_PRICE_CACHE_SIZE = 5000

def _cache_prices_from_search(self, result_text: str) -> None:
    # ... парсинг ...
    if len(self._price_cache) > MAX_PRICE_CACHE_SIZE:
        # Удаляем первые N записей (приблизительно FIFO)
        keys_to_remove = list(self._price_cache.keys())[:MAX_PRICE_CACHE_SIZE // 2]
        for k in keys_to_remove:
            del self._price_cache[k]
        logger.info("Очищен кеш цен: удалено %d записей", len(keys_to_remove))
```

**Файлы:** `gigachat_service.py`, `pyproject.toml`
**Оценка:** 15 минут

---

### 4. Проверка ключа `"data"` в `_calc_cart_total` и верификации корзины

**Риск:** `KeyError` при нестандартном ответе MCP (например `{"ok": true}` без `"data"`)

**Текущее состояние:**

```python
# gigachat_service.py:505 — прямой доступ без проверки
result_data["data"]["price_summary"] = summary

# gigachat_service.py:833 — обёрнуто в try/except KeyError, но это workaround
result_data["data"]["verification"] = verification
```

**Что сделать:**
- [ ] Добавить проверку перед записью:

```python
# _calc_cart_total
data = result_data.get("data")
if not isinstance(data, dict):
    logger.warning("Результат корзины без поля 'data': %s", result_text[:200])
    return result_text
data["price_summary"] = summary

# process_message, блок верификации
if search_log:
    verification = self._verify_cart(args, search_log)
    try:
        result_data = json.loads(result)
        data = result_data.get("data")
        if isinstance(data, dict):
            data["verification"] = verification
            result = json.dumps(result_data, ensure_ascii=False, indent=4)
    except (json.JSONDecodeError, TypeError):
        pass
```

- [ ] Добавить тест: `_calc_cart_total` с `{"ok": true}` без `"data"` → не падает

**Файлы:** `gigachat_service.py`, `test_gigachat_service.py`
**Оценка:** 10 минут

---

### 5. Перенести импорты `copy` и `math` в начало файла

**Проблема:** нестандартные импорты внутри методов, ухудшают читаемость

**Текущее состояние:**

```python
# gigachat_service.py:196 — внутри _enhance_cart_schema
import copy

# gigachat_service.py:430 — внутри _fix_unit_quantities
import math
```

**Что сделать:**
- [ ] Перенести оба импорта в начало файла (после `from collections import OrderedDict`):

```python
import asyncio
import copy
import json
import logging
import math
from collections import OrderedDict
```

- [ ] Убрать дублирующий локальный импорт `VkusvillMCPClient` из `_trim_search_result` (строка 381) — класс уже импортирован в начале файла:

```python
# Было (строка 381-383):
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
max_items = VkusvillMCPClient.SEARCH_LIMIT

# Стало:
max_items = self._mcp_client.SEARCH_LIMIT
# или вынести константу: SEARCH_LIMIT = 5
```

**Файлы:** `gigachat_service.py`
**Оценка:** 2 минуты

---

## Желательные (бэклог)

### 6. Рефакторинг `gigachat_service.py` — разделение на подмодули

**Проблема:** файл вырос до 859 строк и несёт 10+ ответственностей

**Текущие ответственности:**
1. Управление историей диалогов (LRU, trim, reset)
2. Цикл function calling с GigaChat API
3. Кеширование цен из результатов поиска
4. Расчёт стоимости корзины
5. Верификация корзины vs поисковые запросы
6. Обрезка результатов поиска (trim fields)
7. Маршрутизация локальных инструментов (preferences)
8. Подстановка предпочтений в поисковые запросы
9. Округление единиц для штучных товаров
10. Обогащение JSON-схемы корзины для GigaChat

**Предлагаемая структура:**

```
services/
├── gigachat_service.py         # Основной цикл + история (300 строк)
│   - GigaChatService.__init__
│   - _get_history, _trim_history, reset_conversation
│   - _get_functions
│   - process_message (основной цикл)
│   - close
│
├── cart_processor.py            # Логика корзины (200 строк)
│   - CartProcessor
│   - cache_prices_from_search
│   - calc_cart_total
│   - verify_cart
│   - fix_unit_quantities
│   - extract_xml_ids_from_search
│   - enhance_cart_schema
│
├── search_processor.py          # Логика поиска (100 строк)
│   - SearchProcessor
│   - trim_search_result
│   - SEARCH_ITEM_FIELDS
│
├── preferences_engine.py        # Логика предпочтений (150 строк)
│   - PreferencesEngine
│   - parse_preferences
│   - apply_preferences_to_query
│   - call_local_tool
│   - LOCAL_TOOLS, LOCAL_TOOL_NAMES
│
├── preferences_store.py         # SQLite-хранилище (без изменений)
└── mcp_client.py                # MCP-клиент (без изменений)
```

**Пример рефакторинга `CartProcessor`:**

```python
# services/cart_processor.py
"""Обработка корзины: кеш цен, расчёт стоимости, верификация."""

import json
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

DISCRETE_UNITS = frozenset({"шт", "уп", "пач", "бут", "бан", "пак"})


class CartProcessor:
    """Обработчик операций с корзиной ВкусВилл."""

    def __init__(self) -> None:
        self.price_cache: dict[int, dict] = {}

    def cache_prices_from_search(self, result_text: str) -> None:
        """Извлечь цены из результата поиска и закешировать."""
        ...

    def fix_unit_quantities(self, args: dict) -> dict:
        """Округлить q для штучных товаров."""
        ...

    def calc_total(self, args: dict, result_text: str) -> str:
        """Рассчитать стоимость корзины."""
        ...

    def verify(self, cart_args: dict, search_log: dict[str, set[int]]) -> dict:
        """Сопоставить корзину с поисковыми запросами."""
        ...
```

**Использование в `GigaChatService`:**

```python
# gigachat_service.py
from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.preferences_engine import PreferencesEngine

class GigaChatService:
    def __init__(self, ...) -> None:
        ...
        self._cart = CartProcessor()
        self._search = SearchProcessor()
        self._prefs_engine = PreferencesEngine(preferences_store)

    async def process_message(self, user_id: int, text: str) -> str:
        ...
        # Вместо self._cache_prices_from_search(result)
        self._cart.cache_prices_from_search(result)
        # Вместо self._trim_search_result(result)
        result = self._search.trim_result(result)
        # Вместо self._apply_preferences_to_query(q, user_prefs)
        enhanced_q = self._prefs_engine.apply_to_query(q, user_prefs)
```

**Миграция тестов:**

```
tests/
├── test_gigachat_service.py     # Только цикл + история (300 строк)
├── test_cart_processor.py       # TestCachePrices, TestCalcCartTotal, TestVerifyCart, TestFixUnit
├── test_search_processor.py     # TestTrimSearchResult
├── test_preferences_engine.py   # TestParsePreferences, TestApplyPreferences, TestCallLocalTool
├── test_preferences_store.py    # Без изменений
└── ...
```

**Оценка:** 2-4 часа
**Рекомендация:** делать поэтапно — сначала `CartProcessor`, потом `SearchProcessor`, потом `PreferencesEngine`. Каждый этап — отдельный PR.

---

### 7. Персистентное хранение диалогов

**Проблема:** при перезапуске бота все диалоги теряются (в памяти `OrderedDict`)

**Что сделать:**
- [ ] Добавить таблицу `conversations` в `PreferencesStore` (или отдельный `ConversationStore`)
- [ ] Сериализация `Messages` → JSON для хранения в SQLite
- [ ] TTL для диалогов (например, 24 часа)
- [ ] Lazy-загрузка: читать из БД только при обращении к пользователю

**Пример схемы:**

```sql
CREATE TABLE IF NOT EXISTS conversations (
    user_id     INTEGER PRIMARY KEY,
    messages    TEXT    NOT NULL,  -- JSON-массив сообщений
    updated_at  TEXT    DEFAULT CURRENT_TIMESTAMP
);
```

**Файлы:** новый `services/conversation_store.py` или расширение `preferences_store.py`
**Оценка:** 4-8 часов

---

### 8. SSL-верификация GigaChat (отложено)

**Статус:** ожидание поддержки CA-сертификата Минцифры в GigaChat SDK

**Что отслеживать:**
- [ ] [GigaChat SDK issues](https://github.com/ai-forever/gigachat/issues)
- [ ] Обновления SDK через `uv update gigachat`

**Когда будет готово:**
- [ ] Удалить `verify_ssl_certs=False` из `__init__`
- [ ] Добавить `ca_bundle_file` в `config.py` (если нужен кастомный CA)
- [ ] Убрать `xfail` с теста `TestSSLSecurity::test_ssl_verification_settings`

---

## Инструменты — уже настроенные

| Инструмент | Статус | Файл |
|---|---|---|
| CI/CD (тесты, lint) | ✅ | `.github/workflows/ci.yml` |
| Release workflow | ✅ | `.github/workflows/release.yml` |
| Git hooks | ✅ | `.githooks/commit-msg`, `pre-push` |
| SAST-тесты | ✅ | `test_security_sast.py` |
| AI Safety тесты | ✅ | `test_ai_safety.py` |
| Input validation | ✅ | `test_input_validation.py` |
| Config security | ✅ | `test_config_security.py` |
| Ruff (lint + format) | ✅ | `pyproject.toml [dev]` |
| Makefile | ✅ | `Makefile` |
| Issue/PR templates | ✅ | `.github/` |

---

## Сводная таблица

| # | Рекомендация | Приоритет | Сложность | Время |
|---|---|---|---|---|
| 1 | `.gitignore`: `data/`, `*.db` | 🔴 Критический | Низкая | 1 мин |
| 2 | `.env.example`: `DATABASE_PATH` | 🔴 Критический | Низкая | 1 мин |
| 3 | TTL/лимит для `_price_cache` | 🟡 Важный | Низкая | 15 мин |
| 4 | Проверка `"data"` в cart/verify | 🟡 Важный | Низкая | 10 мин |
| 5 | Импорты `copy`, `math` в начало файла | 🟡 Важный | Низкая | 2 мин |
| 6 | Рефакторинг `gigachat_service.py` | 🟢 Желательный | Высокая | 2-4 ч |
| 7 | Персистентные диалоги (SQLite) | 🟢 Желательный | Высокая | 4-8 ч |
| 8 | SSL-верификация GigaChat | 🟢 Желательный | Средняя | Ожидание SDK |
