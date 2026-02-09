"""Тесты redis_client: создание, закрытие, health check, маскировка URL.

Тестируем:
- _mask_url: маскировка пароля в Redis URL для логирования
- create_redis_client: фабрика Redis-клиента с health check
- close_redis_client: безопасное закрытие клиента
- check_redis_health: проверка доступности Redis
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import RedisError

from vkuswill_bot.services.redis_client import (
    _mask_url,
    check_redis_health,
    close_redis_client,
    create_redis_client,
)


# ============================================================================
# _mask_url: маскировка пароля в Redis URL
# ============================================================================


class TestMaskUrl:
    """Тесты _mask_url: маскировка пароля в Redis URL."""

    def test_masks_password(self):
        """Пароль в URL маскируется звёздочками."""
        url = "redis://:secret_password@host:6379/0"
        result = _mask_url(url)
        assert result == "redis://:***@host:6379/0"

    def test_masks_long_password(self):
        """Длинный пароль корректно маскируется."""
        url = "redis://:very_long_secret_password_12345@redis.example.com:6379/2"
        result = _mask_url(url)
        assert result == "redis://:***@redis.example.com:6379/2"

    def test_url_without_password(self):
        """URL без пароля (без @) возвращается как есть."""
        url = "redis://localhost:6379/0"
        result = _mask_url(url)
        assert result == url

    def test_url_with_user_and_password(self):
        """URL с пользователем и паролем."""
        url = "redis://user:pass@host:6379/0"
        result = _mask_url(url)
        assert result == "redis://:***@host:6379/0"

    def test_empty_url(self):
        """Пустой URL возвращается как есть."""
        assert _mask_url("") == ""

    def test_rediss_scheme(self):
        """URL со схемой rediss:// (TLS)."""
        url = "rediss://:secret@host:6380/0"
        result = _mask_url(url)
        assert result == "rediss://:***@host:6380/0"

    def test_no_scheme(self):
        """URL без схемы :// — возвращается как есть при наличии @."""
        url = "user:pass@host:6379"
        result = _mask_url(url)
        # Нет "://" — пароль не маскируется
        assert result == url


# ============================================================================
# create_redis_client
# ============================================================================


class TestCreateRedisClient:
    """Тесты create_redis_client: фабрика Redis-клиента."""

    async def test_creates_client_and_pings(self):
        """Создаёт клиент и проверяет соединение через PING."""
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        with patch(
            "vkuswill_bot.services.redis_client.Redis.from_url",
            return_value=mock_redis,
        ):
            client = await create_redis_client("redis://localhost:6379/0")

        assert client is mock_redis
        mock_redis.ping.assert_called_once()

    async def test_passes_parameters(self):
        """Передаёт параметры decode_responses и таймауты."""
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        with patch(
            "vkuswill_bot.services.redis_client.Redis.from_url",
            return_value=mock_redis,
        ) as mock_from_url:
            await create_redis_client(
                "redis://localhost:6379/0",
                decode_responses=True,
                socket_connect_timeout=10.0,
                socket_timeout=10.0,
            )

        mock_from_url.assert_called_once_with(
            "redis://localhost:6379/0",
            decode_responses=True,
            socket_connect_timeout=10.0,
            socket_timeout=10.0,
        )

    async def test_raises_on_ping_failure(self):
        """Бросает RedisError если Redis недоступен."""
        mock_redis = AsyncMock()
        mock_redis.ping.side_effect = RedisError("Connection refused")

        with patch(
            "vkuswill_bot.services.redis_client.Redis.from_url",
            return_value=mock_redis,
        ):
            with pytest.raises(RedisError, match="Connection refused"):
                await create_redis_client("redis://localhost:6379/0")

    async def test_default_timeouts(self):
        """По умолчанию таймауты 5 секунд."""
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        with patch(
            "vkuswill_bot.services.redis_client.Redis.from_url",
            return_value=mock_redis,
        ) as mock_from_url:
            await create_redis_client("redis://localhost:6379/0")

        _, kwargs = mock_from_url.call_args
        assert kwargs["socket_connect_timeout"] == 5.0
        assert kwargs["socket_timeout"] == 5.0
        assert kwargs["decode_responses"] is False


# ============================================================================
# close_redis_client
# ============================================================================


class TestCloseRedisClient:
    """Тесты close_redis_client: безопасное закрытие."""

    async def test_closes_client(self):
        """Закрывает клиент через aclose()."""
        mock_redis = AsyncMock()
        await close_redis_client(mock_redis)
        mock_redis.aclose.assert_called_once()

    async def test_none_is_noop(self):
        """None на входе — ничего не делает (noop)."""
        await close_redis_client(None)  # не должно бросить

    async def test_handles_error_gracefully(self):
        """Ошибка при закрытии логируется, не бросается."""
        mock_redis = AsyncMock()
        mock_redis.aclose.side_effect = RedisError("Close error")

        # Не должно бросить исключение
        await close_redis_client(mock_redis)


# ============================================================================
# check_redis_health
# ============================================================================


class TestCheckRedisHealth:
    """Тесты check_redis_health: проверка доступности Redis."""

    async def test_healthy_returns_true(self):
        """Redis отвечает на PING → True."""
        mock_redis = AsyncMock()
        mock_redis.ping.return_value = True

        result = await check_redis_health(mock_redis)
        assert result is True

    async def test_unhealthy_returns_false(self):
        """Redis не отвечает → False."""
        mock_redis = AsyncMock()
        mock_redis.ping.side_effect = RedisError("Connection lost")

        result = await check_redis_health(mock_redis)
        assert result is False
