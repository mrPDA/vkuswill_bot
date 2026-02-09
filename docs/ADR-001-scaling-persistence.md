# ADR-001: Масштабирование и персистентность для продакшн-запуска

> **Статус:** Предложено  
> **Дата:** 2026-02-08  
> **Автор:** Архитектор-аналитик (AI)  
> **Версия проекта:** 0.3.0  
> **Контекст:** подготовка к публикации на Хабре и деплою в Yandex Cloud

---

## 1. Контекст и мотивация

Бот ВкусВилл (v0.3.0) полностью функционален: GigaChat + MCP, поиск товаров, сборка корзины, предпочтения, рецепты, 98% покрытие тестами. Планируется публикация статьи на Хабре, после которой ожидается наплыв **300–1000 пользователей** в первые дни.

Текущая архитектура — **single-process, in-memory** — не готова к этому сценарию по двум причинам:

1. **Масштабируемость** — единственный процесс с in-memory состоянием не выдержит конкурентную нагрузку на GigaChat API и не масштабируется горизонтально.
2. **Персистентность** — история диалогов и данные корзины теряются при любом рестарте, что критично для UX.

---

## 2. Анализ текущих проблем

### 2.1. [P0] Диалоги теряются при рестарте

**Где:** `services/dialog_manager.py`

```python
# Строки 38-39 — всё хранится в оперативной памяти
self._conversations: OrderedDict[int, list[Messages]] = OrderedDict()
self._locks: dict[int, asyncio.Lock] = {}
```

**Проблема:**  
- При рестарте бота (деплой, краш, обновление) — все диалоги обнуляются.
- При LRU-вытеснении (>1000 диалогов) — пользователь теряет контекст без уведомления.
- Нет TTL — диалог висит в памяти до вытеснения, даже если пользователь ушёл неделю назад.

**Сценарий пользователя:**  
Пользователь собрал корзину на 2000 руб, попросил заменить товар, отвлёкся на час. Вернулся — а бот его не помнит (был рестарт). Пользователь уходит навсегда.

**Влияние:** Критическое. Блокирует фичи `/reorder`, `/cart`, именованные списки.

---

### 2.2. [P0] Нет глобального rate limit на GigaChat API

**Где:** `services/gigachat_service.py`

```python
# Строки 218-221 — каждый пользователь вызывает API без ограничений
response = await asyncio.to_thread(
    self._client.chat,
    Chat(messages=history, functions=functions, function_call="auto"),
)
```

**Проблема:**  
- При 200 одновременных пользователях → 200 параллельных запросов к GigaChat API.
- Один запрос пользователя может вызвать до 20 итераций function calling (`max_tool_calls=20`).
- Thread pool (50 воркеров) — физический потолок, но GigaChat API может вернуть 429 раньше.
- Нет retry с exponential backoff при 429.

**Сценарий:**  
500 человек пришли с Хабра, 50 написали одновременно. 50 × 3 средних tool-call итерации = 150 запросов к GigaChat за секунды. API возвращает 429 → все 50 получают "Произошла ошибка".

**Влияние:** Критическое. Массовый отказ при пиковой нагрузке.

---

### 2.3. [P0] Polling вместо Webhook

**Где:** `__main__.py`

```python
# Строка 125 — long polling
polling_task = asyncio.create_task(dp.start_polling(bot))
```

**Проблема:**  
- Long polling создаёт постоянное HTTP-соединение к Telegram API, бот сам тянет обновления.
- Неэффективно: при отсутствии сообщений — idle-соединение; при наплыве — задержки в получении обновлений.
- Не работает за Load Balancer (нужно для горизонтального масштабирования в будущем).

**Влияние:** Среднее. Polling работает, но webhook эффективнее под нагрузкой.

---

### 2.4. [P1] Price Cache в оперативной памяти

**Где:** `services/price_cache.py`

```python
# Строки 48-50
def __init__(self, max_size: int = MAX_PRICE_CACHE_SIZE) -> None:
    self._max_size = max_size
    self._data: dict[int, PriceInfo] = {}
```

**Проблема:**  
- Кэш теряется при рестарте — первые пользователи после деплоя не получат расчёт стоимости корзины.
- Не шарится между инстансами (при горизонтальном масштабировании).
- FIFO-вытеснение удаляет 50% кэша разом — spike нагрузки на MCP при массовом вытеснении.
- Нет TTL — цены могут устареть (ВкусВилл меняет цены ежедневно).

**Влияние:** Среднее. Деградирует UX (нет расчёта стоимости), но не ломает основной функционал.

---

### 2.5. [P1] SQLite — single-writer bottleneck

**Где:** `services/preferences_store.py`, `services/recipe_store.py`

```python
# Строки 36-40 — одно соединение на всё приложение
self._db: aiosqlite.Connection | None = None

async def _ensure_db(self) -> aiosqlite.Connection:
    if self._db is None:
        self._db = await aiosqlite.connect(self._db_path)
```

