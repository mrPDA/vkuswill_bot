"""Кэш цен товаров ВкусВилл.

Централизованное хранилище информации о ценах (xml_id → PriceInfo).
Используется SearchProcessor (запись) и CartProcessor (чтение).

Архитектура:
- PriceCache — in-memory async-кэш (L1), FIFO-вытеснение.
- TwoLevelPriceCache — L1 (in-memory) + L2 (Redis), async get/set.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

MAX_PRICE_CACHE_SIZE = 5000

# TTL для Redis L2 (1 час — цены обновляются часто)
DEFAULT_PRICE_TTL = 3600


class PriceInfo:
    """Кэшированная информация о цене товара."""

    __slots__ = ("name", "price", "unit")

    def __init__(self, name: str, price: float, unit: str = "шт") -> None:
        self.name = name
        self.price = price
        self.unit = unit

    def __getitem__(self, key: str) -> str | float:
        """Совместимость с dict-API: info['name'], info['price'], info['unit']."""
        return getattr(self, key)

    def get(self, key: str, default: object = None) -> object:
        """Совместимость с dict-API: info.get('unit', 'шт')."""
        return getattr(self, key, default)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PriceInfo):
            return NotImplemented
        return self.name == other.name and self.price == other.price and self.unit == other.unit

    def __repr__(self) -> str:
        return f"PriceInfo(name={self.name!r}, price={self.price}, unit={self.unit!r})"


class PriceCache:
    """Кэш цен товаров ВкусВилл (xml_id → PriceInfo).

    Async-интерфейс: get() и set() — корутины.
    FIFO-вытеснение при превышении лимита.
    Sync dict-API (__setitem__, __getitem__) работает через _set_sync/_get_sync.
    """

    def __init__(self, max_size: int = MAX_PRICE_CACHE_SIZE) -> None:
        self._max_size = max_size
        self._data: dict[int, PriceInfo] = {}

    # ---- Internal sync methods (для dict-API и подклассов) ----

    def _set_sync(self, xml_id: int, name: str, price: float, unit: str = "шт") -> None:
        """Синхронная запись в L1 (in-memory)."""
        self._data[xml_id] = PriceInfo(name, price, unit)
        self._evict_if_needed()

    def _get_sync(self, xml_id: int) -> PriceInfo | None:
        """Синхронное чтение из L1 (in-memory)."""
        return self._data.get(xml_id)

    # ---- Public async API ----

    async def set(self, xml_id: int, name: str, price: float, unit: str = "шт") -> None:
        """Сохранить информацию о цене товара (async)."""
        self._set_sync(xml_id, name, price, unit)

    async def get(self, xml_id: int) -> PriceInfo | None:
        """Получить информацию о цене товара (async, или None)."""
        return self._get_sync(xml_id)

    # ---- Sync dict-compatible API ----

    def __bool__(self) -> bool:
        """PriceCache всегда truthy — даже пустой кэш является валидным объектом.

        Без этого метода Python использует __len__ для bool(),
        и пустой кэш оценивается как False, ломая паттерн ``cache or default``.
        """
        return True

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, xml_id: int) -> bool:
        return xml_id in self._data

    def __setitem__(self, xml_id: int, value: dict) -> None:
        """Совместимость с dict-API: price_cache[id] = {"name": ..., "price": ..., "unit": ...}."""
        self._set_sync(
            xml_id,
            name=value.get("name", ""),
            price=value.get("price", 0),
            unit=value.get("unit", "шт"),
        )

    def __getitem__(self, xml_id: int) -> PriceInfo:
        """Совместимость с dict-API: price_cache[id] → PriceInfo."""
        item = self._data.get(xml_id)
        if item is None:
            raise KeyError(xml_id)
        return item

    def _evict_if_needed(self) -> None:
        """FIFO-вытеснение при превышении лимита."""
        if len(self._data) > self._max_size:
            keys = list(self._data.keys())[: self._max_size // 2]
            for k in keys:
                del self._data[k]
            logger.info("PriceCache: evicted %d entries", len(keys))


class TwoLevelPriceCache(PriceCache):
    """Двухуровневый кэш цен: L1 (in-memory) + L2 (Redis).

    - get(): L1 → L2 fallthrough с автоматическим promote в L1.
    - set(): запись в оба уровня.
    - При ошибке Redis — graceful fallback на L1 only.

    Рекомендован ADR-001 для предсказуемого поведения после рестарта:
    L1 пуст, но L2 содержит данные предыдущей сессии.
    """

    def __init__(
        self,
        redis: Redis,
        ttl: int = DEFAULT_PRICE_TTL,
        max_size: int = MAX_PRICE_CACHE_SIZE,
    ) -> None:
        super().__init__(max_size=max_size)
        self._redis = redis
        self._ttl = ttl

    async def get(self, xml_id: int) -> PriceInfo | None:
        """L1 → L2 fallthrough с автоматическим promote."""
        # L1 (fast path)
        result = self._get_sync(xml_id)
        if result is not None:
            return result
        # L2 (Redis)
        try:
            data = await self._redis.hgetall(f"price:{xml_id}")
            if data:
                info = PriceInfo(
                    name=data[b"name"].decode(),
                    price=float(data[b"price"]),
                    unit=data[b"unit"].decode(),
                )
                self._data[xml_id] = info  # promote to L1
                return info
        except Exception as e:
            logger.warning("Redis L2 get error for price:%d: %s", xml_id, e)
        return None

    async def set(self, xml_id: int, name: str, price: float, unit: str = "шт") -> None:
        """Запись в оба уровня: L1 + L2."""
        # L1
        self._set_sync(xml_id, name, price, unit)
        # L2
        try:
            key = f"price:{xml_id}"
            await self._redis.hset(
                key,
                mapping={
                    "name": name,
                    "price": str(price),
                    "unit": unit,
                },
            )
            await self._redis.expire(key, self._ttl)
        except Exception as e:
            logger.warning("Redis L2 set error for price:%d: %s", xml_id, e)
