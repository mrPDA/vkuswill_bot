"""Персистентный менеджер диалогов на Redis.

Реализует тот же async-интерфейс, что и DialogManager (aget_history,
save_history, trim_list, areset, get_lock), но хранит историю в Redis
с TTL — диалоги переживают рестарт бота.

Redis-структура:
    dialog:{user_id}  →  JSON-строка (сериализованный list[dict])
                          TTL: dialog_ttl секунд (по умолчанию 24 часа)
"""

import asyncio
import json
import logging
from collections import OrderedDict
from typing import Any

from redis.asyncio import Redis

from vkuswill_bot.services.dialog_history_utils import trim_message_list
from vkuswill_bot.services.prompts import get_system_prompt

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
        Занятые locks (locked) не вытесняются — это защищает
        от race condition при высокой нагрузке.
        """
        if user_id in self._locks:
            self._locks.move_to_end(user_id)
            return self._locks[user_id]
        self._evict_idle()
        lock = asyncio.Lock()
        self._locks[user_id] = lock
        return lock

    def _evict_idle(self) -> None:
        """Удалить idle (не захваченные) locks, если лимит достигнут."""
        while len(self._locks) >= MAX_LOCKS:
            evicted = False
            for uid in list(self._locks):
                if not self._locks[uid].locked():
                    del self._locks[uid]
                    evicted = True
                    break
            if not evicted:
                logger.warning(
                    "All %d locks are active, temporarily exceeding limit",
                    len(self._locks),
                )
                break

    # ---- Async API ----

    async def aget_history(self, user_id: int) -> list[dict[str, Any]]:
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
        return [{"role": "system", "content": get_system_prompt()}]

    async def save_history(
        self,
        user_id: int,
        history: list[dict[str, Any]],
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

    def trim_list(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
# Сериализация / десериализация истории (list[dict])
# ============================================================================


def _serialize(history: list[dict[str, Any]]) -> str:
    """Сериализовать историю в JSON для Redis.

    Сохраняет все поля: role, content, name, function_call, functions_state_id.
    function_call.arguments: dict сериализуется в JSON-строку для компактности.
    """
    items: list[dict[str, Any]] = []
    for msg in history:
        item: dict[str, Any] = {
            "role": str(msg.get("role", "")),
            "content": msg.get("content", ""),
        }
        if msg.get("name") is not None:
            item["name"] = msg["name"]
        fc = msg.get("function_call")
        if fc is not None and isinstance(fc, dict):
            fc_args = fc.get("arguments")
            if isinstance(fc_args, dict):
                fc_args = json.dumps(fc_args, ensure_ascii=False)
            item["function_call"] = {
                "name": fc.get("name", ""),
                "arguments": fc_args,
            }
        if msg.get("functions_state_id") is not None:
            item["functions_state_id"] = msg["functions_state_id"]
        items.append(item)
    return json.dumps(items, ensure_ascii=False)


def _deserialize(raw: str | bytes) -> list[dict[str, Any]]:
    """Десериализовать JSON из Redis в list[dict].

    Args:
        raw: JSON-строка или bytes из Redis.

    Returns:
        Восстановленный список сообщений (dict с role, content и опционально
        name, function_call, functions_state_id).
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    items = json.loads(raw)
    messages: list[dict[str, Any]] = []
    for item in items:
        msg: dict[str, Any] = {
            "role": item.get("role", ""),
            "content": item.get("content", ""),
        }
        if "name" in item:
            msg["name"] = item["name"]
        if "function_call" in item:
            fc = item["function_call"]
            fc_args = fc.get("arguments")
            if isinstance(fc_args, str):
                try:
                    fc_args = json.loads(fc_args)
                except (json.JSONDecodeError, TypeError):
                    fc_args = None
            msg["function_call"] = {
                "name": fc.get("name", ""),
                "arguments": fc_args,
            }
        if "functions_state_id" in item:
            msg["functions_state_id"] = item["functions_state_id"]
        messages.append(msg)
    return messages