**Проблема:**  
- SQLite WAL позволяет параллельные чтения, но **записи сериализуются**.
- Одно соединение — все операции (read + write) проходят через одну точку.
- При 100+ пользователях, сохраняющих предпочтения одновременно — очередь записей.
- SQLite файл привязан к файловой системе контейнера — теряется при пересоздании контейнера.

**Влияние:** Среднее. При текущих объёмах (только preferences) — терпимо. При добавлении /reorder, списков, аналитики — станет критичным.

---

### 2.6. [P1] Rate limiter в оперативной памяти

**Где:** `bot/middlewares.py`

```python
# Строка 48 — in-memory словарь
self._user_timestamps: dict[int, list[float]] = {}
```

**Проблема:**  
- При рестарте — все лимиты сбрасываются (пользователь может спамить сразу после рестарта).
- При нескольких инстансах — каждый считает свои лимиты (пользователь шлёт 5 × N сообщений).

> **Примечание:** `ThrottlingMiddleware` уже содержит защиту от роста памяти:
> `max_tracked_users=10_000`, периодическая `_full_cleanup()` каждые 300 с,
> форсированная очистка при переполнении. Проблема ограничена **только**
> сбросом при рестарте и per-process счётчиками.

**Влияние:** Низкое для варианта A (1 инстанс). Критичное для варианта B (N инстансов).

---

### 2.7. [P2] Нет снимка корзины

**Где:** отсутствует как функционал

**Проблема:**  
- Корзина — это одноразовая ссылка, генерируемая через `vkusvill_cart_link_create`.
- Бот не хранит "текущую корзину" пользователя.
- Для замены товара GigaChat должен вспомнить весь контекст из истории.
- Если история потеряна (рестарт, вытеснение, обрезка >50 сообщений) — замена невозможна.
- Блокирует команды `/cart` и `/reorder` из ROADMAP.

**Влияние:** Среднее. Пользователь не может вернуться к корзине после потери контекста.

---

### 2.8. [P2] Per-user locks не очищаются

**Где:** `services/dialog_manager.py`

```python
# Строка 39 — locks создаются, но не удаляются
self._locks: dict[int, asyncio.Lock] = {}
```

**Проблема:**  
- Каждый новый пользователь создаёт `asyncio.Lock`, который никогда не удаляется.
- При 10 000 уникальных пользователей — 10 000 lock-объектов в памяти навсегда.
- `reset()` удаляет lock, но вызывается только по `/reset` (редко).

**Влияние:** Низкое. Lock-объект ~100 байт, 10K locks ≈ 1 МБ. Но паттерн неаккуратный.

---

## 3. Целевая архитектура (Вариант A — рекомендуемый)

### 3.1. Обзор

```
                           Yandex Cloud
┌──────────────────────────────────────────────────────┐
│                                                      │
│  Telegram                                            │
│  Webhook ────→ ┌───────────────────────────────┐     │
│                │  Compute VM / Container       │     │
│                │                               │     │
│                │  vkuswill-bot                  │     │
│                │  ├── aiohttp (webhook server)  │     │
│                │  ├── aiogram (dispatcher)      │     │
│                │  ├── GigaChat SDK (thread pool)│     │
│                │  └── redis.asyncio (клиент)    │     │
│                └────────┬───────────┬──────────┘     │
│                         │           │                │
│              ┌──────────▼──┐   ┌────▼────────────┐   │
│              │   Managed    │   │   Managed       │   │
│              │   Redis      │   │   PostgreSQL    │   │
│              │              │   │                 │   │
│              │  • dialogs   │   │  • preferences  │   │
│              │    TTL 24h   │   │  • (будущее:    │   │
│              │  • prices    │   │    orders,      │   │
│              │    TTL 1h    │   │    analytics)   │   │
│              │  • cart snap │   │                 │   │
│              │    TTL 24h   │   │                 │   │
│              │  • rate lim  │   │                 │   │
│              │    TTL 60s   │   │                 │   │
│              └─────────────┘   └─────────────────┘   │
│                                                      │
│  GigaChat API ←─── asyncio.Semaphore (15 parallel)   │
│  MCP Server   ←─── httpx.AsyncClient (keep-alive)    │
└──────────────────────────────────────────────────────┘
```

### 3.2. Принципы перехода

1. **Тот же публичный API** — `DialogManager`, `PriceCache`, `PreferencesStore` сохраняют интерфейс. Меняется только реализация (бэкенд).
2. **Feature flag** — переключение между in-memory и Redis через конфиг (`STORAGE_BACKEND=redis|memory`).
3. **Graceful fallback** — при недоступности Redis бот продолжает работать на in-memory (с потерей персистентности, но без краша).
4. **Тесты не ломаются** — тесты используют моки/in-memory, не зависят от Redis.

---

## 4. Решения по каждой проблеме

### 4.1. [P0] Персистентные диалоги → RedisDialogManager

**Решает:** проблемы 2.1, 2.8

#### Интерфейс (сохраняется текущий)

