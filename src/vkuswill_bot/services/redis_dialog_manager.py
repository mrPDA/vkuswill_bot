"""Персистентный менеджер диалогов на Redis.

Реализует тот же async-интерфейс, что и DialogManager (aget_history,
save_history, trim_list, areset, get_lock), но хранит историю в Redis
с TTL — диалоги переживают рестарт бота.

Redis-структура:
    dialog:{user_id}  →  JSON-строка (сериализованный list[Messages])
                          TTL: dialog_ttl секунд (по умолчанию 24 часа)
"""

import asyncio
import json
import logging
from collections import OrderedDict

from gigachat.models import FunctionCall, Messages, MessagesRole
from redis.asyncio import Redis

from vkuswill_bot.services.dialog_manager import trim_message_list
from vkuswill_bot.services.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Макс. количество per-user locks в LRU-dict
MAX_LOCKS = 2000

# TTL диалога по умолчанию (24 часа)
DEFAULT_DIALOG_TTL = 86400

# Префикс ключа в Redis
_KEY_PREFIX = "dialog:"


class RedisDialogManager:
    """Персистентный менеджер диалогов на Redis.

    Предоставляет тот же async-интерфейс, что и DialogManager:
    - aget_history / save_history / trim_list / areset / get_lock

    Per-user asyncio.Lock остаётся in-memory (lock нужен
    только внутри одного процесса). LRU-dict ограничивает
    количество хранимых locks.
    """

    def __init__(
        self,
        redis: Redis,
        max_history: int = 50,
        dialog_ttl: int = DEFAULT_DIALOG_TTL,
    ) -> None:
        self._redis = redis
        self._max_history = max_history
        self._dialog_ttl = dialog_ttl
        self._locks: OrderedDict[int, asyncio.Lock] = OrderedDict()

    # ---- Per-user lock (in-memory, LRU) ----

    def get_lock(self, user_id: int) -> asyncio.Lock:
        """Per-user lock с LRU-вытеснением.

        Lock нужен только внутри одного процесса для защиты
        от параллельных мутаций одного диалога.
        """
        if user_id in self._locks:
            self._locks.move_to_end(user_id)
            return self._locks[user_id]
        if len(self._locks) >= MAX_LOCKS:
            self._locks.popitem(last=False)  # удаляем самый старый
        lock = asyncio.Lock()
        self._locks[user_id] = lock
        return lock

    # ---- Async API ----

    async def aget_history(self, user_id: int) -> list[Messages]:
        """Загрузить историю из Redis или создать новую.

        При каждом доступе TTL продлевается (sliding window).
        """
        key = f"{_KEY_PREFIX}{user_id}"
        raw = await self._redis.get(key)

        if raw is not None:
            try:
                history = _deserialize(raw)
                # Продлеваем TTL при доступе
                await self._redis.expire(key, self._dialog_ttl)
                logger.debug(
                    "Redis: загружена история user %d (%d сообщений)",
                    user_id,
                    len(history),
                )
                return history
            except Exception as e:
                logger.warning(
                    "Redis: ошибка десериализации для user %d, создаю новую историю: %s",
                    user_id,
                    e,
                )

        # Новый диалог
        return [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]

    async def save_history(
        self,
        user_id: int,
        history: list[Messages],
    ) -> None:
        """Сохранить историю в Redis с TTL.

        Вызывается после каждого цикла обработки сообщения.
        """
        key = f"{_KEY_PREFIX}{user_id}"
        raw = _serialize(history)
        await self._redis.set(key, raw, ex=self._dialog_ttl)
        logger.debug(
            "Redis: сохранена история user %d (%d сообщений, TTL %ds)",
            user_id,
            len(history),
            self._dialog_ttl,
        )

    def trim_list(self, history: list[Messages]) -> list[Messages]:
        """Обрезать историю с суммаризацией старых tool results.

        Делегирует в свободную функцию trim_message_list (DRY).
        """
        return trim_message_list(history, self._max_history)

    async def areset(self, user_id: int) -> None:
        """Удалить диалог из Redis + очистить lock."""
        key = f"{_KEY_PREFIX}{user_id}"
        await self._redis.delete(key)
        self._locks.pop(user_id, None)
        logger.info("Redis: диалог user %d удалён", user_id)


# ============================================================================
# Сериализация / десериализация Messages
# ============================================================================


def _serialize(history: list[Messages]) -> str:
    """Сериализовать историю в JSON для Redis.

    Сохраняет все поля Messages, необходимые для восстановления:
    role, content, name, function_call, functions_state_id.

    GigaChat SDK использует Pydantic-модели:
    - msg.role: str (не enum, а строка "system"/"user"/"assistant"/"function")
    - msg.function_call.arguments: dict | None
    """
    items = []
    for msg in history:
        item: dict = {"role": str(msg.role), "content": msg.content}
        # Проверяем через `is not None` — атрибуты всегда определены
        # в Pydantic-модели Messages, hasattr всегда вернёт True
        if msg.name is not None:
            item["name"] = msg.name
        if msg.function_call is not None:
            # arguments — dict в SDK, сериализуем в JSON-строку для Redis
            fc_args = msg.function_call.arguments
            if isinstance(fc_args, dict):
                fc_args = json.dumps(fc_args, ensure_ascii=False)
            item["function_call"] = {
                "name": msg.function_call.name,
                "arguments": fc_args,
            }
        if getattr(msg, "functions_state_id", None) is not None:
            item["functions_state_id"] = msg.functions_state_id
        items.append(item)
    return json.dumps(items, ensure_ascii=False)


def _deserialize(raw: str | bytes) -> list[Messages]:
    """Десериализовать JSON из Redis в list[Messages].

    Args:
        raw: JSON-строка или bytes из Redis.

    Returns:
        Восстановленный список Messages.

    Raises:
        json.JSONDecodeError: Если JSON невалиден.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
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
            # arguments: JSON-строка → dict для Pydantic FunctionCall
            fc_args = fc.get("arguments")
            if isinstance(fc_args, str):
                try:
                    fc_args = json.loads(fc_args)
                except (json.JSONDecodeError, TypeError):
                    fc_args = None
            msg.function_call = FunctionCall(
                name=fc["name"],
                arguments=fc_args,
            )
        if "functions_state_id" in item:
            msg.functions_state_id = item["functions_state_id"]
        messages.append(msg)
    return messages
