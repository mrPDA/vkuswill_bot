"""Тесты DialogManager.

Тестируем:
- Создание и получение истории диалога
- LRU-вытеснение при переполнении
- Обрезку истории (trim)
- Сброс диалога (reset)
- Per-user lock
"""

import asyncio

from gigachat.models import Messages, MessagesRole

from vkuswill_bot.services.dialog_manager import DialogManager, MAX_CONVERSATIONS
from vkuswill_bot.services.prompts import SYSTEM_PROMPT


# ============================================================================
# Фикстуры
# ============================================================================

import pytest


@pytest.fixture
def manager() -> DialogManager:
    """DialogManager с max_history=10 для тестов."""
    return DialogManager(max_history=10)


# ============================================================================
# Управление историей
# ============================================================================


class TestGetHistory:
    """Тесты get_history: создание и получение истории."""

    def test_creates_new_with_system_prompt(self, manager):
        """Создаёт историю с системным промптом для нового пользователя."""
        history = manager.get_history(user_id=1)

        assert len(history) == 1
        assert history[0].role == MessagesRole.SYSTEM
        assert history[0].content == SYSTEM_PROMPT

    def test_reuses_existing(self, manager):
        """Повторный вызов возвращает ту же историю."""
        h1 = manager.get_history(user_id=1)
        h2 = manager.get_history(user_id=1)

        assert h1 is h2

    def test_different_users_separate_histories(self, manager):
        """У разных пользователей — отдельные истории."""
        h1 = manager.get_history(user_id=1)
        h2 = manager.get_history(user_id=2)

        assert h1 is not h2

    def test_access_moves_to_end(self, manager):
        """Доступ к диалогу перемещает его в конец (LRU)."""
        for uid in range(3):
            manager.get_history(uid)

        # Обращаемся к 0 — он теперь самый свежий
        manager.get_history(0)

        keys = list(manager.conversations.keys())
        assert keys == [1, 2, 0]


# ============================================================================
# LRU-вытеснение
# ============================================================================


class TestLRUEviction:
    """Тесты LRU-вытеснения диалогов из памяти."""

    def test_eviction_at_limit(self):
        """При превышении max_conversations старейший диалог удаляется."""
        mgr = DialogManager(max_conversations=MAX_CONVERSATIONS)

        # Заполняем до лимита
        for uid in range(MAX_CONVERSATIONS):
            mgr.get_history(uid)

        assert len(mgr.conversations) == MAX_CONVERSATIONS

        # Добавляем ещё одного — первый должен быть вытеснен
        mgr.get_history(MAX_CONVERSATIONS)

        assert len(mgr.conversations) == MAX_CONVERSATIONS
        assert 0 not in mgr.conversations  # самый старый вытеснен
        assert MAX_CONVERSATIONS in mgr.conversations  # новый на месте

    def test_access_refreshes_lru(self):
        """Обращение к диалогу перемещает его в конец (не вытесняется)."""
        mgr = DialogManager(max_conversations=3)

        # Создаём 3 диалога: 0, 1, 2
        for uid in range(3):
            mgr.get_history(uid)

        # Обращаемся к 0 — он теперь самый свежий
        mgr.get_history(0)

        # Порядок: 1, 2, 0
        keys = list(mgr.conversations.keys())
        assert keys == [1, 2, 0]

        # Добавляем 3 — вытеснится 1 (самый старый)
        mgr.get_history(3)
        assert 1 not in mgr.conversations
        assert 0 in mgr.conversations  # 0 свежий, не вытеснен

    def test_small_limit_eviction(self):
        """Маленький лимит (2) — вытеснение при 3-м диалоге."""
        mgr = DialogManager(max_conversations=2)

        mgr.get_history(1)
        mgr.get_history(2)
        assert len(mgr.conversations) == 2

        mgr.get_history(3)
        assert len(mgr.conversations) == 2
        assert 1 not in mgr.conversations


# ============================================================================
# Обрезка истории
# ============================================================================