```python
class RedisDialogManager:
    """Персистентный менеджер диалогов на Redis."""

    def __init__(
        self,
        redis: Redis,
        max_history: int = 50,
        dialog_ttl: int = 86400,  # 24 часа
    ) -> None: ...

    def get_lock(self, user_id: int) -> asyncio.Lock:
        """Per-user lock (остаётся in-memory — lock нужен
        только внутри одного процесса)."""
        ...

    async def get_history(self, user_id: int) -> list[Messages]:
        """Загрузить историю из Redis или создать новую."""
        ...

    async def save_history(self, user_id: int, history: list[Messages]) -> None:
        """Сохранить историю в Redis с TTL."""
        ...

    def trim(self, history: list[Messages]) -> list[Messages]:
        """Обрезать историю в памяти (перед save).

        Принимает и возвращает list — не зависит от внутреннего хранилища.
        Это позволяет одному интерфейсу работать и для in-memory,
        и для Redis-бэкенда.
        """
        ...

    async def reset(self, user_id: int) -> None:
        """Удалить из Redis + очистить lock."""
        ...
```

#### Redis-структура

```
dialog:{user_id}  →  JSON-строка (сериализованный list[Messages])
                      TTL: 86400 секунд (24 часа)
```

#### Сериализация Messages

```python
def _serialize(self, history: list[Messages]) -> str:
    """Сериализовать историю в JSON для Redis."""
    items = []
    for msg in history:
        item = {"role": msg.role.value, "content": msg.content}
        # Проверяем через `is not None` — атрибуты всегда определены
        # в Pydantic-модели Messages, hasattr всегда вернёт True
        if msg.name is not None:
            item["name"] = msg.name
        if msg.function_call is not None:
            item["function_call"] = {
                "name": msg.function_call.name,
                "arguments": msg.function_call.arguments,
            }
        if getattr(msg, "functions_state_id", None) is not None:
            item["functions_state_id"] = msg.functions_state_id
        items.append(item)
    return json.dumps(items, ensure_ascii=False)

def _deserialize(self, raw: str) -> list[Messages]:
    """Десериализовать JSON из Redis в list[Messages]."""
    items = json.loads(raw)
    messages = []
    for item in items:
        msg = Messages(
            role=MessagesRole(item["role"]),
            content=item.get("content", ""),
        )
        if "name" in item:
            msg.name = item["name"]
        if "function_call" in item:
            fc = item["function_call"]
            msg.function_call = FunctionCall(
                name=fc["name"], arguments=fc.get("arguments"),
            )
        if "functions_state_id" in item:
            msg.functions_state_id = item["functions_state_id"]
        messages.append(msg)
    return messages
```

#### Изменения в GigaChatService

```python
# Было (in-memory, синхронно):
async def _process_message_locked(self, user_id, text):
    history = self._get_history(user_id)  # синхронный вызов
    history.append(...)
    ...
    self._trim_history(user_id)
    return msg.content

# Стало (Redis, асинхронно):
async def _process_message_locked(self, user_id, text):
    history = await self._dialog_manager.get_history(user_id)  # async
    history.append(...)
    ...
    history = self._dialog_manager.trim(history)  # обрезка в памяти
    await self._dialog_manager.save_history(user_id, history)  # persist
    return msg.content
```

#### TTL-стратегия

| Событие | TTL обновляется? |
|---------|-----------------|
| Пользователь отправил сообщение | Да, продлевается на 24ч |
| Пользователь молчит 24ч | Диалог автоматически истекает |
| Пользователь вызвал /reset | Диалог удаляется немедленно |
| Бот перезапустился | Диалоги сохранены в Redis |

#### Оценка памяти Redis

- Средний диалог: 50 сообщений × ~500 байт = ~25 КБ в JSON
- 1000 активных диалогов: ~25 МБ
- 5000 активных диалогов: ~125 МБ
- Вывод: Redis на 256 МБ — достаточно с запасом

---

### 4.2. [P0] Семафор для GigaChat API

**Решает:** проблему 2.2

#### Реализация

```python
# В config.py
gigachat_max_concurrent: int = 15  # макс. параллельных запросов к GigaChat

# В GigaChatService.__init__
self._api_semaphore = asyncio.Semaphore(gigachat_max_concurrent)

# В _process_message_locked — обернуть каждый вызов GigaChat
async with self._api_semaphore:
    response = await asyncio.to_thread(
        self._client.chat,
        Chat(messages=history, functions=functions, function_call="auto"),
    )
```

#### Почему 15?

- Thread pool = 50 воркеров (физический потолок).
- GigaChat API — rate limit зависит от scope (PERS/CORP). Для PERS — ~15-20 RPS.
- Один запрос пользователя = 1-5 вызовов GigaChat (function calling loop).
- 15 параллельных × 3 средних итерации = ~45 запросов "в полёте" → укладывается в пул.
- Пользователи сверх лимита ожидают в очереди семафора (не получают ошибку).

