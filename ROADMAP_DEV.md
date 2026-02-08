# Технический Roadmap для разработчика

> Дата: 2026-02-08 | Версия: 0.2.0 → 0.3.0  
> Основан на архитектурном аудите кодовой базы  
> Дополняет продуктовый [ROADMAP.md](ROADMAP.md) техническими деталями

---

## Обзор текущего состояния

| Метрика | Значение |
|---------|----------|
| Строки кода (src) | 2 614 |
| Строки тестов | 7 058 |
| Покрытие | 98% |
| Модулей (src) | 10 |
| Самый большой модуль | `gigachat_service.py` — 877 строк |
| In-memory state | conversations (OrderedDict), price_cache (dict), rate_limits (dict) |
| Внешние зависимости | aiogram 3, gigachat SDK, httpx, aiosqlite, pydantic-settings |

### Известный техдолг

| # | Проблема | Влияние | Приоритет |
|---|---------|---------|-----------|
| TD-1 | `gigachat_service.py` — God Class (877 строк, 7 обязанностей) | Сложность поддержки, тестирование (2055 строк тестов) | P1 |
| TD-2 | In-memory conversations — теряются при рестарте | Нет персистентности, блокирует `/reorder` | P0 |
| TD-3 | Shared mutable dict `price_cache` между процессорами | Скрытая связанность, не масштабируется | P1 |
| TD-4 | Бизнес-логика в `mcp_client.py` (clean_query, fix_cart) | Нарушение SRP транспортного слоя | P1 |
| TD-5 | Дублирование `_ensure_db` в SQLite-хранилищах | DRY, усложняет добавление новых таблиц | P2 |
| TD-6 | Config как module-level singleton | Затруднённое тестирование | P2 |
| TD-7 | Нет per-user lock (race condition при параллельных сообщениях) | Повреждение истории диалога | P1 |
| TD-8 | SSL-верификация отключена (GigaChat SDK) | Безопасность | P2 |
| TD-9 | Thread pool захардкожен (50 workers) | Нет адаптации к нагрузке | P3 |
| TD-10 | Лог-файл без ротации, нет structured logging | Операционная стабильность | P2 |

---

## Спринт 1: Фундамент (v0.2.1)

> **Цель:** подготовить кодовую базу к добавлению новых фич без деградации качества.  
> **Срок:** 3-5 дней  
> **Ветка:** `refactor/decompose-services`

### 1.1. Декомпозиция `gigachat_service.py` → 4 модуля

> Решает: TD-1, TD-7  
> Сложность: ★★☆☆☆  
> Ожидаемый результат: `gigachat_service.py` сократится с 877 до ~250 строк

#### Новые модули

**`services/dialog_manager.py`** (~120 строк)

Обязанности:
- LRU-кэш диалогов (`OrderedDict`)
- Per-user `asyncio.Lock` для защиты от race condition
- Обрезка истории (`_trim_history`)
- Сброс диалога (`reset_conversation`)
- Подготовка к персистентности (Спринт 2)

```python
import asyncio
from collections import OrderedDict
from gigachat.models import Messages, MessagesRole
from vkuswill_bot.services.prompts import SYSTEM_PROMPT

MAX_CONVERSATIONS = 1000


class DialogManager:
    """Управление историей диалогов пользователей."""

    def __init__(self, max_conversations: int = MAX_CONVERSATIONS, max_history: int = 50):
        self._max_conversations = max_conversations
        self._max_history = max_history
        self._conversations: OrderedDict[int, list[Messages]] = OrderedDict()
        self._locks: dict[int, asyncio.Lock] = {}

    def get_lock(self, user_id: int) -> asyncio.Lock:
        """Per-user lock для защиты от параллельных мутаций."""
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    def get_history(self, user_id: int) -> list[Messages]:
        """Получить или создать историю (LRU-вытеснение)."""
        ...

    def trim(self, user_id: int) -> None:
        """Обрезать историю, оставив системный промпт + последние N."""
        ...

    def reset(self, user_id: int) -> None:
        """Сбросить диалог пользователя."""
        self._conversations.pop(user_id, None)
        self._locks.pop(user_id, None)
```

Переносимые методы из `gigachat_service.py`:
- `_get_history()` → `DialogManager.get_history()`
- `_trim_history()` → `DialogManager.trim()`
- `reset_conversation()` → `DialogManager.reset()`

---

**`services/tool_executor.py`** (~200 строк)

