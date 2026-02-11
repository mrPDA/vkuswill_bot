"""Хранилище снимков корзины в Redis.

Сохраняет последнюю корзину пользователя (товары, ссылку, стоимость)
после успешного создания корзины. TTL = 24 часа.

Redis-структура:
    cart:{user_id} → JSON (products, link, total, created_at)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

# TTL снимка корзины (24 часа)
CART_SNAPSHOT_TTL = 86400

# Префикс ключа в Redis
_KEY_PREFIX = "cart:"


class InMemoryCartSnapshotStore:
    """In-memory fallback для хранения снимков корзины (без Redis).

    Хранит последний снимок корзины каждого пользователя в памяти.
    При рестарте бота данные теряются.
    """

    def __init__(self, ttl: int = CART_SNAPSHOT_TTL) -> None:
        self._ttl = ttl
        self._data: dict[int, dict] = {}

    async def save(
        self,
        user_id: int,
        products: list[dict],
        link: str,
        total: float | None = None,
    ) -> None:
        """Сохранить снимок корзины в памяти."""
        self._data[user_id] = {
            "products": products,
            "link": link,
            "total": total,
            "created_at": datetime.now(UTC).isoformat(),
        }
        logger.debug(
            "Снимок корзины сохранён (in-memory): user=%d, products=%d",
            user_id,
            len(products),
        )

    async def get(self, user_id: int) -> dict | None:
        """Получить последний снимок корзины пользователя."""
        return self._data.get(user_id)

    async def delete(self, user_id: int) -> None:
        """Удалить снимок корзины пользователя."""
        self._data.pop(user_id, None)


class CartSnapshotStore:
    """Сохранение и получение последней корзины пользователя из Redis."""

    def __init__(self, redis: Redis, ttl: int = CART_SNAPSHOT_TTL) -> None:
        self._redis = redis
        self._ttl = ttl

    async def save(
        self,
        user_id: int,
        products: list[dict],
        link: str,
        total: float | None = None,
    ) -> None:
        """Сохранить снимок корзины.

        Args:
            user_id: ID пользователя Telegram.
            products: Список товаров [{xml_id, q, ...}].
            link: Ссылка на корзину ВкусВилл.
            total: Общая стоимость (None если не удалось рассчитать).
        """
        snapshot = {
            "products": products,
            "link": link,
            "total": total,
            "created_at": datetime.now(UTC).isoformat(),
        }
        key = f"{_KEY_PREFIX}{user_id}"
        try:
            await self._redis.set(
                key,
                json.dumps(snapshot, ensure_ascii=False),
                ex=self._ttl,
            )
            logger.info(
                "Снимок корзины сохранён: user=%d, products=%d, total=%s",
                user_id,
                len(products),
                total,
            )
        except Exception as e:
            logger.warning(
                "Ошибка сохранения снимка корзины user=%d: %s",
                user_id,
                e,
            )

    async def get(self, user_id: int) -> dict | None:
        """Получить последний снимок корзины пользователя.

        Returns:
            Словарь {products, link, total, created_at} или None.
        """
        key = f"{_KEY_PREFIX}{user_id}"
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            return data
        except Exception as e:
            logger.warning(
                "Ошибка чтения снимка корзины user=%d: %s",
                user_id,
                e,
            )
            return None

    async def delete(self, user_id: int) -> None:
        """Удалить снимок корзины пользователя."""
        key = f"{_KEY_PREFIX}{user_id}"
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning(
                "Ошибка удаления снимка корзины user=%d: %s",
                user_id,
                e,
            )
