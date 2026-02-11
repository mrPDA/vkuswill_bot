"""Тесты для UserStore (PostgreSQL)."""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock

import pytest

from vkuswill_bot.services.user_store import (
    VALID_ROLES,
    VALID_STATUSES,
    UserStore,
    _MIGRATION_PATH,
)


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


def _make_pool() -> MagicMock:
    """Создать мок asyncpg.Pool с контекстным менеджером acquire."""
    pool = MagicMock()
    conn = AsyncMock()
    # pool.acquire() → async context manager → conn
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    pool.close = AsyncMock()
    return pool, conn


@pytest.fixture
def pool_and_conn():
    """Мок-пул и мок-соединение."""
    return _make_pool()


@pytest.fixture
def store(pool_and_conn):
    """UserStore с мок-пулом (schema_ready=True)."""
    pool, conn = pool_and_conn
    s = UserStore(pool)
    s._schema_ready = True  # Пропускаем ensure_schema
    return s, conn


# ---------------------------------------------------------------------------
# Тесты: ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    """Тесты инициализации схемы."""

    @pytest.mark.asyncio
    async def test_ensure_schema_runs_migration(self, pool_and_conn):
        """ensure_schema выполняет SQL из файла миграции."""
        pool, conn = pool_and_conn
        store = UserStore(pool)
        assert not store._schema_ready

        await store.ensure_schema()

        conn.execute.assert_called_once()
        sql_arg = conn.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS users" in sql_arg
        assert store._schema_ready

    @pytest.mark.asyncio
    async def test_ensure_schema_idempotent(self, pool_and_conn):
        """Повторный вызов ensure_schema не выполняет SQL."""
        pool, conn = pool_and_conn
        store = UserStore(pool)
        store._schema_ready = True

        await store.ensure_schema()

        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Тесты: get_or_create
# ---------------------------------------------------------------------------


class TestGetOrCreate:
    """Тесты upsert пользователя."""

    @pytest.mark.asyncio
    async def test_creates_new_user(self, store):
        """Создаёт нового пользователя при первом обращении."""
        s, conn = store
        # asyncpg.Record ведёт себя как dict при вызове dict(row)
        row = {
            "user_id": 123,
            "username": "testuser",
            "first_name": "Test",
            "role": "user",
            "status": "active",
        }
        conn.fetchrow.return_value = row

        result = await s.get_or_create(
            user_id=123,
            username="testuser",
            first_name="Test",
        )

        conn.fetchrow.assert_called_once()
        sql = conn.fetchrow.call_args[0][0]
        assert "INSERT INTO users" in sql
        assert "ON CONFLICT" in sql
        assert result["user_id"] == 123

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_none(self, store):
        """Возвращает пустой словарь если fetchrow вернул None."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.get_or_create(user_id=999)

        assert result == {}


# ---------------------------------------------------------------------------
# Тесты: get
# ---------------------------------------------------------------------------


class TestGet:
    """Тесты получения пользователя."""

    @pytest.mark.asyncio
    async def test_get_existing_user(self, store):
        """Возвращает пользователя если он существует."""
        s, conn = store
        row = {"user_id": 123, "username": "test", "role": "user"}
        conn.fetchrow.return_value = row

        result = await s.get(123)

        assert result == row

    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, store):
        """Возвращает None если пользователь не найден."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.get(999)

        assert result is None


# ---------------------------------------------------------------------------
# Тесты: блокировка
# ---------------------------------------------------------------------------