Обязанности:
- Маршрутизация: local tools vs MCP tools
- Выполнение с обработкой ошибок
- Детекция зацикливания (дубли вызовов)
- Пре/постпроцессинг аргументов и результатов
- Парсинг предпочтений из результатов

```python
class ToolExecutor:
    """Маршрутизация и выполнение вызовов инструментов."""

    def __init__(
        self,
        mcp_client: VkusvillMCPClient,
        search_processor: SearchProcessor,
        cart_processor: CartProcessor,
        preferences_store: PreferencesStore | None = None,
    ):
        ...

    async def execute(
        self,
        tool_name: str,
        args: dict,
        user_id: int,
        user_prefs: dict[str, str],
        search_log: dict[str, set[int]],
        call_tracker: CallTracker,
    ) -> str:
        """Единая точка входа: предобработка → вызов → постобработка."""
        ...
```

Переносимые методы из `gigachat_service.py`:
- `_call_local_tool()` → `ToolExecutor._call_local()`
- `_execute_tool()` → `ToolExecutor._execute()`
- `_is_duplicate_call()` → `CallTracker.is_duplicate()`
- `_preprocess_tool_args()` → `ToolExecutor._preprocess()`
- `_postprocess_tool_result()` → `ToolExecutor._postprocess()`
- `_parse_preferences()` → `ToolExecutor._parse_preferences()`
- `_apply_preferences_to_query()` → `ToolExecutor._apply_preferences()`
- `_parse_tool_arguments()` → `ToolExecutor.parse_arguments()` (static)
- `_append_assistant_message()` → `ToolExecutor.build_assistant_message()` (static)

---

**`services/recipe_service.py`** (~180 строк)

Обязанности:
- Извлечение ингредиентов через GigaChat
- Кэширование через `RecipeStore`
- Обогащение весами (`_enrich_with_kg`)
- Масштабирование порций

```python
# Вынести из метода в константу модуля
PIECE_WEIGHT_KG: dict[str, float] = {
    "картофель": 0.15, "картошка": 0.15,
    "морковь": 0.15, "морковка": 0.15,
    ...
}


class RecipeService:
    """Извлечение и кэширование рецептов."""

    def __init__(self, gigachat_client: GigaChat, recipe_store: RecipeStore | None = None):
        ...

    async def get_ingredients(self, dish: str, servings: int = 4) -> str:
        """Главный метод: кэш → LLM-fallback → кэш → JSON."""
        ...
```

Переносимые методы из `gigachat_service.py`:
- `_handle_recipe_ingredients()` → `RecipeService.get_ingredients()`
- `_extract_recipe_from_llm()` → `RecipeService._extract_from_llm()`
- `_enrich_with_kg()` → `RecipeService._enrich_with_kg()`
- `_format_recipe_result()` → `RecipeService._format_result()`
- `_parse_json_from_llm()` → `RecipeService._parse_json()`
- `PIECE_WEIGHT_KG` → модульная константа

---

**`gigachat_service.py` остаётся оркестратором** (~250 строк)

```python
class GigaChatService:
    """Оркестратор: function calling loop."""

    def __init__(
        self,
        credentials: str,
        model: str,
        scope: str,
        dialog_manager: DialogManager,
        tool_executor: ToolExecutor,
        mcp_client: VkusvillMCPClient,
        recipe_service: RecipeService | None = None,
        max_tool_calls: int = 20,
    ):
        ...

    async def process_message(self, user_id: int, text: str) -> str:
        """Function calling loop — основная логика."""
        async with self._dialog_manager.get_lock(user_id):
            ...

    async def _get_functions(self) -> list[dict]:
        ...

    def reset_conversation(self, user_id: int) -> None:
        self._dialog_manager.reset(user_id)

    async def close(self) -> None:
        ...
```

#### План миграции

```
Фаза 1: Создание модулей (без изменения gigachat_service.py)
  1. Создать services/dialog_manager.py + тесты
  2. Создать services/tool_executor.py + тесты
  3. Создать services/recipe_service.py + тесты
  4. Убедиться: uv run pytest -v (все тесты зелёные)

Фаза 2: Переключение (один модуль за раз)
  5. gigachat_service.py → использует DialogManager
  6. uv run pytest -v
  7. gigachat_service.py → использует ToolExecutor
  8. uv run pytest -v
  9. gigachat_service.py → использует RecipeService
  10. uv run pytest -v

Фаза 3: Очистка
  11. Удалить мёртвый код из gigachat_service.py
  12. Обновить __main__.py (DI для новых модулей)
  13. uv run pytest -v && uv run pytest --cov
  14. Проверить: покрытие >= 95%
```

