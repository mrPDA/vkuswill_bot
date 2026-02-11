"""Тесты PriceCache и PriceInfo.

Тестируем:
- CRUD операции (set, get, __len__, __contains__)
- FIFO-вытеснение при превышении лимита
- Dict-совместимый API (__setitem__, __getitem__)
- PriceInfo: slots, __eq__, __repr__, dict-совместимость
"""

import pytest

from vkuswill_bot.services.price_cache import MAX_PRICE_CACHE_SIZE, PriceCache, PriceInfo


# ============================================================================
# PriceInfo
# ============================================================================


class TestPriceInfo:
    """Тесты PriceInfo."""

    def test_create_default_unit(self):
        info = PriceInfo("Молоко", 79.0)
        assert info.name == "Молоко"
        assert info.price == 79.0
        assert info.unit == "шт"

    def test_create_custom_unit(self):
        info = PriceInfo("Картофель", 135.0, "кг")
        assert info.unit == "кг"

    def test_eq(self):
        a = PriceInfo("Молоко", 79.0, "шт")
        b = PriceInfo("Молоко", 79.0, "шт")
        assert a == b

    def test_neq(self):
        a = PriceInfo("Молоко", 79.0)
        b = PriceInfo("Хлеб", 50.0)
        assert a != b

    def test_eq_with_non_priceinfo(self):
        info = PriceInfo("Молоко", 79.0)
        assert info != "not a PriceInfo"

    def test_repr(self):
        info = PriceInfo("Молоко", 79.0, "шт")
        assert "Молоко" in repr(info)
        assert "79.0" in repr(info)

    def test_slots(self):
        info = PriceInfo("Молоко", 79.0)
        with pytest.raises(AttributeError):
            info.extra = "nope"  # type: ignore[attr-defined]

    def test_dict_getitem(self):
        """PriceInfo поддерживает info['name'], info['price'], info['unit']."""
        info = PriceInfo("Молоко", 79.0, "шт")
        assert info["name"] == "Молоко"
        assert info["price"] == 79.0
        assert info["unit"] == "шт"

    def test_dict_get(self):
        """PriceInfo поддерживает info.get('unit', 'шт')."""
        info = PriceInfo("Молоко", 79.0, "кг")
        assert info.get("unit", "шт") == "кг"


# ============================================================================
# PriceCache
# ============================================================================


class TestPriceCache:
    """Тесты PriceCache."""

    @pytest.fixture
    def cache(self) -> PriceCache:
        return PriceCache()

    async def test_set_and_get(self, cache):
        await cache.set(100, "Молоко", 79.0, "шт")
        info = await cache.get(100)
        assert info is not None
        assert info.name == "Молоко"
        assert info.price == 79.0
        assert info.unit == "шт"

    async def test_get_missing(self, cache):
        assert await cache.get(999) is None

    async def test_len(self, cache):
        assert len(cache) == 0
        await cache.set(1, "A", 10.0)
        assert len(cache) == 1
        await cache.set(2, "B", 20.0)
        assert len(cache) == 2

    async def test_contains(self, cache):
        assert 100 not in cache
        await cache.set(100, "Молоко", 79.0)
        assert 100 in cache

    async def test_overwrite(self, cache):
        await cache.set(100, "Молоко", 79.0)
        await cache.set(100, "Молоко 3.2%", 89.0)
        info = await cache.get(100)
        assert info.name == "Молоко 3.2%"
        assert len(cache) == 1

    async def test_dict_setitem(self, cache):
        """Совместимость: cache[id] = {...}."""
        cache[100] = {"name": "Молоко", "price": 79.0, "unit": "шт"}
        info = await cache.get(100)
        assert info.name == "Молоко"
        assert info.price == 79.0

    def test_dict_getitem(self, cache):
        """Совместимость: cache[id] → PriceInfo (sync)."""
        cache[100] = {"name": "Молоко", "price": 79.0, "unit": "шт"}
        info = cache[100]
        assert info["name"] == "Молоко"

    def test_dict_getitem_missing_raises(self, cache):
        with pytest.raises(KeyError):
            _ = cache[999]

    def test_default_max_size(self):
        cache = PriceCache()
        assert cache._max_size == MAX_PRICE_CACHE_SIZE

    def test_custom_max_size(self):
        cache = PriceCache(max_size=10)
        assert cache._max_size == 10


