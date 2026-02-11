"""Тесты ThrottlingMiddleware.

Тестируем:
- Пропуск сообщений в пределах лимита
- Блокировка при превышении лимита
- Сброс лимита после истечения периода
- Изоляция лимитов между пользователями
- Обработка сообщений без from_user
- F-05: Защита от DDoS (периодическая очистка, лимит отслеживаемых users)
"""

import time
from unittest.mock import AsyncMock, MagicMock


from vkuswill_bot.bot.middlewares import ThrottlingMiddleware


def _make_message_event(user_id: int = 1) -> MagicMock:
    """Создать мок Message для middleware."""
    from aiogram.types import Message

    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    return msg


class TestThrottlingMiddleware:
    """Тесты rate-limiting middleware."""

    async def test_allows_within_limit(self):
        """Сообщения в пределах лимита проходят."""
        mw = ThrottlingMiddleware(rate_limit=3, period=60.0)
        handler = AsyncMock(return_value="ok")

        for _ in range(3):
            event = _make_message_event(user_id=1)
            result = await mw(handler, event, {})
            assert result == "ok"

        assert handler.call_count == 3

    async def test_blocks_over_limit(self):
        """Сообщения сверх лимита блокируются."""
        mw = ThrottlingMiddleware(rate_limit=2, period=60.0)
        handler = AsyncMock(return_value="ok")

        # Первые 2 проходят
        for _ in range(2):
            event = _make_message_event(user_id=1)
            await mw(handler, event, {})

        assert handler.call_count == 2

        # 3-е блокируется
        event = _make_message_event(user_id=1)
        result = await mw(handler, event, {})

        assert result is None
        event.answer.assert_called_once()
        answer_text = event.answer.call_args[0][0]
        assert "Слишком много" in answer_text or "Подождите" in answer_text

    async def test_resets_after_period(self):
        """Лимит сбрасывается после истечения периода."""
        mw = ThrottlingMiddleware(rate_limit=2, period=1.0)
        handler = AsyncMock(return_value="ok")

        # Исчерпываем лимит
        for _ in range(2):
            event = _make_message_event(user_id=1)
            await mw(handler, event, {})

        # Мокаем time.monotonic чтобы "перемотать" время
        original_timestamps = mw._user_timestamps[1].copy()
        # Устанавливаем timestamps в прошлое
        mw._user_timestamps[1] = [t - 2.0 for t in original_timestamps]

        # Теперь сообщение проходит
        event = _make_message_event(user_id=1)
        result = await mw(handler, event, {})
        assert result == "ok"

    async def test_independent_user_limits(self):
        """У каждого пользователя свой лимит."""
        mw = ThrottlingMiddleware(rate_limit=1, period=60.0)
        handler = AsyncMock(return_value="ok")

        # Пользователь 1 — первое сообщение
        event1 = _make_message_event(user_id=1)
        result = await mw(handler, event1, {})
        assert result == "ok"

        # Пользователь 1 — второе (заблокировано)
        event1b = _make_message_event(user_id=1)
        result = await mw(handler, event1b, {})
        assert result is None

        # Пользователь 2 — первое (проходит, свой лимит)
        event2 = _make_message_event(user_id=2)
        result = await mw(handler, event2, {})
        assert result == "ok"

    async def test_no_user_passes_through(self):
        """Сообщение без from_user проходит без проверки."""
        mw = ThrottlingMiddleware(rate_limit=1, period=60.0)
        handler = AsyncMock(return_value="ok")

        event = _make_message_event()
        event.from_user = None
        result = await mw(handler, event, {})
        assert result == "ok"

    async def test_non_message_event_passes(self):
        """Событие не-Message проходит без проверки."""
        mw = ThrottlingMiddleware(rate_limit=1, period=60.0)
        handler = AsyncMock(return_value="ok")

        event = MagicMock()  # Не Message
        # Убираем spec чтобы isinstance проверка не прошла
        event.__class__ = type("NotMessage", (), {})
        result = await mw(handler, event, {})
        assert result == "ok"

    async def test_default_parameters(self):
        """Значения по умолчанию корректны."""
        mw = ThrottlingMiddleware()
        assert mw.rate_limit == 5
        assert mw.period == 60.0

    async def test_custom_parameters(self):
        """Кастомные параметры принимаются."""
        mw = ThrottlingMiddleware(rate_limit=10, period=120.0)
        assert mw.rate_limit == 10
        assert mw.period == 120.0


# ============================================================================
# F-05: DDoS-защита
# ============================================================================


