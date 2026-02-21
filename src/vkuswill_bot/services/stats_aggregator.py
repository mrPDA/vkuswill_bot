"""Фоновая агрегация аналитики: user_events → daily_stats.

Запускается как asyncio-задача при старте бота.
Раз в час пересчитывает агрегаты за текущий и вчерашний день.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timedelta, UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# Интервал агрегации (секунды)
AGGREGATION_INTERVAL = 3600  # 1 час

# Интервал очистки старых событий (секунды) — раз в сутки
CLEANUP_INTERVAL = 86400

# TTL событий в user_events (месяцы)
EVENTS_TTL_MONTHS = 12

# SQL для агрегации одного дня
_AGGREGATE_DAY_SQL = """
INSERT INTO daily_stats (
    date, dau, new_users, sessions, carts_created,
    total_gmv, avg_cart_value, searches, errors,
    cart_limits_hit, surveys_completed,
    trial_carts, referral_links, referral_bonuses, feedback_bonuses,
    updated_at
)
SELECT
    $1::date AS date,
    -- DAU: уникальные пользователи с session_start
    COUNT(DISTINCT CASE WHEN event_type = 'session_start' THEN user_id END),
    -- Новые пользователи
    COUNT(CASE WHEN event_type = 'bot_start'
               AND (metadata->>'is_new_user')::boolean = true THEN 1 END),
    -- Сессии
    COUNT(CASE WHEN event_type = 'session_start' THEN 1 END),
    -- Корзины
    COUNT(CASE WHEN event_type = 'cart_created' THEN 1 END),
    -- GMV (сумма total_sum)
    COALESCE(SUM(CASE WHEN event_type = 'cart_created'
                      THEN (metadata->>'total_sum')::numeric END), 0),
    -- Средний чек
    COALESCE(AVG(CASE WHEN event_type = 'cart_created'
                      AND metadata->>'total_sum' IS NOT NULL
                      THEN (metadata->>'total_sum')::numeric END), 0),
    -- Поиски
    COUNT(CASE WHEN event_type = 'product_search' THEN 1 END),
    -- Ошибки
    COUNT(CASE WHEN event_type = 'bot_error' THEN 1 END),
    -- Лимиты корзин
    COUNT(CASE WHEN event_type = 'cart_limit_reached' THEN 1 END),
    -- Опросы
    COUNT(CASE WHEN event_type = 'survey_completed' THEN 1 END),
    -- Корзины в trial-периоде
    COUNT(
        CASE WHEN event_type = 'cart_created'
            AND metadata->>'trial_active' = 'true'
        THEN 1 END
    ),
    -- Привязки по рефералке
    COUNT(CASE WHEN event_type = 'referral_linked' THEN 1 END),
    -- Начисления бонусов рефереру
    COUNT(CASE WHEN event_type = 'referral_bonus_granted' THEN 1 END),
    -- Начисления бонусов за обратную связь
    COUNT(CASE WHEN event_type = 'feedback_bonus_granted' THEN 1 END),
    NOW()
FROM user_events
WHERE created_at >= $1::date
  AND created_at < ($1::date + INTERVAL '1 day')
ON CONFLICT (date) DO UPDATE SET
    dau               = EXCLUDED.dau,
    new_users         = EXCLUDED.new_users,
    sessions          = EXCLUDED.sessions,
    carts_created     = EXCLUDED.carts_created,
    total_gmv         = EXCLUDED.total_gmv,
    avg_cart_value    = EXCLUDED.avg_cart_value,
    searches          = EXCLUDED.searches,
    errors            = EXCLUDED.errors,
    cart_limits_hit   = EXCLUDED.cart_limits_hit,
    surveys_completed = EXCLUDED.surveys_completed,
    trial_carts       = EXCLUDED.trial_carts,
    referral_links    = EXCLUDED.referral_links,
    referral_bonuses  = EXCLUDED.referral_bonuses,
    feedback_bonuses  = EXCLUDED.feedback_bonuses,
    updated_at        = NOW();