#### Критерии приёмки

- [ ] `gigachat_service.py` <= 300 строк
- [ ] Все существующие тесты проходят без изменений
- [ ] Покрытие >= 95%
- [ ] Нет циклических зависимостей
- [ ] `process_message` использует per-user lock

---

### 1.2. Очистка MCP-клиента (SRP)

> Решает: TD-4  
> Сложность: ★☆☆☆☆  
> Срок: 0.5 дня

**Что переносим:**

| Метод/константа | Из | В |
|---|---|---|
| `_clean_search_query()` | `mcp_client.py:334-353` | `SearchProcessor` |
| `_fix_cart_args()` | `mcp_client.py:277-318` | `CartProcessor` |
| `SEARCH_LIMIT = 5` | `mcp_client.py:321` | `SearchProcessor` |
| `_UNIT_PATTERN`, `_STANDALONE_NUM` | `mcp_client.py:325-332` | `SearchProcessor` |

**После переноса `mcp_client.py` содержит только:**
- Инициализация HTTP-соединения
- JSON-RPC вызовы (`_rpc_call`, `_rpc_notify`)
- SSE-парсинг
- Retry + session management
- `get_tools()`, `call_tool(name, args) -> str`

**Вызывающий код меняется в `tool_executor.py`:**
```python
# Было (в mcp_client.call_tool):
if name == "vkusvill_products_search":
    arguments = self._clean_and_limit(arguments)

# Стало (в tool_executor._preprocess):
if tool_name == "vkusvill_products_search":
    args = self._search_processor.clean_and_limit(args)
```

#### Критерии приёмки

- [ ] `mcp_client.py` не содержит бизнес-логики (поиск, корзина)
- [ ] `SearchProcessor` владеет `_clean_search_query` и `SEARCH_LIMIT`
- [ ] `CartProcessor` владеет `_fix_cart_args`
- [ ] `search_processor.py` не импортирует `VkusvillMCPClient`
- [ ] Все тесты зелёные

---

### 1.3. Выделение `PriceCache`

> Решает: TD-3  
> Сложность: ★☆☆☆☆  
> Срок: 0.5 дня

**Новый файл: `services/price_cache.py`** (~60 строк)

```python
import logging

logger = logging.getLogger(__name__)

MAX_PRICE_CACHE_SIZE = 5000


class PriceInfo:
    """Кэшированная информация о цене товара."""
    __slots__ = ("name", "price", "unit")

    def __init__(self, name: str, price: float, unit: str = "шт"):
        self.name = name
        self.price = price
        self.unit = unit


class PriceCache:
    """Кэш цен товаров ВкусВилл (xml_id → PriceInfo).

    FIFO-вытеснение при превышении лимита.
    Готов к замене на Redis в будущем.
    """

    def __init__(self, max_size: int = MAX_PRICE_CACHE_SIZE):
        self._max_size = max_size
        self._data: dict[int, PriceInfo] = {}

    def set(self, xml_id: int, name: str, price: float, unit: str = "шт") -> None:
        self._data[xml_id] = PriceInfo(name, price, unit)
        self._evict_if_needed()

    def get(self, xml_id: int) -> PriceInfo | None:
        return self._data.get(xml_id)

    def __len__(self) -> int:
        return len(self._data)

    def _evict_if_needed(self) -> None:
        if len(self._data) > self._max_size:
            keys = list(self._data.keys())[: self._max_size // 2]
            for k in keys:
                del self._data[k]
            logger.info("PriceCache: evicted %d entries", len(keys))
```

**Изменения в существующих модулях:**

```python
# search_processor.py — вместо dict
class SearchProcessor:
    def __init__(self, price_cache: PriceCache):
        self._price_cache = price_cache

    def cache_prices(self, result_text: str) -> None:
        ...
        self._price_cache.set(xml_id, name, price, unit)

# cart_processor.py — вместо dict
class CartProcessor:
    def __init__(self, price_cache: PriceCache):
        self._price_cache = price_cache

# __main__.py — DI
price_cache = PriceCache()
search_processor = SearchProcessor(price_cache)
cart_processor = CartProcessor(price_cache)
```

#### Критерии приёмки

- [ ] `PriceCache` — единственный владелец данных о ценах
- [ ] `SearchProcessor` и `CartProcessor` получают `PriceCache` через DI
- [ ] Нет shared mutable `dict` между объектами
- [ ] Все тесты зелёные