class TestBlockUnblock:
    """Тесты блокировки/разблокировки."""

    @pytest.mark.asyncio
    async def test_is_blocked_true(self, store):
        """is_blocked возвращает True для заблокированного пользователя."""
        s, conn = store
        conn.fetchrow.return_value = {"status": "blocked"}

        assert await s.is_blocked(123) is True

    @pytest.mark.asyncio
    async def test_is_blocked_false(self, store):
        """is_blocked возвращает False для активного пользователя."""
        s, conn = store
        conn.fetchrow.return_value = {"status": "active"}

        assert await s.is_blocked(123) is False

    @pytest.mark.asyncio
    async def test_is_blocked_nonexistent(self, store):
        """is_blocked возвращает False для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        assert await s.is_blocked(999) is False

    @pytest.mark.asyncio
    async def test_block_success(self, store):
        """block возвращает True при успешной блокировке."""
        s, conn = store
        conn.fetchrow.return_value = {"user_id": 123}

        result = await s.block(123, "спам")

        assert result is True
        sql = conn.fetchrow.call_args[0][0]
        assert "status = 'blocked'" in sql

    @pytest.mark.asyncio
    async def test_block_nonexistent(self, store):
        """block возвращает False для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.block(999, "test")

        assert result is False

    @pytest.mark.asyncio
    async def test_unblock_success(self, store):
        """unblock возвращает True при успешной разблокировке."""
        s, conn = store
        conn.fetchrow.return_value = {"user_id": 123}

        result = await s.unblock(123)

        assert result is True
        sql = conn.fetchrow.call_args[0][0]
        assert "status = 'active'" in sql

    @pytest.mark.asyncio
    async def test_unblock_nonexistent(self, store):
        """unblock возвращает False для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.unblock(999)

        assert result is False


# ---------------------------------------------------------------------------
# Тесты: роли
# ---------------------------------------------------------------------------


class TestRoles:
    """Тесты управления ролями."""

    @pytest.mark.asyncio
    async def test_is_admin_true(self, store):
        """is_admin возвращает True для администратора."""
        s, conn = store
        conn.fetchrow.return_value = {"role": "admin"}

        assert await s.is_admin(123) is True

    @pytest.mark.asyncio
    async def test_is_admin_false(self, store):
        """is_admin возвращает False для обычного пользователя."""
        s, conn = store
        conn.fetchrow.return_value = {"role": "user"}

        assert await s.is_admin(123) is False

    @pytest.mark.asyncio
    async def test_is_admin_nonexistent(self, store):
        """is_admin возвращает False для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        assert await s.is_admin(999) is False

    @pytest.mark.asyncio
    async def test_set_role_valid(self, store):
        """set_role обновляет роль при допустимом значении."""
        s, conn = store
        conn.fetchrow.return_value = {"user_id": 123}

        result = await s.set_role(123, "admin")

        assert result is True
        sql = conn.fetchrow.call_args[0][0]
        assert "role = $2" in sql

    @pytest.mark.asyncio
    async def test_set_role_invalid(self, store):
        """set_role поднимает ValueError для недопустимой роли."""
        s, _ = store

        with pytest.raises(ValueError, match="Недопустимая роль"):
            await s.set_role(123, "superadmin")

    @pytest.mark.asyncio
    async def test_set_role_nonexistent(self, store):
        """set_role возвращает False для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.set_role(999, "admin")

        assert result is False


# ---------------------------------------------------------------------------
# Тесты: лимиты
# ---------------------------------------------------------------------------


class TestLimits:
    """Тесты персональных лимитов."""

    @pytest.mark.asyncio
    async def test_get_limits_with_overrides(self, store):
        """get_limits возвращает персональные лимиты."""
        s, conn = store
        conn.fetchrow.return_value = {"rate_limit": 10, "rate_period": 30.0}

        result = await s.get_limits(123)

        assert result == {"rate_limit": 10, "rate_period": 30.0}

    @pytest.mark.asyncio
    async def test_get_limits_default(self, store):
        """get_limits возвращает None если лимиты не заданы."""
        s, conn = store
        conn.fetchrow.return_value = {"rate_limit": None, "rate_period": None}

        result = await s.get_limits(123)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_limits_nonexistent(self, store):
        """get_limits возвращает None для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.get_limits(999)

        assert result is None

    @pytest.mark.asyncio
    async def test_set_limits(self, store):
        """set_limits обновляет персональные лимиты."""
        s, conn = store
        conn.fetchrow.return_value = {"user_id": 123}

        result = await s.set_limits(123, rate_limit=20, rate_period=120.0)

        assert result is True

    @pytest.mark.asyncio
    async def test_set_limits_reset(self, store):
        """set_limits с None сбрасывает лимиты к дефолтным."""
        s, conn = store
        conn.fetchrow.return_value = {"user_id": 123}

        result = await s.set_limits(123, rate_limit=None, rate_period=None)

        assert result is True