#### Добавить retry при 429

```python
# В _process_message_locked, внутри цикла function calling
from gigachat.exceptions import GigaChatException  # проверить иерархию SDK

for attempt in range(3):
    try:
        async with self._api_semaphore:
            response = await asyncio.to_thread(
                self._client.chat, Chat(...)
            )
        break
    except GigaChatException as e:
        # TODO: исследовать иерархию исключений GigaChat SDK.
        # Проверка `"429" in str(e)` — хрупкая. Если SDK предоставляет
        # status_code или подкласс для rate limit — использовать его.
        if attempt < 2 and _is_rate_limit_error(e):
            delay = 2 ** attempt  # 1s, 2s
            logger.warning("GigaChat 429, retry %d через %ds", attempt + 1, delay)
            await asyncio.sleep(delay)
            continue
        raise

def _is_rate_limit_error(e: Exception) -> bool:
    """Определить, является ли ошибка rate limit (429)."""
    # Первичная реализация — заменить на проверку типа/кода при изучении SDK
    return "429" in str(e) or "rate" in str(e).lower()
```

---

### 4.3. [P0] Webhook вместо Polling

**Решает:** проблему 2.3

#### Изменения в __main__.py

```python
from aiohttp import web
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)

WEBHOOK_PATH = "/webhook"

async def main() -> None:
    # ... инициализация сервисов (без изменений) ...

    # Webhook-сервер
    app = web.Application()
    webhook_url = f"https://{config.webhook_host}{WEBHOOK_PATH}"

    handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # Установить webhook в Telegram
    await bot.set_webhook(
        url=webhook_url,
        drop_pending_updates=True,  # не обрабатывать старые при рестарте
    )

    # Запуск HTTP-сервера
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.webhook_port)
    await site.start()

    # Ожидание сигнала завершения
    shutdown_event = asyncio.Event()
    # ... signal handlers ...
    await shutdown_event.wait()

    # Cleanup
    await bot.delete_webhook()
    await runner.cleanup()
    # ... close resources ...
```

#### Новые параметры конфигурации

```python
# В config.py
webhook_host: str = ""           # Домен для webhook (напр. bot.example.com)
webhook_port: int = 8080         # Порт HTTP-сервера
use_webhook: bool = False        # False = polling (для разработки)
```

#### Совместимость: polling для dev, webhook для prod

```python
if config.use_webhook:
    await _run_webhook(bot, dp, ...)
else:
    await dp.start_polling(bot)
```

---

### 4.4. [P1] PriceCache → RedisPriceCache

**Решает:** проблему 2.4

#### Интерфейс (сохраняется)

```python
class RedisPriceCache:
    """Кэш цен в Redis с TTL."""

    def __init__(self, redis: Redis, ttl: int = 3600) -> None:
        self._redis = redis
        self._ttl = ttl  # 1 час — цены обновляются часто

    async def set(self, xml_id: int, name: str, price: float, unit: str = "шт"):
        await self._redis.hset(f"price:{xml_id}", mapping={
            "name": name, "price": str(price), "unit": unit,
        })
        await self._redis.expire(f"price:{xml_id}", self._ttl)

    async def get(self, xml_id: int) -> PriceInfo | None:
        data = await self._redis.hgetall(f"price:{xml_id}")
        if not data:
            return None
        return PriceInfo(
            name=data[b"name"].decode(),
            price=float(data[b"price"]),
            unit=data[b"unit"].decode(),
        )
```

#### Внимание: sync → async

Текущий `PriceCache` — синхронный (dict). `RedisPriceCache` — асинхронный.
Это потребует изменений в `SearchProcessor` и `CartProcessor`:

```python
# Было (sync):
cached = self._price_cache.get(xml_id)

# Стало (async):
cached = await self._price_cache.get(xml_id)
```

**Объём изменений:** ~15 мест в `search_processor.py` и `cart_processor.py`.

#### Альтернатива: двухуровневый кэш

Чтобы минимизировать изменения, можно использовать in-memory кэш как L1, Redis как L2:

```python
class TwoLevelPriceCache:
    """L1 (in-memory) + L2 (Redis) с async get."""

    def __init__(self, redis: Redis, ttl: int = 3600):
        self._l1: dict[int, PriceInfo] = {}
        self._redis = redis
        self._ttl = ttl

    async def get(self, xml_id: int) -> PriceInfo | None:
        """L1 → L2 fallthrough с автоматическим promote."""
        if xml_id in self._l1:
            return self._l1[xml_id]
        # L1 miss — пробуем L2 (Redis)
        data = await self._redis.hgetall(f"price:{xml_id}")
        if data:
            info = PriceInfo(
                name=data[b"name"].decode(),
                price=float(data[b"price"]),
                unit=data[b"unit"].decode(),
            )
            self._l1[xml_id] = info  # promote to L1
            return info
        return None

    async def set(self, xml_id: int, name: str, price: float, unit: str = "шт"):
        """Запись в оба уровня."""
        info = PriceInfo(name, price, unit)
        self._l1[xml_id] = info
        await self._redis.hset(f"price:{xml_id}", mapping={
            "name": name, "price": str(price), "unit": unit,
        })
        await self._redis.expire(f"price:{xml_id}", self._ttl)
```