---

## Спринт 2: Персистентность (v0.2.2)

> **Цель:** данные не теряются при рестарте.  
> **Срок:** 2-3 дня  
> **Ветка:** `feat/persistent-dialogs`  
> **Зависимости:** Спринт 1 (DialogManager уже выделен)

### 2.1. Базовый класс `BaseSQLiteStore`

> Решает: TD-5  
> Сложность: ★☆☆☆☆  
> Срок: 0.5 дня

**Новый файл: `services/base_store.py`** (~50 строк)

```python
import logging
import os
import aiosqlite

logger = logging.getLogger(__name__)


class BaseSQLiteStore:
    """Базовый класс для SQLite-хранилищ.

    Обеспечивает lazy-инициализацию, создание директории,
    миграцию схемы и graceful shutdown.
    """

    _CREATE_TABLE_SQL: str = ""  # переопределяется в подклассах

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _ensure_db(self) -> aiosqlite.Connection:
        if self._db is None:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute(self._CREATE_TABLE_SQL)
            await self._db.commit()
            logger.info("SQLite opened: %s (%s)", self._db_path, type(self).__name__)
        return self._db

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("SQLite closed: %s", type(self).__name__)
```

**Рефакторинг существующих хранилищ:**

```python
# preferences_store.py
class PreferencesStore(BaseSQLiteStore):
    _CREATE_TABLE_SQL = """..."""

    async def get_all(self, user_id: int) -> list[dict]:
        db = await self._ensure_db()
        ...

# recipe_store.py
class RecipeStore(BaseSQLiteStore):
    _CREATE_TABLE_SQL = """..."""
    ...
```

Удаляется ~30 строк дублированного кода.

#### Критерии приёмки

- [ ] `PreferencesStore` и `RecipeStore` наследуют `BaseSQLiteStore`
- [ ] Дублированный код `_ensure_db` / `close` удалён
- [ ] Все тесты `test_preferences_store.py` и `test_recipe_store.py` зелёные

---

### 2.2. Персистентные диалоги

> Решает: TD-2  
> Сложность: ★★★☆☆  
> Срок: 2 дня  
> Блокирует: ROADMAP #8 (повторный заказ)

**Изменения в `services/dialog_manager.py`:**

```python
class DialogManager:
    """Управление историей диалогов с SQLite-персистентностью."""

    _CREATE_TABLE_SQL = """\
    CREATE TABLE IF NOT EXISTS conversations (
        user_id    INTEGER PRIMARY KEY,
        messages   TEXT    NOT NULL,
        updated_at TEXT    DEFAULT CURRENT_TIMESTAMP
    )
    """

    def __init__(
        self,
        db_path: str,
        max_conversations: int = 1000,
        max_history: int = 50,
    ):
        self._db_path = db_path
        self._max_conversations = max_conversations
        self._max_history = max_history
        # In-memory LRU кэш (горячие диалоги)
        self._cache: OrderedDict[int, list[Messages]] = OrderedDict()
        self._locks: dict[int, asyncio.Lock] = {}
        self._db: aiosqlite.Connection | None = None

    async def get_history(self, user_id: int) -> list[Messages]:
        """Кэш → SQLite → новый диалог."""
        if user_id in self._cache:
            self._cache.move_to_end(user_id)
            return self._cache[user_id]

        # Загрузить из SQLite
        history = await self._load_from_db(user_id)
        if history is None:
            history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]

        self._cache[user_id] = history
        self._evict_if_needed()
        return history

    async def save(self, user_id: int) -> None:
        """Сохранить текущую историю в SQLite."""
        history = self._cache.get(user_id)
        if history:
            await self._save_to_db(user_id, history)

    async def _load_from_db(self, user_id: int) -> list[Messages] | None:
        """Десериализация Messages из JSON."""
        ...

    async def _save_to_db(self, user_id: int, messages: list[Messages]) -> None:
        """Сериализация Messages в JSON."""
        ...
```

**Сериализация Messages:**

