"""Тесты для UserStore (PostgreSQL)."""

from __future__ import annotations

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vkuswill_bot.services.user_store import (
    VALID_ROLES,
    VALID_STATUSES,
    UserStore,
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
    async def test_ensure_schema_delegates_to_migration_runner(self, pool_and_conn):
        """ensure_schema делегирует MigrationRunner.run()."""
        pool, _conn = pool_and_conn
        store = UserStore(pool)
        assert not store._schema_ready

        with patch("vkuswill_bot.services.migration_runner.MigrationRunner") as MockRunner:
            mock_runner = AsyncMock()
            MockRunner.return_value = mock_runner

            await store.ensure_schema()

            MockRunner.assert_called_once_with(pool)
            mock_runner.run.assert_awaited_once()
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
# Тесты: Freemium — лимиты корзин
# ---------------------------------------------------------------------------


class TestFreemiumCartLimits:
    """Тесты freemium-методов: лимиты корзин."""

    @pytest.mark.asyncio
    async def test_check_cart_limit_allowed(self, store):
        """check_cart_limit возвращает allowed=True когда лимит не исчерпан."""
        s, conn = store
        conn.fetchrow.return_value = {
            "carts_created": 2,
            "cart_limit": 5,
            "survey_completed": False,
        }

        result = await s.check_cart_limit(123)

        assert result["allowed"] is True
        assert result["carts_created"] == 2
        assert result["cart_limit"] == 5
        assert result["survey_completed"] is False

    @pytest.mark.asyncio
    async def test_check_cart_limit_denied(self, store):
        """check_cart_limit возвращает allowed=False когда лимит исчерпан."""
        s, conn = store
        conn.fetchrow.return_value = {
            "carts_created": 5,
            "cart_limit": 5,
            "survey_completed": False,
        }

        result = await s.check_cart_limit(123)

        assert result["allowed"] is False
        assert result["carts_created"] == 5

    @pytest.mark.asyncio
    async def test_check_cart_limit_denied_survey_done(self, store):
        """check_cart_limit возвращает survey_completed=True для tier 2."""
        s, conn = store
        conn.fetchrow.return_value = {
            "carts_created": 10,
            "cart_limit": 10,
            "survey_completed": True,
        }

        result = await s.check_cart_limit(123)

        assert result["allowed"] is False
        assert result["survey_completed"] is True

    @pytest.mark.asyncio
    async def test_check_cart_limit_nonexistent_user(self, store):
        """check_cart_limit возвращает allowed=True для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.check_cart_limit(999)

        assert result["allowed"] is True
        assert result["carts_created"] == 0
        assert result["survey_completed"] is False

    @pytest.mark.asyncio
    async def test_check_cart_limit_custom_default(self, store):
        """check_cart_limit принимает кастомный default_limit."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.check_cart_limit(999, default_limit=10)

        assert result["cart_limit"] == 10

    @pytest.mark.asyncio
    async def test_increment_carts(self, store):
        """increment_carts увеличивает счётчик на 1."""
        s, conn = store
        conn.fetchrow.return_value = {
            "carts_created": 3,
            "cart_limit": 5,
            "survey_completed": False,
        }

        result = await s.increment_carts(123)

        assert result["carts_created"] == 3
        sql = conn.fetchrow.call_args[0][0]
        assert "carts_created = carts_created + 1" in sql

    @pytest.mark.asyncio
    async def test_increment_carts_nonexistent(self, store):
        """increment_carts возвращает пустой dict для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.increment_carts(999)

        assert result == {}

    @pytest.mark.asyncio
    async def test_grant_bonus_carts(self, store):
        """grant_bonus_carts увеличивает лимит корзин."""
        s, conn = store
        conn.fetchrow.return_value = {"cart_limit": 10}

        new_limit = await s.grant_bonus_carts(123, 5)

        assert new_limit == 10
        sql = conn.fetchrow.call_args[0][0]
        assert "cart_limit = cart_limit + $2" in sql

    @pytest.mark.asyncio
    async def test_grant_bonus_carts_nonexistent(self, store):
        """grant_bonus_carts возвращает 0 для несуществующего пользователя."""
        s, conn = store
        conn.fetchrow.return_value = None

        new_limit = await s.grant_bonus_carts(999, 5)

        assert new_limit == 0

    @pytest.mark.asyncio
    async def test_grant_bonus_carts_default_amount(self, store):
        """grant_bonus_carts по умолчанию добавляет 5 корзин."""
        s, conn = store
        conn.fetchrow.return_value = {"cart_limit": 10}

        await s.grant_bonus_carts(123)

        args = conn.fetchrow.call_args[0]
        assert args[2] == 5  # amount = 5


# ---------------------------------------------------------------------------
# Тесты: Freemium — survey
# ---------------------------------------------------------------------------


class TestFreemiumSurvey:
    """Тесты survey-методов."""

    @pytest.mark.asyncio
    async def test_mark_survey_completed(self, store):
        """mark_survey_completed выполняет UPDATE survey_completed = TRUE."""
        s, conn = store

        await s.mark_survey_completed(123)

        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "survey_completed = TRUE" in sql

    @pytest.mark.asyncio
    async def test_mark_survey_completed_if_not_first_time(self, store):
        """mark_survey_completed_if_not возвращает True при первом прохождении."""
        s, conn = store
        conn.fetchrow.return_value = {"user_id": 123}

        result = await s.mark_survey_completed_if_not(123)

        assert result is True
        sql = conn.fetchrow.call_args[0][0]
        assert "survey_completed = FALSE" in sql

    @pytest.mark.asyncio
    async def test_mark_survey_completed_if_not_already_done(self, store):
        """mark_survey_completed_if_not возвращает False если уже пройден."""
        s, conn = store
        conn.fetchrow.return_value = None

        result = await s.mark_survey_completed_if_not(123)

        assert result is False

    @pytest.mark.asyncio
    async def test_get_survey_stats(self, store):
        """get_survey_stats возвращает агрегированную статистику."""
        s, conn = store
        conn.fetchval.side_effect = [42, 4.2]  # total, avg_nps
        conn.fetch.side_effect = [
            [{"answer": "yes", "cnt": 30}, {"answer": "maybe", "cnt": 10}],
            [{"feat": "search", "cnt": 25}, {"feat": "recipe", "cnt": 15}],
        ]

        result = await s.get_survey_stats()

        assert result["total"] == 42
        assert result["avg_nps"] == 4.2
        assert len(result["will_continue"]) == 2
        assert len(result["features"]) == 2

    @pytest.mark.asyncio
    async def test_get_survey_stats_empty(self, store):
        """get_survey_stats возвращает нули при отсутствии данных."""
        s, conn = store
        conn.fetchval.side_effect = [0, None]
        conn.fetch.side_effect = [[], []]

        result = await s.get_survey_stats()

        assert result["total"] == 0
        assert result["avg_nps"] == 0.0
        assert result["will_continue"] == []
        assert result["features"] == []


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

    def test_migration_files_exist(self):
        """Файлы SQL-миграций существуют."""
        from vkuswill_bot.services.migration_runner import MIGRATIONS_DIR

        sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        assert len(sql_files) >= 1, "Нет SQL-миграций в migrations/"
        assert any("001" in f.name for f in sql_files)


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