> **Внимание:** `get()` теперь async — потребует `await` в ~15 местах
> в `search_processor.py` и `cart_processor.py`. Это неизбежно для
> корректной работы после рестарта (иначе L1 пуст, а warm_up — скрытый
> контракт, который легко забыть).

**Рекомендация:** двухуровневый кэш с async get — предсказуемое поведение после рестарта.

---

### 4.5. [P1] SQLite → PostgreSQL для preferences

**Решает:** проблему 2.5

#### Зачем не оставить SQLite в Redis?

- Предпочтения — долгосрочные данные (пользователь задал "люблю пломбир" — это навсегда).
- Redis с TTL — плохо для вечных данных (нужно отдельно управлять персистентностью).
- PostgreSQL — ACID, миграции, расширяемость (orders, analytics в будущем).

#### Изменения

```python
class PostgresPreferencesStore:
    """Хранилище предпочтений на PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_all(self, user_id: int) -> list[dict]:
        rows = await self._pool.fetch(
            "SELECT category, preference FROM preferences "
            "WHERE user_id = $1 ORDER BY category",
            user_id,
        )
        return [{"category": r["category"], "preference": r["preference"]} for r in rows]

    async def set(self, user_id: int, category: str, preference: str) -> str:
        await self._pool.execute(
            "INSERT INTO preferences (user_id, category, preference) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (user_id, category) DO UPDATE SET preference = $3",
            user_id, category, preference,
        )
        ...
```

#### Миграция данных

Если в SQLite уже есть данные пользователей:

```sql
-- Схема PostgreSQL (идентична SQLite)
CREATE TABLE IF NOT EXISTS preferences (
    user_id    BIGINT  NOT NULL,
    category   TEXT    NOT NULL,
    preference TEXT    NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, category)
);

CREATE INDEX idx_preferences_user ON preferences(user_id);
```

#### Инструмент миграций

Для управления схемой PostgreSQL в production — использовать **yoyo-migrations**
(легковесный, не требует ORM, SQL-first):

```toml
# В pyproject.toml [project.dependencies]
"yoyo-migrations >= 8.2",
```

```
migrations/
├── 0001.preferences-create.sql
├── 0002.future-orders-table.sql
└── ...
```

Альтернатива: `alembic` (тяжелее, зависит от SQLAlchemy).

---

### 4.6. [P1] Снимок корзины

**Решает:** проблему 2.7

#### Реализация

После успешного создания корзины — сохраняем снимок в Redis:

```python
# В ToolExecutor.postprocess_result, после vkusvill_cart_link_create:
if tool_name == "vkusvill_cart_link_create" and self._cart_snapshot_store:
    await self._cart_snapshot_store.save(user_id, {
        "products": args.get("products", []),
        "link": extract_link(result),
        "total": extract_total(result),
        "created_at": datetime.utcnow().isoformat(),
    })
```

#### Redis-структура

```
cart:{user_id}  →  JSON (products, link, total, timestamp)
                   TTL: 86400 секунд (24 часа)
```

#### Команда /cart (будущее)

```python
@router.message(Command("cart"))
async def cmd_cart(message: Message, cart_store: CartSnapshotStore):
    snapshot = await cart_store.get(message.from_user.id)
    if not snapshot:
        await message.answer("У вас нет активной корзины.")
        return
    # Показать список товаров + ссылку
    await message.answer(format_cart_snapshot(snapshot))
```

---

### 4.7. Graceful fallback: Redis → in-memory

**Решает:** устойчивость к сбоям Redis

При недоступности Redis бот не должен крашиться — только терять персистентность.

#### Паттерн: Factory + try/except обёртка

```python
# В __main__.py — выбор бэкенда
async def create_dialog_manager(config: Config) -> DialogManager:
    if config.storage_backend == "redis":
        try:
            redis = await create_redis_client(config.redis_url)
            await redis.ping()  # health check
            logger.info("Redis доступен, используем RedisDialogManager")
            return RedisDialogManager(redis, max_history=config.max_history_messages)
        except Exception as e:
            logger.warning("Redis недоступен (%s), fallback на in-memory", e)
    return DialogManager(max_history=config.max_history_messages)
```

> **Решение:** fallback на уровне инициализации (factory).
> Автоматическое переключение при сбоях Redis во время работы —
> отложено (требует circuit breaker, усложняет тестирование).
> При сбое Redis в рантайме — ошибка логируется, пользователь
> получает сообщение об ошибке, но бот не падает.

---

### 4.8. RecipeStore — остаётся на SQLite

**Решение:** `RecipeStore` **не** мигрируется в PostgreSQL.