"""


class StatsAggregator:
    """Фоновая агрегация user_events → daily_stats + очистка старых событий."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._task: asyncio.Task | None = None
        self._last_cleanup: float = 0.0

    async def ensure_schema(self) -> None:
        """Убедиться, что таблица daily_stats существует.

        Миграции применяются через MigrationRunner (из __main__.py).
        Этот метод — подстраховка для standalone-запуска.
        """
        from vkuswill_bot.services.migration_runner import MigrationRunner

        runner = MigrationRunner(self._pool)
        await runner.run()
        logger.info("StatsAggregator: схема актуальна (MigrationRunner)")

    async def aggregate_day(self, date: datetime) -> None:
        """Агрегировать события за указанный день."""
        async with self._pool.acquire() as conn:
            await conn.execute(_AGGREGATE_DAY_SQL, date)
        logger.debug("StatsAggregator: агрегирован день %s", date.date())

    async def run_aggregation(self) -> None:
        """Агрегировать сегодня и вчера (для корректировки опоздавших событий)."""
        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)

        await self.aggregate_day(yesterday)
        await self.aggregate_day(today)
        logger.info("StatsAggregator: агрегация завершена (вчера + сегодня)")

    async def cleanup_old_events(self) -> int:
        """Удалить события старше EVENTS_TTL_MONTHS месяцев.

        Returns:
            Количество удалённых записей.
        """
        sql = """
            DELETE FROM user_events
            WHERE created_at < NOW() - ($1::integer || ' months')::interval
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(sql, EVENTS_TTL_MONTHS)
        # result = "DELETE N"
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            logger.info(
                "StatsAggregator: удалено %d событий старше %d мес.",
                count,
                EVENTS_TTL_MONTHS,
            )
        return count

    async def _loop(self) -> None:
        """Основной цикл: агрегация каждый час, очистка раз в сутки."""
        await self.ensure_schema()
        # Первая агрегация сразу при старте
        try:
            await self.run_aggregation()
        except Exception as exc:
            logger.error("StatsAggregator: ошибка первой агрегации: %s", exc)

        self._last_cleanup = time.monotonic()

        while True:
            await asyncio.sleep(AGGREGATION_INTERVAL)
            try:
                await self.run_aggregation()
                # Очистка старых событий — раз в сутки
                if time.monotonic() - self._last_cleanup >= CLEANUP_INTERVAL:
                    await self.cleanup_old_events()
                    self._last_cleanup = time.monotonic()
            except asyncio.CancelledError:
                logger.info("StatsAggregator: задача отменена")
                break
            except Exception as exc:
                logger.error("StatsAggregator: ошибка агрегации: %s", exc)

    def start(self) -> None:
        """Запустить фоновую задачу."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())
            logger.info("StatsAggregator: фоновая задача запущена")

    async def stop(self) -> None:
        """Остановить фоновую задачу."""
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            logger.info("StatsAggregator: фоновая задача остановлена")

    # ------------------------------------------------------------------
    # Запросы для /admin_analytics
    # ------------------------------------------------------------------

    async def get_summary(self, days: int = 1) -> dict:
        """Получить агрегированную статистику за N дней.

        Returns:
            Словарь с суммами/средними за указанный период.
        """
        sql = """
            SELECT
                COALESCE(SUM(dau), 0)               AS total_dau,
                COALESCE(AVG(dau), 0)               AS avg_dau,
                COALESCE(SUM(new_users), 0)         AS total_new_users,
                COALESCE(SUM(sessions), 0)          AS total_sessions,
                COALESCE(SUM(carts_created), 0)     AS total_carts,
                COALESCE(SUM(total_gmv), 0)         AS total_gmv,
                COALESCE(AVG(avg_cart_value), 0)    AS avg_cart_value,
                COALESCE(SUM(searches), 0)          AS total_searches,
                COALESCE(SUM(errors), 0)            AS total_errors,
                COALESCE(SUM(cart_limits_hit), 0)   AS total_limits,
                COALESCE(SUM(surveys_completed), 0) AS total_surveys,
                COALESCE(SUM(trial_carts), 0)       AS total_trial_carts,
                COALESCE(SUM(referral_links), 0)    AS total_referral_links,
                COALESCE(SUM(referral_bonuses), 0)  AS total_referral_bonuses,
                COALESCE(SUM(feedback_bonuses), 0)  AS total_feedback_bonuses,
                MIN(date)                           AS period_start,
                MAX(date)                           AS period_end
            FROM daily_stats
            WHERE date >= (CURRENT_DATE - $1::integer + 1)
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, days)
        return dict(row) if row else {}

    async def get_funnel(self, days: int = 7) -> dict:
        """Получить воронку за N дней (из user_events)."""
        sql = """
            SELECT
                COUNT(DISTINCT CASE WHEN event_type = 'bot_start'
                    THEN user_id END) AS started,
                COUNT(DISTINCT CASE WHEN event_type = 'session_start'
                    THEN user_id END) AS active,
                COUNT(DISTINCT CASE WHEN event_type = 'product_search'
                    THEN user_id END) AS searched,
                COUNT(DISTINCT CASE WHEN event_type = 'cart_created'
                    THEN user_id END) AS carted,
                COUNT(DISTINCT CASE WHEN event_type = 'cart_limit_reached'
                    THEN user_id END) AS hit_limit,
                COUNT(DISTINCT CASE WHEN event_type = 'survey_completed'
                    THEN user_id END) AS surveyed
            FROM user_events
            WHERE created_at >= (CURRENT_DATE - $1::integer + 1)
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, days)
        return dict(row) if row else {}