```python
def _serialize_messages(self, messages: list[Messages]) -> str:
    """Messages → JSON для SQLite."""
    data = []
    for msg in messages:
        item = {"role": msg.role.value, "content": msg.content or ""}
        if msg.function_call:
            item["function_call"] = {
                "name": msg.function_call.name,
                "arguments": msg.function_call.arguments,
            }
        if hasattr(msg, "name") and msg.name:
            item["name"] = msg.name
        data.append(item)
    return json.dumps(data, ensure_ascii=False)

def _deserialize_messages(self, raw: str) -> list[Messages]:
    """JSON → Messages из SQLite."""
    data = json.loads(raw)
    messages = []
    for item in data:
        msg = Messages(
            role=MessagesRole(item["role"]),
            content=item.get("content", ""),
        )
        if "function_call" in item:
            ...
        if "name" in item:
            msg.name = item["name"]
        messages.append(msg)
    return messages
```

**Точки сохранения в `gigachat_service.py`:**

```python
async def process_message(self, user_id: int, text: str) -> str:
    async with self._dialog_manager.get_lock(user_id):
        history = await self._dialog_manager.get_history(user_id)
        ...
        # После получения финального ответа
        self._dialog_manager.trim(user_id)
        await self._dialog_manager.save(user_id)  # <-- persist
        return msg.content
```

#### Критерии приёмки

- [ ] Диалоги сохраняются в SQLite после каждого ответа
- [ ] При рестарте бота диалоги загружаются из SQLite
- [ ] LRU-кэш в памяти работает как раньше (hot path без I/O)
- [ ] `reset_conversation` удаляет из SQLite и из кэша
- [ ] Тесты: сохранение, загрузка, сериализация, LRU-вытеснение
- [ ] Покрытие >= 95%

---

## Спринт 3: Качество и безопасность (v0.2.3)

> **Цель:** observability, тестируемость, безопасность.  
> **Срок:** 2-3 дня  
> **Ветка:** `chore/quality-improvements`

### 3.1. Config через DI

> Решает: TD-6  
> Сложность: ★☆☆☆☆  
> Срок: 0.5 дня

**Изменения в `config.py`:**

```python
class Config(BaseSettings):
    ...
    # Новые поля (из хардкода)
    thread_pool_workers: int = 20
    rate_limit: int = 5
    rate_period: float = 60.0

# Убрать module-level singleton:
# config = Config()  # УДАЛИТЬ

# Вместо этого — фабрика:
def load_config() -> Config:
    """Загрузить конфигурацию из .env."""
    return Config()
```

**Изменения в `__main__.py`:**

```python
from vkuswill_bot.config import Config, load_config

async def main() -> None:
    config = load_config()  # явное создание
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=config.thread_pool_workers))
    ...
```

#### Критерии приёмки

- [ ] `config.py` не создаёт инстанс на уровне модуля
- [ ] `__main__.py` создаёт `Config` явно и прокидывает через DI
- [ ] `thread_pool_workers` и `rate_limit/rate_period` в Config
- [ ] Тесты не зависят от `.env` файла

---

### 3.2. Structured logging

> Решает: TD-10  
> Сложность: ★★☆☆☆  
> Срок: 1 день

**Зависимость:** `uv add structlog`

**Новый файл: `services/logging_config.py`** (~40 строк)

```python
import logging
import structlog

def setup_logging(debug: bool = False, json_output: bool = False) -> None:
    """Настройка structured logging."""
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_output:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(structlog.stdlib.ProcessorFormatter(
        processors=[*shared_processors, renderer],
    ))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.DEBUG if debug else logging.INFO)
```

**Использование в модулях:**

```python
# Вместо:
logger.info("Вызов инструмента: %s(%s)", tool_name, json.dumps(args))

# Станет:
logger.info("tool_call", tool_name=tool_name, args=args, user_id=user_id)
```