class TestPriceCacheBool:
    """Тесты: пустой PriceCache должен быть truthy (иначе ломается DI)."""

    def test_empty_cache_is_truthy(self):
        """Пустой PriceCache должен быть truthy.

        Без __bool__ Python использует __len__ для bool(),
        и пустой кэш оценивается как False, ломая паттерн
        ``cache or PriceCache()`` в SearchProcessor.__init__.

        Баг: SearchProcessor и CartProcessor работали с разными PriceCache,
        потому что ``price_cache or PriceCache()`` создавал новый объект.
        """
        cache = PriceCache()
        assert bool(cache) is True

    async def test_non_empty_cache_is_truthy(self):
        cache = PriceCache()
        await cache.set(1, "item", 10.0)
        assert bool(cache) is True

    def test_or_pattern_preserves_empty_cache(self):
        """``price_cache or PriceCache()`` должен вернуть переданный кэш."""
        original = PriceCache()
        result = original or PriceCache()
        assert result is original

    async def test_di_shared_cache_e2e(self):
        """E2E: SearchProcessor и CartProcessor должны разделять один PriceCache.

        Воспроизводит production-баг: бот перестал считать цену корзины
        после рефакторинга, потому что SearchProcessor создавал свой PriceCache
        вместо использования общего (пустой PriceCache был falsy).
        """
        import json
        from vkuswill_bot.services.cart_processor import CartProcessor
        from vkuswill_bot.services.search_processor import SearchProcessor

        # Как в __main__.py
        shared_cache = PriceCache()
        sp = SearchProcessor(shared_cache)
        cp = CartProcessor(shared_cache)

        # Проверяем: один и тот же объект
        assert sp.price_cache is cp._price_cache

        # Имитируем поиск → кэшируем цены
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "meta": {"q": "молоко", "total": 1},
                    "items": [
                        {
                            "xml_id": 100,
                            "name": "Молоко 3.2%",
                            "price": {"current": 79},
                            "unit": "шт",
                        }
                    ],
                },
            }
        )
        await sp.cache_prices(search_result)
        assert len(shared_cache) == 1
        assert await shared_cache.get(100) is not None
        assert (await shared_cache.get(100)).price == 79

        # Имитируем корзину → рассчитываем стоимость
        cart_args = {"products": [{"xml_id": 100, "q": 2}]}
        cart_result = json.dumps(
            {
                "ok": True,
                "data": {"link": "https://vkusvill.ru/cart/123"},
            }
        )
        result = await cp.calc_total(cart_args, cart_result)
        data = json.loads(result)

        summary = data["data"]["price_summary"]
        assert summary["total"] == 158.0
        assert "Молоко 3.2%" in summary["items"][0]
        assert "цена неизвестна" not in summary["items"][0]


class TestPriceCacheEviction:
    """Тесты FIFO-вытеснения."""

    async def test_eviction_on_overflow(self):
        cache = PriceCache(max_size=10)
        for i in range(11):
            await cache.set(i, f"item_{i}", float(i))
        # После вытеснения должно остаться <= max_size
        assert len(cache) <= 10

    async def test_old_entries_evicted_first(self):
        cache = PriceCache(max_size=10)
        for i in range(11):
            await cache.set(i, f"item_{i}", float(i))
        # Последний элемент (10) должен остаться
        assert await cache.get(10) is not None
        # Первые элементы (0-4) должны быть вытеснены
        evicted = 0
        for i in range(5):
            if await cache.get(i) is None:
                evicted += 1
        assert evicted == 5