**Обоснование:**
- Это **read-only кэш** ингредиентов, генерируемых через GigaChat
- Данные недолгоживущие и легко воспроизводимые
- Потеря при рестарте не критична — GigaChat сгенерирует заново
- SQLite для read-heavy нагрузки работает отлично
- При горизонтальном масштабировании — каждый инстанс будет иметь свой кэш (это допустимо)

> Если в будущем рецепты станут пользовательскими данными (не кэш),
> пересмотреть решение.

---

### 4.9. Health check endpoint

Для webhook-режима необходим `/health` для мониторинга и load balancer.

```python
async def health_handler(request: web.Request) -> web.Response:
    """Проверка работоспособности бота."""
    checks = {"status": "ok", "redis": False, "mcp": False}
    try:
        await redis.ping()
        checks["redis"] = True
    except Exception:
        checks["status"] = "degraded"
    try:
        await mcp_client.get_tools()
        checks["mcp"] = True
    except Exception:
        checks["status"] = "degraded"
    status_code = 200 if checks["status"] == "ok" else 503
    return web.json_response(checks, status=status_code)

# В setup webhook:
app.router.add_get("/health", health_handler)
```

---

### 4.10. Очистка per-user locks

**Решает:** проблему 2.8 (locks не очищаются)

Per-user `asyncio.Lock` остаётся in-memory (lock нужен только внутри процесса).
Но словарь locks растёт без ограничений.

#### Решение: LRU-dict для locks

```python
from collections import OrderedDict

class RedisDialogManager:
    MAX_LOCKS = 2000  # максимум одновременно хранимых locks

    def get_lock(self, user_id: int) -> asyncio.Lock:
        if user_id in self._locks:
            self._locks.move_to_end(user_id)
            return self._locks[user_id]
        if len(self._locks) >= self.MAX_LOCKS:
            self._locks.popitem(last=False)  # удаляем самый старый
        lock = asyncio.Lock()
        self._locks[user_id] = lock
        return lock
```

> Lock-объект ~100 байт, 2000 locks ≈ 200 КБ — пренебрежимо.
> Но LRU-dict предотвращает неограниченный рост.

---

## 5. Конфигурация Yandex Cloud

### 5.1. Ресурсы

| Сервис | Tier | Конфигурация | Стоимость |
|--------|------|-------------|-----------|
| **Compute VM** (или Serverless Container) | burstable | 2 vCPU, 4 GB RAM, 20 GB SSD | ~2 000 руб/мес |
| **Managed Redis** | burstable (b3-c1-m4) | 1 нода, 4 GB RAM | ~1 500 руб/мес |
| **Managed PostgreSQL** | burstable (b3-c1-m4) | 1 нода, 10 GB SSD | ~2 000 руб/мес |
| **Container Registry** | — | Хранение Docker-образов | ~100 руб/мес |
| **Итого** | | | **~5 600 руб/мес** |

### 5.2. Конфигурация Redis

```yaml
# Yandex Managed Redis
version: "7.2"
resources:
  resource_preset_id: b3-c1-m4  # burstable, 4GB RAM
  disk_size: 16GB
  disk_type_id: network-ssd
config:
  maxmemory_policy: volatile-lru  # удалять только ключи с TTL
  timeout: 300                     # закрывать idle-соединения через 5 мин
```

### 5.3. Конфигурация PostgreSQL

```yaml
# Yandex Managed PostgreSQL
version: "16"
resources:
  resource_preset_id: b3-c1-m4
  disk_size: 10GB
  disk_type_id: network-ssd
config:
  max_connections: 50
  shared_buffers: 1GB
```

### 5.4. Переменные окружения (дополнение к .env)

```env
# Redis
REDIS_URL=redis://:password@rc1a-xxxxx.mdb.yandexcloud.net:6379/0

# PostgreSQL
DATABASE_URL=postgresql://user:password@rc1a-xxxxx.mdb.yandexcloud.net:6432/vkuswill_bot

# Webhook
USE_WEBHOOK=true
WEBHOOK_HOST=bot.example.com
WEBHOOK_PORT=8080

# Лимиты
GIGACHAT_MAX_CONCURRENT=15

# Бэкенд хранилища
STORAGE_BACKEND=redis  # redis | memory
```

---

## 6. Новые зависимости

```toml
# В pyproject.toml [project.dependencies]
dependencies = [
    # ... существующие ...
    "redis[hiredis] >= 5.0",        # Redis клиент + C-парсер
    "asyncpg >= 0.29",              # PostgreSQL async-драйвер
    "yoyo-migrations >= 8.2",       # Управление SQL-миграциями
    # aiohttp НЕ добавляем явно — уже транзитивная зависимость через aiogram 3.x
]
```

---

## 7. План реализации

### Фаза 1: Подготовка (день 1)