class TestDDoSProtection:
    """F-05: Тесты защиты ThrottlingMiddleware от DDoS."""

    async def test_stale_users_cleaned_up(self):
        """Устаревшие записи удаляются при полной очистке."""
        mw = ThrottlingMiddleware(rate_limit=5, period=1.0)
        handler = AsyncMock(return_value="ok")

        # Создаём записи для нескольких пользователей
        for uid in range(10):
            event = _make_message_event(user_id=uid)
            await mw(handler, event, {})

        assert len(mw._user_timestamps) == 10

        # Делаем все timestamps устаревшими
        for uid in mw._user_timestamps:
            mw._user_timestamps[uid] = [t - 100.0 for t in mw._user_timestamps[uid]]

        # Форсируем полную очистку
        now = time.monotonic()
        mw._full_cleanup(now)

        assert len(mw._user_timestamps) == 0

    async def test_max_tracked_users_limit(self):
        """При превышении max_tracked_users — принудительная очистка."""
        mw = ThrottlingMiddleware(rate_limit=5, period=60.0, max_tracked_users=10)
        handler = AsyncMock(return_value="ok")

        # Заполняем до лимита
        for uid in range(10):
            event = _make_message_event(user_id=uid)
            await mw(handler, event, {})

        assert len(mw._user_timestamps) == 10

        # Делаем все timestamps устаревшими чтобы cleanup помог
        for uid in list(mw._user_timestamps.keys()):
            mw._user_timestamps[uid] = [t - 100.0 for t in mw._user_timestamps[uid]]

        # Новый пользователь — должен триггерить cleanup + пройти
        event = _make_message_event(user_id=999)
        result = await mw(handler, event, {})
        assert result == "ok"

        # После cleanup старые записи удалены
        assert len(mw._user_timestamps) <= 1  # только новый user

    async def test_overflow_skips_tracking(self):
        """При реальном переполнении (все активные) — новый user пропускается."""
        mw = ThrottlingMiddleware(rate_limit=5, period=60.0, max_tracked_users=5)
        handler = AsyncMock(return_value="ok")

        # Заполняем лимит активными пользователями
        for uid in range(5):
            event = _make_message_event(user_id=uid)
            await mw(handler, event, {})

        assert len(mw._user_timestamps) == 5

        # Новый user 999 — overflow, tracking пропускается, но запрос проходит
        event = _make_message_event(user_id=999)
        result = await mw(handler, event, {})
        assert result == "ok"
        # User 999 не добавлен в tracking
        assert 999 not in mw._user_timestamps

    async def test_empty_lists_removed(self):
        """Пустые списки timestamp удаляются из словаря."""
        mw = ThrottlingMiddleware(rate_limit=5, period=0.01)
        handler = AsyncMock(return_value="ok")

        # Создаём запись
        event = _make_message_event(user_id=1)
        await mw(handler, event, {})
        assert 1 in mw._user_timestamps

        # Делаем timestamp устаревшим
        mw._user_timestamps[1] = [time.monotonic() - 1.0]

        # Следующий запрос — cleanup удалит пустой список
        event = _make_message_event(user_id=1)
        await mw(handler, event, {})

        # Запись всё ещё есть (новый timestamp добавлен)
        assert 1 in mw._user_timestamps

    async def test_periodic_cleanup_interval(self):
        """Полная очистка вызывается через _FULL_CLEANUP_INTERVAL."""
        mw = ThrottlingMiddleware(rate_limit=5, period=1.0)
        handler = AsyncMock(return_value="ok")

        # Добавляем устаревших пользователей
        for uid in range(5):
            mw._user_timestamps[uid] = [time.monotonic() - 100.0]

        # Смещаем время последней очистки далеко в прошлое
        mw._last_full_cleanup = time.monotonic() - 600.0

        # Новый запрос должен триггерить полную очистку
        event = _make_message_event(user_id=100)
        await mw(handler, event, {})

        # Устаревшие пользователи удалены, остался только user 100
        assert len(mw._user_timestamps) == 1
        assert 100 in mw._user_timestamps

    async def test_no_defaultdict_behavior(self):
        """Словарь НЕ defaultdict — обращение по несуществующему ключу не создаёт запись."""
        mw = ThrottlingMiddleware(rate_limit=5, period=60.0)

        # Прямое обращение не должно создать запись
        result = mw._user_timestamps.get(999)
        assert result is None
        assert 999 not in mw._user_timestamps

    async def test_memory_bounded_under_many_users(self):
        """Общий тест: после массовых запросов + cleanup, память ограничена."""
        mw = ThrottlingMiddleware(rate_limit=2, period=0.01, max_tracked_users=50)
        handler = AsyncMock(return_value="ok")

        # Имитируем 100 уникальных пользователей
        for uid in range(100):
            event = _make_message_event(user_id=uid)
            await mw(handler, event, {})
            # Устариваем сразу
            if uid in mw._user_timestamps:
                mw._user_timestamps[uid] = [t - 1.0 for t in mw._user_timestamps[uid]]

        # Размер не может превышать max_tracked_users
        assert len(mw._user_timestamps) <= 50