# ---------------------------------------------------------------------------
# Тесты: статистика
# ---------------------------------------------------------------------------


class TestStatistics:
    """Тесты статистики и событий."""

    @pytest.mark.asyncio
    async def test_increment_message_count(self, store):
        """increment_message_count выполняет UPDATE."""
        s, conn = store

        await s.increment_message_count(123)

        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "message_count = message_count + 1" in sql

    @pytest.mark.asyncio
    async def test_log_event(self, store):
        """log_event вставляет событие в user_events."""
        s, conn = store

        await s.log_event(123, "search", {"query": "молоко"})

        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "INSERT INTO user_events" in sql

    @pytest.mark.asyncio
    async def test_log_event_no_metadata(self, store):
        """log_event работает без метаданных."""
        s, conn = store

        await s.log_event(123, "command")

        conn.execute.assert_called_once()
        # metadata передаётся как None
        args = conn.execute.call_args[0]
        assert args[3] is None  # $3 = metadata

    @pytest.mark.asyncio
    async def test_get_stats_found(self, store):
        """get_stats возвращает статистику пользователя."""
        s, conn = store
        now = datetime.now(UTC)
        conn.fetchrow.return_value = {
            "message_count": 42,
            "created_at": now,
            "last_message_at": now,
        }
        conn.fetch.return_value = [
            {"event_type": "search", "cnt": 30},
            {"event_type": "command", "cnt": 12},
        ]

        result = await s.get_stats(123)

        assert result is not None
        assert result["message_count"] == 42
        assert result["events"]["search"] == 30

    @pytest.mark.asyncio
    async def test_get_stats_not_found(self, store):
        """get_stats возвращает None для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.get_stats(999)

        assert result is None


# ---------------------------------------------------------------------------
# Тесты: админские запросы
# ---------------------------------------------------------------------------


class TestAdminQueries:
    """Тесты админских запросов."""

    @pytest.mark.asyncio
    async def test_list_users(self, store):
        """list_users возвращает список пользователей."""
        s, conn = store
        conn.fetch.return_value = [
            {"user_id": 1, "username": "a"},
            {"user_id": 2, "username": "b"},
        ]

        result = await s.list_users(limit=10, offset=0)

        assert len(result) == 2
        conn.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_count_users(self, store):
        """count_users возвращает общее количество."""
        s, conn = store
        conn.fetchrow.return_value = {"cnt": 42}

        result = await s.count_users()

        assert result == 42

    @pytest.mark.asyncio
    async def test_count_active_today(self, store):
        """count_active_today возвращает DAU."""
        s, conn = store
        conn.fetchrow.return_value = {"cnt": 10}

        result = await s.count_active_today()

        assert result == 10

    @pytest.mark.asyncio
    async def test_ensure_admins(self, store):
        """ensure_admins создаёт/обновляет записи для админов."""
        s, conn = store

        await s.ensure_admins([111, 222])

        assert conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_ensure_admins_empty(self, store):
        """ensure_admins ничего не делает при пустом списке."""
        s, conn = store

        await s.ensure_admins([])

        conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# Тесты: константы
# ---------------------------------------------------------------------------


class TestConstants:
    """Тесты валидных значений и констант."""

    def test_valid_roles(self):
        """VALID_ROLES содержит user и admin."""
        assert {"user", "admin"} == VALID_ROLES

    def test_valid_statuses(self):
        """VALID_STATUSES содержит active, blocked, limited."""
        assert {"active", "blocked", "limited"} == VALID_STATUSES

    def test_migration_file_exists(self):
        """Файл миграции существует."""
        assert _MIGRATION_PATH.exists()


# ---------------------------------------------------------------------------
# Тесты: lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Тесты жизненного цикла."""

    @pytest.mark.asyncio
    async def test_close(self, store):
        """close вызывается без ошибок."""
        s, _ = store
        await s.close()  # Не падает
