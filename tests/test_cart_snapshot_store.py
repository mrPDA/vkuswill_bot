"""Тесты CartSnapshotStore.

Тестируем:
- Сохранение снимка корзины в Redis (mock)
- Чтение снимка корзины
- Удаление снимка корзины
- Graceful error handling
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from vkuswill_bot.services.cart_snapshot_store import (
    CART_SNAPSHOT_TTL,
    CartSnapshotStore,
)


@pytest.fixture
def mock_redis():
    """Мок Redis-клиента."""
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    return redis


@pytest.fixture
def store(mock_redis) -> CartSnapshotStore:
    """Экземпляр CartSnapshotStore с мок-Redis."""
    return CartSnapshotStore(redis=mock_redis)


class TestSave:
    """Тесты save: сохранение снимка корзины."""

    async def test_saves_snapshot(self, store, mock_redis):
        """Сохраняет снимок корзины в Redis с TTL."""
        products = [{"xml_id": 100, "q": 2}]
        await store.save(user_id=42, products=products, link="https://vkusvill.ru/cart/123", total=158.0)

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert call_args[0][0] == "cart:42"
        data = json.loads(call_args[0][1])
        assert data["products"] == products
        assert data["link"] == "https://vkusvill.ru/cart/123"
        assert data["total"] == 158.0
        assert "created_at" in data
        assert call_args[1]["ex"] == CART_SNAPSHOT_TTL

    async def test_saves_without_total(self, store, mock_redis):
        """Сохраняет снимок без total (None)."""
        await store.save(user_id=42, products=[], link="", total=None)

        data = json.loads(mock_redis.set.call_args[0][1])
        assert data["total"] is None

    async def test_redis_error_graceful(self, store, mock_redis):
        """Ошибка Redis при сохранении не крашит."""
        mock_redis.set.side_effect = Exception("connection lost")
        # Не должно поднимать исключение
        await store.save(user_id=42, products=[], link="", total=None)


class TestGet:
    """Тесты get: чтение снимка корзины."""

    async def test_returns_snapshot(self, store, mock_redis):
        """Возвращает сохранённый снимок."""
        snapshot = {
            "products": [{"xml_id": 100, "q": 2}],
            "link": "https://vkusvill.ru/cart/123",
            "total": 158.0,
            "created_at": "2026-01-15T12:00:00+00:00",
        }
        mock_redis.get.return_value = json.dumps(snapshot)

        result = await store.get(user_id=42)

        assert result is not None
        assert result["products"] == snapshot["products"]
        assert result["total"] == 158.0
        mock_redis.get.assert_called_once_with("cart:42")

    async def test_returns_none_for_missing(self, store, mock_redis):
        """Возвращает None, если снимка нет."""
        mock_redis.get.return_value = None
        result = await store.get(user_id=42)
        assert result is None

    async def test_returns_none_for_invalid_json(self, store, mock_redis):
        """Возвращает None при невалидном JSON."""
        mock_redis.get.return_value = "not json{{"
        result = await store.get(user_id=42)
        assert result is None

    async def test_returns_none_for_non_dict(self, store, mock_redis):
        """Возвращает None, если JSON — не dict."""
        mock_redis.get.return_value = json.dumps([1, 2, 3])
        result = await store.get(user_id=42)
        assert result is None

    async def test_redis_error_graceful(self, store, mock_redis):
        """Ошибка Redis при чтении не крашит, возвращает None."""
        mock_redis.get.side_effect = Exception("connection lost")
        result = await store.get(user_id=42)
        assert result is None


class TestDelete:
    """Тесты delete: удаление снимка корзины."""

    async def test_deletes_snapshot(self, store, mock_redis):
        """Удаляет снимок по user_id."""
        await store.delete(user_id=42)
        mock_redis.delete.assert_called_once_with("cart:42")

    async def test_redis_error_graceful(self, store, mock_redis):
        """Ошибка Redis при удалении не крашит."""
        mock_redis.delete.side_effect = Exception("connection lost")
        await store.delete(user_id=42)


class TestCustomTTL:
    """Тесты с кастомным TTL."""

    async def test_custom_ttl_applied(self):
        """Кастомный TTL передаётся в Redis."""
        redis = AsyncMock()
        store = CartSnapshotStore(redis=redis, ttl=3600)
        await store.save(user_id=1, products=[], link="", total=None)
        assert redis.set.call_args[1]["ex"] == 3600