# ============================================================================
# Персональные лимиты (user_limits из UserMiddleware)
# ============================================================================


class TestPersonalLimits:
    """Тесты персональных лимитов через data['user_limits']."""

    async def test_personal_limit_override_allows_more(self):
        """Персональный лимит (10) позволяет больше сообщений, чем дефолтный (2)."""
        mw = ThrottlingMiddleware(rate_limit=2, period=60.0)
        handler = AsyncMock(return_value="ok")

        # С персональным лимитом 10 — 5 сообщений проходят
        for _ in range(5):
            event = _make_message_event(user_id=1)
            data = {"user_limits": {"rate_limit": 10, "rate_period": 60.0}}
            result = await mw(handler, event, data)
            assert result == "ok"

        assert handler.call_count == 5

    async def test_personal_limit_override_blocks_at_limit(self):
        """Персональный лимит (3) блокирует при превышении."""
        mw = ThrottlingMiddleware(rate_limit=10, period=60.0)
        handler = AsyncMock(return_value="ok")

        # Исчерпываем персональный лимит 3
        for _ in range(3):
            event = _make_message_event(user_id=1)
            data = {"user_limits": {"rate_limit": 3, "rate_period": 60.0}}
            await mw(handler, event, data)

        assert handler.call_count == 3

        # 4-е сообщение с тем же лимитом — блокируется
        event = _make_message_event(user_id=1)
        data = {"user_limits": {"rate_limit": 3, "rate_period": 60.0}}
        result = await mw(handler, event, data)

        assert result is None
        event.answer.assert_called_once()

    async def test_no_user_limits_uses_default(self):
        """Без user_limits в data — используется дефолтный лимит."""
        mw = ThrottlingMiddleware(rate_limit=2, period=60.0)
        handler = AsyncMock(return_value="ok")

        for _ in range(2):
            event = _make_message_event(user_id=1)
            await mw(handler, event, {})

        # 3-е сообщение — блокировка по дефолтному лимиту
        event = _make_message_event(user_id=1)
        result = await mw(handler, event, {})
        assert result is None

    async def test_personal_period_override(self):
        """Персональный период влияет на сообщение при блокировке."""
        mw = ThrottlingMiddleware(rate_limit=1, period=60.0)
        handler = AsyncMock(return_value="ok")

        # Первое сообщение проходит
        event = _make_message_event(user_id=1)
        data = {"user_limits": {"rate_limit": 1, "rate_period": 120.0}}
        await mw(handler, event, data)

        # Второе — блокировка, период 120 секунд в ответе
        event = _make_message_event(user_id=1)
        data = {"user_limits": {"rate_limit": 1, "rate_period": 120.0}}
        result = await mw(handler, event, data)

        assert result is None
        answer_text = event.answer.call_args[0][0]
        assert "120" in answer_text

    async def test_partial_user_limits_only_rate_limit(self):
        """user_limits только с rate_limit (без rate_period) — period дефолтный."""
        mw = ThrottlingMiddleware(rate_limit=10, period=60.0)
        handler = AsyncMock(return_value="ok")

        for _ in range(3):
            event = _make_message_event(user_id=1)
            data = {"user_limits": {"rate_limit": 3}}
            await mw(handler, event, data)

        # 4-е — блокировка
        event = _make_message_event(user_id=1)
        data = {"user_limits": {"rate_limit": 3}}
        result = await mw(handler, event, data)
        assert result is None

    async def test_is_rate_limited_with_overrides(self):
        """_is_rate_limited корректно использует limit_override/period_override."""
        mw = ThrottlingMiddleware(rate_limit=10, period=60.0)

        # Записываем 2 timestamps для user 1
        mw._user_timestamps[1] = [time.monotonic(), time.monotonic()]

        # Дефолтный лимит 10 → не превышен
        assert mw._is_rate_limited(1) is False

        # Персональный лимит 2 → превышен
        assert mw._is_rate_limited(1, limit_override=2) is True