class TestTrim:
    """Тесты trim: обрезка длинной истории."""

    def test_trim_long_history(self, manager):
        """Обрезка истории при превышении max_history=10."""
        history = manager.get_history(user_id=1)
        # Добавляем 15 сообщений (1 системный + 15 = 16 всего)
        for i in range(15):
            history.append(
                Messages(role=MessagesRole.USER, content=f"msg-{i}")
            )

        manager.trim(user_id=1)

        trimmed = manager.conversations[1]
        assert len(trimmed) == 10  # max_history
        assert trimmed[0].role == MessagesRole.SYSTEM  # системный промпт сохранён
        assert trimmed[-1].content == "msg-14"  # последнее сообщение на месте

    def test_trim_noop_when_short(self, manager):
        """Обрезка ничего не делает, если история короткая."""
        history = manager.get_history(user_id=1)
        original_len = len(history)

        manager.trim(user_id=1)

        assert len(manager.conversations[1]) == original_len

    def test_trim_nonexistent_user(self, manager):
        """Обрезка несуществующего пользователя не падает."""
        manager.trim(user_id=999)  # не должно бросить исключение

    def test_trim_preserves_system_prompt(self, manager):
        """После обрезки системный промпт всегда первый."""
        history = manager.get_history(user_id=1)
        for i in range(20):
            history.append(
                Messages(role=MessagesRole.USER, content=f"msg-{i}")
            )

        manager.trim(user_id=1)

        trimmed = manager.conversations[1]
        assert trimmed[0].role == MessagesRole.SYSTEM
        assert trimmed[0].content == SYSTEM_PROMPT


# ============================================================================
# Сброс диалога
# ============================================================================


class TestReset:
    """Тесты reset: сброс истории и lock."""

    def test_reset_removes_history(self, manager):
        """Сброс удаляет историю пользователя."""
        manager.get_history(user_id=42)
        assert 42 in manager.conversations

        manager.reset(user_id=42)
        assert 42 not in manager.conversations

    def test_reset_removes_lock(self, manager):
        """Сброс удаляет lock пользователя."""
        manager.get_lock(42)
        assert 42 in manager._locks

        manager.reset(user_id=42)
        assert 42 not in manager._locks

    def test_reset_nonexistent_user(self, manager):
        """Сброс несуществующего пользователя не падает."""
        manager.reset(user_id=999)  # не должно бросить исключение


# ============================================================================
# Per-user lock
# ============================================================================


class TestGetLock:
    """Тесты get_lock: per-user asyncio.Lock."""

    def test_creates_lock_on_first_access(self, manager):
        """Lock создаётся при первом обращении."""
        lock = manager.get_lock(42)
        assert isinstance(lock, asyncio.Lock)

    def test_returns_same_lock(self, manager):
        """Повторный вызов возвращает тот же lock."""
        lock1 = manager.get_lock(42)
        lock2 = manager.get_lock(42)
        assert lock1 is lock2

    def test_different_users_different_locks(self, manager):
        """У разных пользователей — разные lock."""
        lock1 = manager.get_lock(1)
        lock2 = manager.get_lock(2)
        assert lock1 is not lock2

    async def test_lock_actually_serializes(self, manager):
        """Lock действительно сериализует конкурентные операции."""
        lock = manager.get_lock(42)
        results: list[int] = []

        async def task(val: int) -> None:
            async with lock:
                results.append(val)
                await asyncio.sleep(0.01)

        await asyncio.gather(task(1), task(2))
        # Оба значения должны быть в results (в любом порядке)
        assert sorted(results) == [1, 2]


# ============================================================================
# Свойство conversations
# ============================================================================


class TestConversationsProperty:
    """Тесты свойства conversations."""

    def test_returns_underlying_dict(self, manager):
        """Свойство conversations возвращает OrderedDict."""
        manager.get_history(1)
        assert 1 in manager.conversations
        assert isinstance(manager.conversations, dict)