Это подготовит к интеграции с Sentry (ROADMAP #18) и бизнес-метрикам (ROADMAP #17).

#### Критерии приёмки

- [ ] Все логи содержат структурированные поля (tool_name, user_id, duration)
- [ ] В production — JSON-формат для агрегации
- [ ] В development — human-readable консоль
- [ ] Убран `FileHandler("bot.log")` — логирование в stdout
- [ ] Тесты не сломаны

---

### 3.3. SSL-сертификат для GigaChat

> Решает: TD-8  
> Сложность: ★★☆☆☆  
> Срок: 0.5 дня

**Шаги:**

1. Скачать CA-сертификат Минцифры (Russian Trusted Root CA)
2. Поместить в `certs/russian_trusted_root_ca.pem`
3. Добавить в `.gitignore`: `certs/` (сертификат не секрет, но лучше отдельно)
4. Обновить `Config`:
   ```python
   gigachat_ca_bundle: str | None = None  # путь к CA-сертификату
   ```
5. Обновить инициализацию GigaChat:
   ```python
   self._client = GigaChat(
       credentials=credentials,
       model=model,
       scope=scope,
       verify_ssl_certs=bool(ca_bundle),
       ca_bundle_file=ca_bundle,
       timeout=60,
   )
   ```

#### Критерии приёмки

- [ ] При наличии `gigachat_ca_bundle` — SSL включён
- [ ] При отсутствии — поведение как раньше (для dev)
- [ ] Документация в README (секция "Безопасность")

---

## Итоговая целевая структура после Спринтов 1-3

```
src/vkuswill_bot/
├── __main__.py                  # Точка входа, DI (~130 строк)
├── config.py                    # Конфигурация, load_config() (~45 строк)
├── bot/
│   ├── handlers.py              # Telegram-хендлеры (~150 строк)
│   └── middlewares.py           # ThrottlingMiddleware (~90 строк)
└── services/
    ├── base_store.py            # ← НОВЫЙ: базовый SQLite-класс (~50 строк)
    ├── dialog_manager.py        # ← НОВЫЙ: LRU + persist + per-user lock (~180 строк)
    ├── gigachat_service.py      # Оркестратор (~250 строк, было 877)
    ├── logging_config.py        # ← НОВЫЙ: structured logging (~40 строк)
    ├── mcp_client.py            # Чистый HTTP-транспорт (~300 строк, было 416)
    ├── price_cache.py           # ← НОВЫЙ: кэш цен (~60 строк)
    ├── recipe_service.py        # ← НОВЫЙ: рецепты + LLM (~180 строк)
    ├── tool_executor.py         # ← НОВЫЙ: маршрутизация инструментов (~200 строк)
    ├── search_processor.py      # Поиск + clean_query (~160 строк)
    ├── cart_processor.py        # Корзина + fix_cart_args (~270 строк)
    ├── preferences_store.py     # SQLite предпочтения (~100 строк)
    ├── recipe_store.py          # SQLite рецепты (~90 строк)
    └── prompts.py               # Промпты и константы (~130 строк)
```

**Метрики до/после:**

| Метрика | До | После |
|---------|:---:|:---:|
| Макс. размер модуля | 877 строк | ~300 строк |
| Модулей (services) | 7 | 12 |
| God Classes | 1 | 0 |
| Shared mutable state | 1 (price_cache dict) | 0 |
| Per-user lock | нет | есть |
| Персистентность диалогов | нет | SQLite |
| Structured logging | нет | structlog |
| SSL verification | отключена | опциональна |
| Дублирование (SQLite stores) | 30 строк | 0 |

---

## Зависимости между спринтами

```
Спринт 1 (Фундамент)
├── 1.1 Декомпозиция gigachat_service
├── 1.2 Очистка MCP-клиента
└── 1.3 PriceCache
        │
        ▼
Спринт 2 (Персистентность)
├── 2.1 BaseSQLiteStore
└── 2.2 Персистентные диалоги ──► разблокирует ROADMAP #8 (/reorder)
        │
        ▼
Спринт 3 (Качество)
├── 3.1 Config через DI
├── 3.2 Structured logging ──► подготовка к ROADMAP #17 (метрики), #18 (Sentry)
└── 3.3 SSL-сертификат
        │
        ▼
Продуктовые фичи (ROADMAP.md Волна 1)
├── Голосовые сообщения (#1) ← независимо от спринтов
├── Inline-кнопки (#2) ← после Спринта 1
├── /reorder (#8) ← после Спринта 2
└── Sentry (#18) ← после Спринта 3
```

---

## Чеклист для каждой задачи

Перед мержем PR убедись:

```
- [ ] Все тесты зелёные: uv run pytest -v
- [ ] Покрытие >= 95%: uv run pytest --cov --cov-report=term-missing
- [ ] Линтер чист: uv run ruff check src/ tests/
- [ ] Нет циклических импортов
- [ ] Нет новых module-level side effects
- [ ] PR описывает что и зачем (не только что)
- [ ] CHANGELOG.md обновлён
```

---

## Принципы

1. **Один PR — одна задача.** Не мешать рефакторинг с фичами.
2. **Тесты первыми.** Сначала тесты для нового модуля, потом перенос кода.
3. **Обратная совместимость.** Существующие тесты не должны меняться (только импорты).
4. **Инкрементально.** Каждый коммит — рабочее состояние, `pytest` зелёный.
5. **Не абстрагируй преждевременно.** Если паттерн встречается дважды — терпи. Трижды — рефактори.
