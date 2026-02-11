# Ревью проекта vkuswill-bot

> Дата: 2026-02-06 | Покрытие тестами: 94% | Тестов: 289 passed, 3 xfailed
>
> Общая оценка: **💬 Comment** — блокеров нет, есть предложения по улучшению

---

## Содержание

1. [Предложения по улучшению](#предложения-по-улучшению)
2. [Мелкие замечания](#мелкие-замечания)
3. [Что сделано хорошо](#что-сделано-хорошо)
4. [Приоритеты](#приоритеты)

---

## Предложения по улучшению

### 1. Rate-limiting для пользователей

**Файл:** `src/vkuswill_bot/bot/handlers.py`

**Проблема:** Пользователь может отправлять неограниченное число сообщений подряд. Каждое сообщение порождает вызов GigaChat API, что может привести к чрезмерному расходу средств на API и перегрузке бота.

**Решение:** Добавить throttling-middleware в aiogram. Создать файл `src/vkuswill_bot/bot/middlewares.py`:

```python
"""Middleware для ограничения частоты запросов."""

import time
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Лимит: не более N сообщений за WINDOW секунд
MAX_MESSAGES = 5
WINDOW_SECONDS = 60


class ThrottlingMiddleware(BaseMiddleware):
    """Ограничение частоты сообщений от одного пользователя."""

    def __init__(self) -> None:
        self._timestamps: dict[int, list[float]] = defaultdict(list)

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id
        now = time.monotonic()

        # Удаляем старые записи за пределами окна
        self._timestamps[user_id] = [
            ts for ts in self._timestamps[user_id]
            if now - ts < WINDOW_SECONDS
        ]

        if len(self._timestamps[user_id]) >= MAX_MESSAGES:
            logger.warning(
                "Throttle: пользователь %d превысил лимит %d/%ds",
                user_id, MAX_MESSAGES, WINDOW_SECONDS,
            )
            await event.answer(
                "Слишком много сообщений. "
                f"Подождите немного (лимит: {MAX_MESSAGES} сообщений в минуту)."
            )
            return None

        self._timestamps[user_id].append(now)
        return await handler(event, data)
```

Подключение в `__main__.py`:

```python
from vkuswill_bot.bot.middlewares import ThrottlingMiddleware

dp = Dispatcher()
dp.message.middleware(ThrottlingMiddleware())
dp.include_router(router)
```

---

### 2. LRU-вытеснение старых диалогов из памяти

**Файл:** `src/vkuswill_bot/services/gigachat_service.py`, строка 100

**Проблема:** Словарь `_conversations` растёт неограниченно при росте числа уникальных пользователей. При 10 000 пользователей с 50 сообщениями каждый это может потреблять значительный объём RAM. При перезапуске бота все диалоги теряются.

**Решение (минимальное):** Использовать `OrderedDict` с лимитом:

```python
from collections import OrderedDict

MAX_CONVERSATIONS = 1000  # максимум активных диалогов


class GigaChatService:
    def __init__(self, ...) -> None:
        # ...
        self._conversations: OrderedDict[int, list[Messages]] = OrderedDict()

    def _get_history(self, user_id: int) -> list[Messages]:
        if user_id in self._conversations:
            # Перемещаем в конец (самый свежий)
            self._conversations.move_to_end(user_id)
            return self._conversations[user_id]

        # Вытесняем самый старый диалог при превышении лимита
        if len(self._conversations) >= MAX_CONVERSATIONS:
            evicted_id, _ = self._conversations.popitem(last=False)
            logger.debug("Вытеснен диалог пользователя %d (LRU)", evicted_id)

        self._conversations[user_id] = [
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)
        ]
        return self._conversations[user_id]
```

**Решение (продвинутое):** Для персистентности — Redis или SQLite:

```python
# Пример с Redis (схема)
import redis.asyncio as redis

class ConversationStore:
    """Хранилище диалогов в Redis с TTL."""

    def __init__(self, redis_url: str, ttl: int = 3600) -> None:
        self._redis = redis.from_url(redis_url)
        self._ttl = ttl  # время жизни диалога в секундах

    async def get(self, user_id: int) -> list[dict] | None:
        data = await self._redis.get(f"conv:{user_id}")
        if data:
            await self._redis.expire(f"conv:{user_id}", self._ttl)
            return json.loads(data)
        return None

    async def set(self, user_id: int, messages: list[dict]) -> None:
        await self._redis.set(
            f"conv:{user_id}",
            json.dumps(messages, ensure_ascii=False),
            ex=self._ttl,
        )

    async def delete(self, user_id: int) -> None:
        await self._redis.delete(f"conv:{user_id}")
```

---

### 3. Graceful shutdown при SIGTERM/SIGINT

**Файл:** `src/vkuswill_bot/__main__.py`

**Проблема:** При получении SIGTERM в production (например, от systemd или Docker) процесс может не дойти до блока `finally`, и ресурсы не будут освобождены корректно.

**Решение:**

```python
import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from vkuswill_bot.bot.handlers import router
from vkuswill_bot.config import config
from vkuswill_bot.services.gigachat_service import GigaChatService
from vkuswill_bot.services.mcp_client import VkusvillMCPClient

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILE = "bot.log"

logging.basicConfig(
    level=logging.DEBUG if config.debug else logging.INFO,
    format=LOG_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Инициализация сервисов и запуск бота."""
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    mcp_client = VkusvillMCPClient(config.mcp_server_url)

    gigachat_service = GigaChatService(
        credentials=config.gigachat_credentials,
        model=config.gigachat_model,
        scope=config.gigachat_scope,
        mcp_client=mcp_client,
        max_tool_calls=config.max_tool_calls,
        max_history=config.max_history_messages,
    )

    try:
        tools = await mcp_client.get_tools()
        logger.info("MCP инструменты загружены: %s", [t["name"] for t in tools])
    except Exception as e:
        logger.warning("Не удалось загрузить MCP инструменты при старте: %s", e)

    dp["gigachat_service"] = gigachat_service

    # --- Graceful shutdown ---
    async def shutdown() -> None:
        logger.info("Завершение работы бота...")
        await dp.stop_polling()
        await gigachat_service.close()
        await bot.session.close()
        logger.info("Бот остановлен.")

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda: asyncio.create_task(shutdown()),
        )

    logger.info("Бот запускается...")
    try:
        await dp.start_polling(bot)
    finally:
        await gigachat_service.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
```

---

### 4. Ограничение длины входящего сообщения

**Файл:** `src/vkuswill_bot/services/gigachat_service.py`

**Проблема:** Сообщение пользователя отправляется в GigaChat без ограничения длины. Telegram ограничивает текстовые сообщения 4096 символами, но forwarded-сообщения или API-вызовы могут быть длиннее. Это увеличивает стоимость API-вызова и время обработки.

**Решение:**

```python
MAX_USER_MESSAGE_LENGTH = 4096  # Лимит Telegram


class GigaChatService:
    # ...

    async def process_message(self, user_id: int, text: str) -> str:
        """Обработать сообщение пользователя."""
        # Обрезаем сверхдлинные сообщения
        if len(text) > MAX_USER_MESSAGE_LENGTH:
            logger.warning(
                "Сообщение пользователя %d обрезано: %d → %d символов",
                user_id, len(text), MAX_USER_MESSAGE_LENGTH,
            )
            text = text[:MAX_USER_MESSAGE_LENGTH]

        history = self._get_history(user_id)
        history.append(Messages(role=MessagesRole.USER, content=text))
        # ... остальная логика
```

---

### 5. Исправить `kill -9` в скриптах на SIGTERM

**Файлы:** `run.sh`, `stop.sh`

**Проблема:** `kill -9` (SIGKILL) мгновенно убивает процесс, не давая Python выполнить блок `finally` для корректного закрытия HTTP-соединений и сброса буферов.

**Решение для `stop.sh`:**

```bash
#!/bin/bash
# Остановка бота (graceful → force)
cd "$(dirname "$0")"

if [ -f .bot.pid ]; then
    PID=$(cat .bot.pid)

    # Шаг 1: SIGTERM — даём боту завершиться корректно
    kill "$PID" 2>/dev/null
    echo "Отправлен SIGTERM (PID: $PID), ожидание завершения..."

    # Шаг 2: Ждём до 10 секунд
    for i in $(seq 1 10); do
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "Бот остановлен корректно (за ${i}с)"
            rm -f .bot.pid
            exit 0
        fi
        sleep 1
    done

    # Шаг 3: Если не завершился — SIGKILL
    echo "Бот не ответил на SIGTERM, отправляю SIGKILL..."
    kill -9 "$PID" 2>/dev/null
    rm -f .bot.pid
    echo "Бот принудительно остановлен (PID: $PID)"
else
    pkill -f "python.*vkuswill_bot" 2>/dev/null
    echo "Бот остановлен"
fi
```

**Решение для `run.sh`:**

```bash
#!/bin/bash
# Быстрый запуск бота
cd "$(dirname "$0")"

# Корректно остановить предыдущий экземпляр
if [ -f .bot.pid ]; then
    OLD_PID=$(cat .bot.pid)
    kill "$OLD_PID" 2>/dev/null
    sleep 3
    kill -0 "$OLD_PID" 2>/dev/null && kill -9 "$OLD_PID" 2>/dev/null
    rm -f .bot.pid
fi

PYTHONPATH=src .venv/bin/python -m vkuswill_bot &
echo $! > .bot.pid
echo "Бот запущен (PID: $(cat .bot.pid))"
```

---

### 6. SSL-верификация для GigaChat

**Файл:** `src/vkuswill_bot/services/gigachat_service.py`, строка 96

**Проблема:** `verify_ssl_certs=False` отключает проверку SSL-сертификатов, что делает соединение уязвимым для MITM-атак.

**Решение:** Попробовать включить SSL и при необходимости передать корневой сертификат:

```python
# Вариант 1: Включить верификацию (если GigaChat SDK поддерживает)
self._client = GigaChat(
    credentials=credentials,
    model=model,
    scope=scope,
    verify_ssl_certs=True,
)

# Вариант 2: Передать путь к CA-сертификату Минцифры
# Скачать: https://www.gosuslugi.ru/crt
self._client = GigaChat(
    credentials=credentials,
    model=model,
    scope=scope,
    verify_ssl_certs=True,
    ca_bundle_file="/path/to/russian_trusted_root_ca.crt",
)

# Вариант 3: Через переменную окружения (в config.py)
# GIGACHAT_CA_BUNDLE=/path/to/cert.crt
self._client = GigaChat(
    credentials=credentials,
    model=model,
    scope=scope,
    verify_ssl_certs=True,
    ca_bundle_file=ca_bundle,  # из config
)
```

Если SDK GigaChat не работает без `verify_ssl_certs=False`, оставьте как есть, но добавьте TODO-комментарий:

```python
# TODO: включить verify_ssl_certs=True после решения проблемы
# с корневым сертификатом Минцифры в GigaChat SDK
# Трекер: https://github.com/ai-forever/gigachat/issues/XXX
```

---

### 7. Использование `asyncio.to_thread` для синхронного GigaChat SDK

**Файл:** `src/vkuswill_bot/services/gigachat_service.py`, строки 174, 144

**Проблема:** SDK GigaChat — синхронный. Обёртка через `asyncio.to_thread` работает, но при высокой нагрузке пул потоков (по умолчанию `min(32, os.cpu_count() + 4)`) может быть исчерпан.

**Решение (минимальное):** Увеличить пул в `__main__.py`:

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def main() -> None:
    # Увеличиваем пул потоков для GigaChat-вызовов
    loop = asyncio.get_event_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=50))
    # ... остальная инициализация
```

**Решение (продвинутое):** Использовать `httpx` напрямую для асинхронных вызовов GigaChat API (без SDK), аналогично тому, как реализован MCP-клиент.

---

## Мелкие замечания

### 1. Логирование в проглоченных исключениях

**Файлы:** `handlers.py:115-116`, `gigachat_service.py:143-146`

**Было:**

```python
# gigachat_service.py
async def close(self) -> None:
    try:
        await asyncio.to_thread(self._client.close)
    except Exception:
        pass
```

**Стало:**

```python
async def close(self) -> None:
    try:
        await asyncio.to_thread(self._client.close)
    except Exception as e:
        logger.debug("Ошибка при закрытии GigaChat-клиента: %s", e)
```

---

### 2. `.gitignore` — расширить паттерн для лог-файлов

**Было:**

```
bot.log
```

**Стало:**

```
*.log
```

---

### 3. README — заполнить placeholder-ы

**Файл:** `README.md`, строки 113-118

Секции «Лицензия» и «Автор» содержат placeholder-текст. Заполнить реальными данными перед публикацией.

---

### 4. Вынести тестовые хелперы в conftest.py

**Файлы:** `test_gigachat_service.py`, `test_ai_safety.py`, `test_input_validation.py`

Функции `_make_text_response`, `_make_function_call_response` и `_make_message` продублированы в 3 файлах.

**Решение:** Перенести в `tests/conftest.py`:

```python
# tests/conftest.py

from unittest.mock import AsyncMock, MagicMock

from gigachat.models import (
    ChatCompletion, Choices, FunctionCall,
    Messages, MessagesRole, Usage,
)

_USAGE = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)


def make_text_response(text: str) -> ChatCompletion:
    """Создать ответ GigaChat с текстом (без function_call)."""
    return ChatCompletion(
        choices=[
            Choices(
                message=Messages(role=MessagesRole.ASSISTANT, content=text),
                index=0,
                finish_reason="stop",
            )
        ],
        created=1000000,
        model="GigaChat",
        usage=_USAGE,
        object="chat.completion",
    )


def make_function_call_response(
    name: str, arguments: dict | str
) -> ChatCompletion:
    """Создать ответ GigaChat с вызовом функции."""
    import json
    args = json.loads(arguments) if isinstance(arguments, str) else arguments
    return ChatCompletion(
        choices=[
            Choices(
                message=Messages(
                    role=MessagesRole.ASSISTANT,
                    content="",
                    function_call=FunctionCall(name=name, arguments=args),
                ),
                index=0,
                finish_reason="function_call",
            )
        ],
        created=1000000,
        model="GigaChat",
        usage=_USAGE,
        object="chat.completion",
    )


def make_message(text: str = "", user_id: int = 1) -> MagicMock:
    """Создать мок aiogram.types.Message."""
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.chat = MagicMock()
    msg.chat.id = 100
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.send_chat_action = AsyncMock()
    return msg
```

Затем в тестовых файлах:

```python
from tests.conftest import make_text_response, make_function_call_response, make_message
```

---

### 5. Синхронизация версии в MCP-клиенте

**Файл:** `src/vkuswill_bot/services/mcp_client.py`, строка 196

**Было:**

```python
"clientInfo": {"name": "vkuswill-bot", "version": "0.1.0"},
```

**Стало:**

```python
from importlib.metadata import version, PackageNotFoundError

try:
    _CLIENT_VERSION = version("vkuswill-bot")
except PackageNotFoundError:
    _CLIENT_VERSION = "0.0.0"

# В _ensure_initialized:
"clientInfo": {"name": "vkuswill-bot", "version": _CLIENT_VERSION},
```

---

## Что сделано хорошо

| # | Что | Почему это важно |
|---|-----|-----------------|
| 1 | **Чёткая архитектура** (handlers → services → APIs) | Легко расширять, тестировать, поддерживать |
| 2 | **94% покрытие тестами**, 289 тестов | Минимизирует регрессии при изменениях |
| 3 | **Тесты безопасности ИИ** (prompt injection, jailbreak, tool abuse) | Редкость даже в enterprise-проектах |
| 4 | **SAST-тесты** (AST-анализ, поиск секретов, опасных функций) | Автоматизированный аудит безопасности |
| 5 | **Function calling цикл** с лимитами и anti-loop | Надёжная работа с MCP-инструментами |
| 6 | **Конфигурация через pydantic-settings** | Валидация, типизация, безопасность секретов |
| 7 | **Retry с exponential backoff** в MCP-клиенте | Устойчивость к сетевым сбоям |
| 8 | **Docstrings и комментарии** | Код самодокументирован |
| 9 | **DI через aiogram Dispatcher** | Тестируемость без глобального состояния |
| 10 | **Разбивка длинных сообщений** (`_split_message`) | UX: корректная работа с лимитом Telegram |

---

## Приоритеты

| Приоритет | Задача | Сложность | Влияние |
|-----------|--------|-----------|---------|
| 🔴 Высокий | Rate-limiting для пользователей | Средняя | Защита от злоупотреблений и перерасхода API |
| 🔴 Высокий | LRU-вытеснение старых диалогов | Низкая | Предотвращение утечки памяти |
| 🟡 Средний | Graceful shutdown (SIGTERM) | Низкая | Корректное завершение в production |
| 🟡 Средний | Ограничение длины сообщения | Низкая | Защита от перерасхода API |
| 🟡 Средний | Исправить `kill -9` в скриптах | Низкая | Корректное завершение |
| 🟢 Низкий | SSL-верификация GigaChat | Зависит от SDK | Безопасность соединения |
| 🟢 Низкий | Вынести тестовые хелперы | Низкая | Чистота кода |
| 🟢 Низкий | Синхронизация версии MCP | Низкая | Консистентность |
