"""Инициализация и управление Redis-клиентом.

Предоставляет фабрику для создания redis.asyncio.Redis
с health check и graceful close.
"""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


async def create_redis_client(
    redis_url: str,
    *,
    decode_responses: bool = False,
    socket_connect_timeout: float = 5.0,
    socket_timeout: float = 5.0,
) -> Redis:
    """Создать Redis-клиент и проверить соединение.

    Args:
        redis_url: URL подключения (redis://:password@host:port/db).
        decode_responses: Декодировать bytes → str (False для бинарных данных).
        socket_connect_timeout: Таймаут подключения (секунды).
        socket_timeout: Таймаут операций (секунды).

    Returns:
        Подключённый Redis-клиент.

    Raises:
        RedisError: Если Redis недоступен.
    """
    client = Redis.from_url(
        redis_url,
        decode_responses=decode_responses,
        socket_connect_timeout=socket_connect_timeout,
        socket_timeout=socket_timeout,
    )
    # Health check — убеждаемся, что Redis отвечает
    await client.ping()
    logger.info("Redis подключён: %s", _mask_url(redis_url))
    return client


async def close_redis_client(client: Redis | None) -> None:
    """Безопасно закрыть Redis-клиент.

    Args:
        client: Redis-клиент или None (noop).
    """
    if client is None:
        return
    try:
        await client.aclose()
        logger.info("Redis соединение закрыто.")
    except RedisError as e:
        logger.warning("Ошибка при закрытии Redis: %s", e)


async def check_redis_health(client: Redis) -> bool:
    """Проверить доступность Redis.

    Returns:
        True если Redis отвечает на PING, False иначе.
    """
    try:
        return await client.ping()
    except RedisError:
        return False


def _mask_url(url: str) -> str:
    """Замаскировать пароль в URL для логирования.

    redis://:secret@host:6379/0 → redis://:***@host:6379/0
    """
    if "@" in url:
        prefix, suffix = url.rsplit("@", 1)
        # Находим пароль между :// и @
        if "://" in prefix:
            scheme_end = prefix.index("://") + 3
            return f"{prefix[:scheme_end]}:***@{suffix}"
    return url
