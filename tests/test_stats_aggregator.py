"""Тесты StatsAggregator (фоновая агрегация user_events → daily_stats).

Тестируем:
- ensure_schema делегирует MigrationRunner
- aggregate_day выполняет SQL
- run_aggregation вызывает aggregate_day для вчера и сегодня
- cleanup_old_events удаляет старые события
- get_summary возвращает агрегированные данные
- get_funnel возвращает воронку
- start / stop управляют фоновой задачей
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vkuswill_bot.services.stats_aggregator import (
    AGGREGATION_INTERVAL,
    CLEANUP_INTERVAL,
    EVENTS_TTL_MONTHS,
    StatsAggregator,
)


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


def _make_pool() -> tuple[MagicMock, AsyncMock]:
    """Создать мок asyncpg.Pool с контекстным менеджером acquire."""
    pool = MagicMock()
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


@pytest.fixture
def pool_and_conn():
    return _make_pool()


@pytest.fixture
def aggregator(pool_and_conn) -> StatsAggregator:
    pool, _ = pool_and_conn
    return StatsAggregator(pool)


# ---------------------------------------------------------------------------
# Тесты: ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    """Тесты инициализации схемы."""

    @pytest.mark.asyncio
    async def test_delegates_to_migration_runner(self, pool_and_conn):
        """ensure_schema делегирует MigrationRunner.run()."""
        pool, conn = pool_and_conn
        agg = StatsAggregator(pool)

        with patch(
            "vkuswill_bot.services.migration_runner.MigrationRunner"
        ) as MockRunner:
            mock_runner = AsyncMock()
            MockRunner.return_value = mock_runner

            await agg.ensure_schema()

            MockRunner.assert_called_once_with(pool)
            mock_runner.run.assert_awaited_once()


# ---------------------------------------------------------------------------
# Тесты: aggregate_day
# ---------------------------------------------------------------------------


class TestAggregateDay:
    """Тесты агрегации одного дня."""

    @pytest.mark.asyncio
    async def test_executes_aggregate_sql(self, pool_and_conn):
        """aggregate_day выполняет INSERT INTO daily_stats."""
        pool, conn = pool_and_conn
        agg = StatsAggregator(pool)
        today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

        await agg.aggregate_day(today)

        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "daily_stats" in sql
        assert "ON CONFLICT" in sql

    @pytest.mark.asyncio
    async def test_passes_date_parameter(self, pool_and_conn):
        """aggregate_day передаёт дату как параметр запроса."""
        pool, conn = pool_and_conn
        agg = StatsAggregator(pool)
        target_date = datetime(2026, 1, 15, tzinfo=UTC)

        await agg.aggregate_day(target_date)

        args = conn.execute.call_args[0]
        assert args[1] == target_date


# ---------------------------------------------------------------------------
# Тесты: run_aggregation
# ---------------------------------------------------------------------------


class TestRunAggregation:
    """Тесты полной агрегации (вчера + сегодня)."""

    @pytest.mark.asyncio
    async def test_aggregates_yesterday_and_today(self, pool_and_conn):
        """run_aggregation вызывает aggregate_day для вчера и сегодня."""
        pool, conn = pool_and_conn
        agg = StatsAggregator(pool)

        await agg.run_aggregation()

        # Должно быть 2 вызова execute (вчера + сегодня)
        assert conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_dates_are_correct(self, pool_and_conn):
        """run_aggregation передаёт правильные даты."""
        pool, conn = pool_and_conn
        agg = StatsAggregator(pool)

        await agg.run_aggregation()

        calls = conn.execute.call_args_list
        date1 = calls[0][0][1]  # вчера
        date2 = calls[1][0][1]  # сегодня
        assert (date2 - date1).days == 1


# ---------------------------------------------------------------------------
# Тесты: cleanup_old_events
# ---------------------------------------------------------------------------


class TestCleanupOldEvents:
    """Тесты очистки старых событий."""

    @pytest.mark.asyncio
    async def test_executes_delete_query(self, pool_and_conn):
        """cleanup_old_events выполняет DELETE."""
        pool, conn = pool_and_conn
        conn.execute.return_value = "DELETE 42"
        agg = StatsAggregator(pool)

        count = await agg.cleanup_old_events()

        assert count == 42
        conn.execute.assert_called_once()
        sql = conn.execute.call_args[0][0]
        assert "DELETE FROM user_events" in sql

    @pytest.mark.asyncio
    async def test_returns_zero_for_no_deletions(self, pool_and_conn):
        """cleanup_old_events возвращает 0 если ничего не удалено."""
        pool, conn = pool_and_conn
        conn.execute.return_value = "DELETE 0"
        agg = StatsAggregator(pool)

        count = await agg.cleanup_old_events()

        assert count == 0

    @pytest.mark.asyncio
    async def test_passes_ttl_parameter(self, pool_and_conn):
        """cleanup_old_events передаёт EVENTS_TTL_MONTHS в запрос."""
        pool, conn = pool_and_conn
        conn.execute.return_value = "DELETE 0"
        agg = StatsAggregator(pool)

        await agg.cleanup_old_events()

        args = conn.execute.call_args[0]
        assert args[1] == EVENTS_TTL_MONTHS


# ---------------------------------------------------------------------------
# Тесты: get_summary
# ---------------------------------------------------------------------------


class TestGetSummary:
    """Тесты получения агрегированной статистики."""

    @pytest.mark.asyncio
    async def test_returns_dict(self, pool_and_conn):
        """get_summary возвращает словарь с данными."""
        pool, conn = pool_and_conn
        row = {
            "total_dau": 100,
            "avg_dau": 14.3,
            "total_new_users": 50,
            "total_sessions": 200,
            "total_carts": 30,
            "total_gmv": 45000,
            "avg_cart_value": 1500,
            "total_searches": 150,
            "total_errors": 5,
            "total_limits": 10,
            "total_surveys": 3,
            "period_start": datetime(2026, 2, 6, tzinfo=UTC).date(),
            "period_end": datetime(2026, 2, 12, tzinfo=UTC).date(),
        }
        conn.fetchrow.return_value = row
        agg = StatsAggregator(pool)

        result = await agg.get_summary(7)

        assert result["avg_dau"] == 14.3
        assert result["total_carts"] == 30
        assert result["total_gmv"] == 45000

    @pytest.mark.asyncio
    async def test_passes_days_parameter(self, pool_and_conn):
        """get_summary передаёт количество дней в SQL."""
        pool, conn = pool_and_conn
        conn.fetchrow.return_value = {"total_dau": 0}
        agg = StatsAggregator(pool)

        await agg.get_summary(30)

        args = conn.fetchrow.call_args[0]
        assert args[1] == 30

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_none(self, pool_and_conn):
        """get_summary возвращает {} если fetchrow вернул None."""
        pool, conn = pool_and_conn
        conn.fetchrow.return_value = None
        agg = StatsAggregator(pool)

        result = await agg.get_summary(7)

        assert result == {}


# ---------------------------------------------------------------------------
# Тесты: get_funnel
# ---------------------------------------------------------------------------


class TestGetFunnel:
    """Тесты получения воронки."""

    @pytest.mark.asyncio
    async def test_returns_funnel_data(self, pool_and_conn):
        """get_funnel возвращает словарь с шагами воронки."""
        pool, conn = pool_and_conn
        row = {
            "started": 100,
            "active": 80,
            "searched": 60,
            "carted": 20,
            "hit_limit": 5,
            "surveyed": 3,
        }
        conn.fetchrow.return_value = row
        agg = StatsAggregator(pool)

        result = await agg.get_funnel(7)

        assert result["started"] == 100
        assert result["carted"] == 20
        assert result["surveyed"] == 3

    @pytest.mark.asyncio
    async def test_passes_days_parameter(self, pool_and_conn):
        """get_funnel передаёт количество дней в SQL."""
        pool, conn = pool_and_conn
        conn.fetchrow.return_value = {"started": 0}
        agg = StatsAggregator(pool)

        await agg.get_funnel(14)

        args = conn.fetchrow.call_args[0]
        assert args[1] == 14

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_none(self, pool_and_conn):
        """get_funnel возвращает {} если fetchrow вернул None."""
        pool, conn = pool_and_conn
        conn.fetchrow.return_value = None
        agg = StatsAggregator(pool)

        result = await agg.get_funnel(7)

        assert result == {}


# ---------------------------------------------------------------------------
# Тесты: start / stop
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Тесты запуска и остановки фоновой задачи."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self, pool_and_conn):
        """start() создаёт asyncio задачу."""
        pool, conn = pool_and_conn
        agg = StatsAggregator(pool)

        assert agg._task is None

        # Мокаем _loop чтобы задача не запускала реальный цикл
        with patch.object(agg, "_loop", new_callable=AsyncMock) as mock_loop:
            agg.start()
            assert agg._task is not None
            # Даём задаче шанс стартовать
            await asyncio.sleep(0.01)

        # Очищаем задачу
        if agg._task and not agg._task.done():
            agg._task.cancel()
            try:
                await agg._task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, pool_and_conn):
        """stop() отменяет задачу."""
        pool, conn = pool_and_conn
        agg = StatsAggregator(pool)

        # Создаём задачу, которая просто ждёт отмены
        async def fake_loop():
            await asyncio.sleep(9999)

        agg._task = asyncio.create_task(fake_loop())

        await agg.stop()

        assert agg._task.done()

    @pytest.mark.asyncio
    async def test_stop_no_task_safe(self, pool_and_conn):
        """stop() не падает если задача не запущена."""
        pool, conn = pool_and_conn
        agg = StatsAggregator(pool)

        await agg.stop()  # Не должно упасть

    @pytest.mark.asyncio
    async def test_start_idempotent(self, pool_and_conn):
        """Повторный start() не создаёт вторую задачу."""
        pool, conn = pool_and_conn
        agg = StatsAggregator(pool)

        with patch.object(agg, "_loop", new_callable=AsyncMock):
            agg.start()
            first_task = agg._task
            agg.start()
            second_task = agg._task

        assert first_task is second_task

        # Очищаем
        if agg._task and not agg._task.done():
            agg._task.cancel()
            try:
                await agg._task
            except asyncio.CancelledError:
                pass


# ---------------------------------------------------------------------------
# Тесты: константы
# ---------------------------------------------------------------------------


class TestConstants:
    """Тесты константов модуля."""

    def test_aggregation_interval_one_hour(self):
        """AGGREGATION_INTERVAL = 3600 (1 час)."""
        assert AGGREGATION_INTERVAL == 3600

    def test_cleanup_interval_one_day(self):
        """CLEANUP_INTERVAL = 86400 (1 сутки)."""
        assert CLEANUP_INTERVAL == 86400

    def test_events_ttl_months(self):
        """EVENTS_TTL_MONTHS — разумное значение (1–36 мес.)."""
        assert 1 <= EVENTS_TTL_MONTHS <= 36