| # | Задача | Часы | Файлы |
|---|--------|------|-------|
| 1.1 | Добавить зависимости (`redis`, `asyncpg`, `aiohttp`) | 0.5 | `pyproject.toml` |
| 1.2 | Расширить `config.py` новыми параметрами | 1 | `config.py` |
| 1.3 | Создать `services/redis_client.py` — инициализация и health check | 1 | новый файл |
| 1.4 | Добавить `asyncio.Semaphore` для GigaChat | 1 | `gigachat_service.py`, `config.py` |
| 1.5 | Добавить retry при 429 от GigaChat | 1 | `gigachat_service.py` |

**Контрольная точка:** `uv run pytest -v` — все тесты проходят.

### Фаза 2: Персистентные диалоги (день 1-2)

| # | Задача | Часы | Файлы |
|---|--------|------|-------|
| 2.1 | Создать `RedisDialogManager` с сериализацией Messages | 3 | новый или расширение `dialog_manager.py` |
| 2.2 | Тесты: round-trip сериализации, TTL, reset | 2 | `test_dialog_manager.py` |
| 2.3 | Интегрировать в `GigaChatService` (sync → async для get/save) | 2 | `gigachat_service.py` |
| 2.4 | Feature flag: `STORAGE_BACKEND=redis\|memory` | 1 | `__main__.py`, `config.py` |

**Контрольная точка:** диалог сохраняется после рестарта бота (ручной тест).

### Фаза 3: Кэш цен и снимок корзины (день 2)

| # | Задача | Часы | Файлы |
|---|--------|------|-------|
| 3.1 | `TwoLevelPriceCache` (L1 in-memory + L2 Redis) | 2 | `price_cache.py` |
| 3.2 | `CartSnapshotStore` — сохранение последней корзины | 2 | новый файл |
| 3.3 | Интеграция snapshot в `ToolExecutor.postprocess_result` | 1 | `tool_executor.py` |
| 3.4 | Тесты | 2 | `test_price_cache.py`, новый файл |

**Контрольная точка:** цены не теряются при рестарте, корзина сохраняется в Redis.

### Фаза 4: Webhook + PostgreSQL (день 2-3)

| # | Задача | Часы | Файлы |
|---|--------|------|-------|
| 4.1 | Webhook-режим в `__main__.py` | 2 | `__main__.py` |
| 4.2 | Health check endpoint `/health` | 1 | `__main__.py` |
| 4.3 | `PostgresPreferencesStore` | 2 | новый или расширение `preferences_store.py` |
| 4.4 | SQL-миграция preferences (yoyo-migrations) | 1 | `migrations/0001.preferences-create.sql` |
| 4.5 | Feature flag для SQLite / PostgreSQL | 1 | `__main__.py`, `config.py` |
| 4.6 | Warm-up скрипт при старте (MCP tools, GigaChat SDK init) | 1 | `__main__.py` |
| 4.7 | Тесты | 2 | `test_preferences_store.py` |

**Контрольная точка:** `uv run pytest -v` — все тесты проходят, webhook работает локально, `/health` отвечает 200.

### Фаза 5: Деплой в Yandex Cloud (день 3)

| # | Задача | Часы | Файлы |
|---|--------|------|-------|
| 5.1 | `Dockerfile` (multi-stage build) | 1 | новый файл |
| 5.2 | `docker-compose.yml` (для локальной разработки с Redis + PG) | 1 | новый файл |
| 5.3 | Создать ресурсы в Yandex Cloud (Redis, PG, VM) | 2 | Terraform / ручная настройка |
| 5.4 | Настроить CI/CD для деплоя | 2 | `.github/workflows/deploy.yml` |
| 5.5 | Smoke test в production | 1 | ручной тест |

**Контрольная точка:** бот работает в Yandex Cloud, диалоги персистентны.

---

## 8. Тестирование

### 8.1. Unit-тесты (автоматические)

- Сериализация/десериализация `Messages` → JSON → `Messages` (round-trip)
- `RedisDialogManager`: get, save, trim, reset, TTL
- `RedisPriceCache`: set, get, expiry
- `CartSnapshotStore`: save, get, TTL
- `PostgresPreferencesStore`: CRUD, limits, concurrent writes
- Семафор: не более N параллельных вызовов

### 8.2. Интеграционные тесты (локально с docker-compose)

- Бот → Redis → перезапуск → диалог сохранён
- Бот → PostgreSQL → preferences CRUD
- Webhook endpoint принимает Telegram update

### 8.3. Нагрузочное тестирование (перед запуском)

```bash
# locust для имитации наплыва с Хабра
locust -f tests/load/locustfile.py \
    --users 50 \
    --spawn-rate 10 \
    --run-time 5m \
    --host https://bot.example.com
```

**Метрики успеха:**
- p95 время ответа < 15 секунд (включая GigaChat)
- 0 ошибок 500 при 50 параллельных пользователях
- Redis memory < 200 МБ
- PostgreSQL connections < 30

---

## 9. Риски и митигации

