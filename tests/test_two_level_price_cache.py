"""Тесты TwoLevelPriceCache (L1 in-memory + L2 Redis).

Тестируем:
- L1 hit (sync fast-path)
- L2 hit с promote в L1
- Miss (L1 + L2)
- set() пишет в оба уровня
- Redis-ошибки → graceful fallback на L1
"""

import pytest
from unittest.mock import AsyncMock

from vkuswill_bot.services.price_cache import PriceInfo, TwoLevelPriceCache


@pytest.fixture
def mock_redis():
    """Мок Redis-клиента."""
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.expire = AsyncMock()
    return redis


@pytest.fixture
def cache(mock_redis) -> TwoLevelPriceCache:
    """TwoLevelPriceCache с мок-Redis."""
    return TwoLevelPriceCache(redis=mock_redis, ttl=3600)


class TestGet:
    """Тесты get: L1 → L2 fallthrough."""

    async def test_l1_hit(self, cache, mock_redis):
        """L1 hit — Redis не вызывается."""
        cache._data[100] = PriceInfo("Молоко", 79.0, "шт")

        result = await cache.get(100)

        assert result is not None
        assert result.name == "Молоко"
        assert result.price == 79.0
        mock_redis.hgetall.assert_not_called()

    async def test_l2_hit_promotes_to_l1(self, cache, mock_redis):
        """L2 hit — данные загружаются из Redis и промотируются в L1."""
        mock_redis.hgetall.return_value = {
            b"name": b"\xd0\x9c\xd0\xbe\xd0\xbb\xd0\xbe\xd0\xba\xd0\xbe",  # "Молоко" в UTF-8
            b"price": b"79.0",
            b"unit": b"\xd1\x88\xd1\x82",  # "шт" в UTF-8
        }

        result = await cache.get(100)

        assert result is not None
        assert result.name == "Молоко"
        assert result.price == 79.0
        assert result.unit == "шт"
        mock_redis.hgetall.assert_called_once_with("price:100")
        # Промотировано в L1
        assert 100 in cache._data

    async def test_l2_hit_second_call_hits_l1(self, cache, mock_redis):
        """После промоции повторный вызов идёт из L1."""
        mock_redis.hgetall.return_value = {
            b"name": b"Milk",
            b"price": b"79.0",
            b"unit": b"sht",
        }

        await cache.get(100)  # L2 hit → promote
        mock_redis.hgetall.reset_mock()
        result = await cache.get(100)  # L1 hit

        assert result is not None
        mock_redis.hgetall.assert_not_called()

    async def test_miss_both_levels(self, cache, mock_redis):
        """Miss в обоих уровнях → None."""
        mock_redis.hgetall.return_value = {}

        result = await cache.get(999)

        assert result is None

    async def test_redis_error_returns_none(self, cache, mock_redis):
        """Ошибка Redis → graceful fallback (None, L1 пуст)."""
        mock_redis.hgetall.side_effect = Exception("connection lost")

        result = await cache.get(999)

        assert result is None


class TestSet:
    """Тесты set: запись в L1 + L2."""

    async def test_writes_to_both_levels(self, cache, mock_redis):
        """set() пишет в L1 и L2."""
        await cache.set(100, "Молоко", 79.0, "шт")

        # L1
        assert 100 in cache._data
        assert cache._data[100].name == "Молоко"
        # L2
        mock_redis.hset.assert_called_once_with(
            "price:100",
            mapping={"name": "Молоко", "price": "79.0", "unit": "шт"},
        )
        mock_redis.expire.assert_called_once_with("price:100", 3600)

    async def test_redis_error_still_writes_l1(self, cache, mock_redis):
        """Ошибка Redis → L1 всё равно обновляется."""
        mock_redis.hset.side_effect = Exception("connection lost")

        await cache.set(100, "Молоко", 79.0, "шт")

        # L1 работает
        assert 100 in cache._data
        assert cache._data[100].price == 79.0


class TestDictAPI:
    """Тесты sync dict-API (от PriceCache)."""

    async def test_setitem_writes_l1_only(self, cache, mock_redis):
        """__setitem__ пишет только в L1 (sync)."""
        cache[100] = {"name": "Молоко", "price": 79.0, "unit": "шт"}

        assert 100 in cache._data
        # Redis НЕ вызывается (sync shortcut)
        mock_redis.hset.assert_not_called()

    def test_getitem_reads_l1_only(self, cache):
        """__getitem__ читает только из L1 (sync)."""
        cache._data[100] = PriceInfo("Молоко", 79.0, "шт")

        info = cache[100]

        assert info["name"] == "Молоко"

    def test_contains(self, cache):
        """__contains__ проверяет только L1."""
        assert 100 not in cache
        cache._data[100] = PriceInfo("Молоко", 79.0, "шт")
        assert 100 in cache

    def test_len(self, cache):
        """__len__ считает только L1."""
        assert len(cache) == 0
        cache._data[100] = PriceInfo("Молоко", 79.0, "шт")
        assert len(cache) == 1

    def test_bool_always_true(self, cache):
        """Пустой TwoLevelPriceCache — truthy."""
        assert bool(cache) is True


class TestCustomTTL:
    """Тесты с кастомным TTL."""

    async def test_custom_ttl(self):
        """Кастомный TTL передаётся в expire."""
        redis = AsyncMock()
        cache = TwoLevelPriceCache(redis=redis, ttl=7200)

        await cache.set(1, "item", 10.0)

        redis.expire.assert_called_once_with("price:1", 7200)


class TestEviction:
    """Тесты FIFO-вытеснения L1 (наследуется от PriceCache)."""

    async def test_l1_eviction(self):
        """L1 вытесняет старые записи при превышении max_size."""
        redis = AsyncMock()
        cache = TwoLevelPriceCache(redis=redis, ttl=3600, max_size=5)

        for i in range(6):
            await cache.set(i, f"item_{i}", float(i))

        assert len(cache) <= 5
