"""Кэш цен товаров ВкусВилл.

Централизованное хранилище информации о ценах (xml_id → PriceInfo).
Используется SearchProcessor (запись) и CartProcessor (чтение).
"""

import logging

logger = logging.getLogger(__name__)

MAX_PRICE_CACHE_SIZE = 5000


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

    FIFO-вытеснение при превышении лимита.
    Готов к замене на Redis в будущем.
    """

    def __init__(self, max_size: int = MAX_PRICE_CACHE_SIZE) -> None:
        self._max_size = max_size
        self._data: dict[int, PriceInfo] = {}

    def set(self, xml_id: int, name: str, price: float, unit: str = "шт") -> None:
        """Сохранить информацию о цене товара."""
        self._data[xml_id] = PriceInfo(name, price, unit)
        self._evict_if_needed()

    def get(self, xml_id: int) -> PriceInfo | None:
        """Получить информацию о цене товара (или None)."""
        return self._data.get(xml_id)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, xml_id: int) -> bool:
        return xml_id in self._data

    def __setitem__(self, xml_id: int, value: dict) -> None:
        """Совместимость с dict-API: price_cache[id] = {"name": ..., "price": ..., "unit": ...}."""
        self.set(xml_id, name=value.get("name", ""), price=value.get("price", 0), unit=value.get("unit", "шт"))

    def __getitem__(self, xml_id: int) -> "PriceInfo":
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