| # | Риск | Вероятность | Влияние | Митигация |
|---|------|------------|---------|-----------|
| R1 | GigaChat API 429 при наплыве | Средняя | Высокое | Семафор (15) + retry + backoff |
| R2 | Redis недоступен (сбой Yandex Cloud) | Низкая | Высокое | Fallback на in-memory; Managed Redis с авто-failover |
| R3 | Сериализация Messages теряет поля | Средняя | Высокое | Round-trip тесты на всех типах Messages |
| R4 | MCP-сервер ВкусВилл перегружен | Средняя | Среднее | Retry уже есть (3 попытки + backoff) |
| R5 | Рост стоимости Yandex Cloud | Низкая | Низкое | burstable-инстансы, мониторинг |
| R6 | Миграция SQLite → PostgreSQL | Низкая | Низкое | Скрипт миграции, дамп + восстановление |
| R7 | Cold start после деплоя: пустые кэши, первая auth GigaChat, загрузка MCP tools | Высокая | Среднее | Warm-up скрипт при старте: `await mcp_client.get_tools()`, первый запрос к GigaChat для инициализации SDK, заполнение L1 PriceCache из Redis |
| R8 | `aiohttp` version conflict с aiogram 3.x (aiogram зависит от aiohttp) | Низкая | Низкое | Не добавлять `aiohttp` явно — использовать транзитивную зависимость через aiogram. Проверить: `uv pip show aiohttp` |

---

## 10. Решение не принято (отложено)

| Решение | Причина откладывания |
|---------|---------------------|
| Kubernetes + горизонтальное масштабирование | Overkill для текущей нагрузки. Переход потребует distributed rate limiting, helm charts, HPA. Код, подготовленный в варианте A (Redis shared state), позволит мигрировать на K8s без изменений бизнес-логики. |
| Очередь сообщений (RabbitMQ / YMQ) | Не нужна при <1000 пользователей. Aiogram + asyncio справляются с конкурентностью. |
| Streaming ответов GigaChat | SDK не поддерживает streaming в текущей версии. Пересмотреть при обновлении. |
| PostgreSQL для диалогов | Redis с TTL — оптимальнее для короткоживущих данных (диалоги = 24ч). PostgreSQL для диалогов — overkill и медленнее. |

---

## 11. Чеклист качества решения

- [x] Решение обосновано trade-off анализом (2 варианта: Redis+VM vs Kubernetes)
- [x] Указаны все затрагиваемые модули (dialog_manager, price_cache, preferences_store, gigachat_service, __main__, config)
- [x] Определены новые зависимости (redis, asyncpg, yoyo-migrations)
- [x] Проверена совместимость с ROADMAP.md (разблокирует /cart, /reorder, списки)
- [x] Оценена сложность и сроки (3 дня, 5 фаз)
- [x] Описаны риски и митигации (8 рисков)
- [x] Решение тестируемо (unit + integration + load)
- [x] Обратная совместимость сохранена (feature flags, тот же API)
- [x] Не нарушает принцип слоёв (handlers → services → storage)
- [x] Нет циклических зависимостей
- [x] Описан graceful fallback при недоступности Redis
- [x] Описана судьба RecipeStore (остаётся SQLite)
- [x] Добавлен health check endpoint
- [x] Решена проблема утечки per-user locks (LRU-dict)

---

## Приложение A: Диаграмма потоков данных (после изменений)

```
[Пользователь]
      │ текст
      ▼
[Telegram API]
      │ webhook POST
      ▼
[aiohttp server :8080]
      │
      ▼
[ThrottlingMiddleware]
      │ rate limit check
      │ (in-memory, затем Redis при горизонтальном масштабировании)
      ▼
[handlers.py → handle_text()]
      │
      ▼
[GigaChatService.process_message()]
      │
      ├──→ acquire per-user Lock (in-memory asyncio.Lock)
      │
      ├──→ RedisDialogManager.get_history()
      │        └──→ Redis GET dialog:{user_id}
      │              └──→ deserialize JSON → list[Messages]
      │
      ├──→ acquire Semaphore (max 15)
      │        └──→ GigaChat API (asyncio.to_thread)
      │              └──→ retry on 429 (backoff 1s, 2s)
      │
      ├──→ function_call? ──→ ToolExecutor.execute()
      │        ├──→ MCP: vkusvill_products_search
      │        │        └──→ TwoLevelPriceCache.set() → L1 + Redis
      │        │
      │        ├──→ MCP: vkusvill_cart_link_create
      │        │        ├──→ CartProcessor.calc_total()
      │        │        └──→ CartSnapshotStore.save() → Redis
      │        │
      │        ├──→ Local: user_preferences_get
      │        │        └──→ PostgreSQL SELECT
      │        │
      │        └──→ Local: recipe_ingredients
      │                 └──→ RecipeStore (SQLite, read-only кэш)
      │
      ├──→ RedisDialogManager.save_history()
      │        └──→ Redis SET dialog:{user_id} EX 86400
      │
      └──→ release Lock, release Semaphore
              │
              ▼
[handlers.py → message.answer()]
      │
      ▼
[Telegram API → пользователю]
```
