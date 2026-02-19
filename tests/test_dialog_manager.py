"""Тесты DialogManager.

Тестируем:
- Создание и получение истории диалога
- LRU-вытеснение при переполнении
- Обрезку истории (trim / trim_list)
- Сброс диалога (reset / areset)
- Per-user lock
- Async API (aget_history, save_history, trim_list, areset)
- Суммаризация tool results (_summarize_tool_result)
"""

import asyncio
import json

from gigachat.models import Messages, MessagesRole

from vkuswill_bot.services.dialog_manager import (
    DialogManager,
    MAX_CONVERSATIONS,
    MAX_SUMMARY_LENGTH,
    _summarize_tool_result,
)
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
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

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
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

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
        second_entered = asyncio.Event()
        release_first = asyncio.Event()
        in_critical = 0
        max_in_critical = 0

        async def task(val: int) -> None:
            nonlocal in_critical, max_in_critical
            async with lock:
                in_critical += 1
                max_in_critical = max(max_in_critical, in_critical)
                if val == 1:
                    await release_first.wait()
                else:
                    second_entered.set()
                results.append(val)
                in_critical -= 1

        t1 = asyncio.create_task(task(1))
        await asyncio.sleep(0)  # Дать первому таску захватить lock
        t2 = asyncio.create_task(task(2))
        await asyncio.sleep(0)  # Дать второму таску попытаться войти в lock
        assert not second_entered.is_set()

        release_first.set()
        await asyncio.gather(t1, t2)

        assert results == [1, 2]
        assert max_in_critical == 1


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


# ============================================================================
# Async API: aget_history
# ============================================================================


class TestAgetHistory:
    """Тесты aget_history: async-обёртка get_history."""

    async def test_creates_new_with_system_prompt(self, manager):
        """Async: создаёт историю с системным промптом."""
        history = await manager.aget_history(user_id=1)

        assert len(history) == 1
        assert history[0].role == MessagesRole.SYSTEM
        assert history[0].content == SYSTEM_PROMPT

    async def test_reuses_existing(self, manager):
        """Async: повторный вызов возвращает ту же историю."""
        h1 = await manager.aget_history(user_id=1)
        h2 = await manager.aget_history(user_id=1)
        assert h1 is h2

    async def test_consistent_with_sync(self, manager):
        """aget_history и get_history возвращают один и тот же объект."""
        h_sync = manager.get_history(user_id=1)
        h_async = await manager.aget_history(user_id=1)
        assert h_sync is h_async


# ============================================================================
# Async API: save_history
# ============================================================================


class TestSaveHistory:
    """Тесты save_history: сохранение (обновление) истории."""

    async def test_saves_new_history(self, manager):
        """save_history обновляет ссылку в conversations."""
        original = await manager.aget_history(user_id=1)
        original.append(Messages(role=MessagesRole.USER, content="тест"))

        new_list = list(original)  # новый объект
        await manager.save_history(user_id=1, history=new_list)

        assert manager.conversations[1] is new_list

    async def test_save_after_trim(self, manager):
        """save_history после trim_list обновляет историю корректно."""
        history = await manager.aget_history(user_id=1)
        for i in range(15):
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

        trimmed = manager.trim_list(history)
        await manager.save_history(user_id=1, history=trimmed)

        saved = manager.conversations[1]
        assert len(saved) == 10  # max_history
        assert saved[0].role == MessagesRole.SYSTEM

    async def test_save_for_new_user(self, manager):
        """save_history для нового пользователя создаёт запись."""
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        await manager.save_history(user_id=99, history=history)

        assert 99 in manager.conversations
        assert manager.conversations[99] is history


# ============================================================================
# Async API: trim_list (чистая функция)
# ============================================================================


class TestTrimList:
    """Тесты trim_list: обрезка истории как чистая функция."""

    def test_trims_long_history(self, manager):
        """Длинная история обрезается до max_history."""
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        for i in range(15):
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

        result = manager.trim_list(history)

        assert len(result) == 10  # max_history
        assert result[0].role == MessagesRole.SYSTEM
        assert result[-1].content == "msg-14"

    def test_noop_when_short(self, manager):
        """Короткая история возвращается без изменений."""
        history = [
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content="привет"),
        ]

        result = manager.trim_list(history)
        assert result is history  # тот же объект

    def test_returns_new_list_when_trimmed(self, manager):
        """При обрезке возвращается НОВЫЙ список (не мутирует оригинал)."""
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        for i in range(15):
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

        original_len = len(history)
        result = manager.trim_list(history)

        assert result is not history  # новый объект
        assert len(history) == original_len  # оригинал не мутирован

    def test_preserves_system_prompt(self, manager):
        """Системный промпт всегда первый после обрезки."""
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        for i in range(20):
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

        result = manager.trim_list(history)
        assert result[0].role == MessagesRole.SYSTEM
        assert result[0].content == SYSTEM_PROMPT

    def test_exact_max_history_noop(self, manager):
        """Ровно max_history элементов — не обрезается."""
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        for i in range(9):  # 1 + 9 = 10 = max_history
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

        result = manager.trim_list(history)
        assert result is history
        assert len(result) == 10


# ============================================================================
# Async API: areset
# ============================================================================


class TestAreset:
    """Тесты areset: async-сброс диалога."""

    async def test_removes_history(self, manager):
        """areset удаляет историю пользователя."""
        manager.get_history(user_id=42)
        assert 42 in manager.conversations

        await manager.areset(user_id=42)
        assert 42 not in manager.conversations

    async def test_removes_lock(self, manager):
        """areset удаляет lock пользователя."""
        manager.get_lock(42)
        assert 42 in manager._locks

        await manager.areset(user_id=42)
        assert 42 not in manager._locks

    async def test_nonexistent_user(self, manager):
        """areset несуществующего пользователя не падает."""
        await manager.areset(user_id=999)  # не должно бросить

    async def test_consistent_with_sync_reset(self, manager):
        """areset и reset дают одинаковый результат."""
        # Подготовка: два пользователя
        manager.get_history(user_id=1)
        manager.get_history(user_id=2)
        manager.get_lock(1)
        manager.get_lock(2)

        # Sync reset
        manager.reset(user_id=1)
        # Async reset
        await manager.areset(user_id=2)

        assert 1 not in manager.conversations
        assert 2 not in manager.conversations
        assert 1 not in manager._locks
        assert 2 not in manager._locks


# ============================================================================
# Суммаризация tool results: _summarize_tool_result
# ============================================================================


class TestSummarizeToolResult:
    """Тесты _summarize_tool_result: диспетчеризация и форматирование."""

    # ---- Диспетчеризация по name (приоритет) ----

    def test_products_search_by_name(self):
        """name='vkusvill_products_search' → суммаризация поиска."""
        content = json.dumps(
            {
                "query": "молоко",
                "products": [{"name": "Молоко 3.2%", "price": 89}],
            }
        )
        result = _summarize_tool_result("vkusvill_products_search", content)
        assert "Поиск" in result
        assert "молоко" in result
        assert "1 товаров" in result

    def test_cart_link_by_name(self):
        """name='vkusvill_cart_link_create' → суммаризация корзины."""
        content = json.dumps(
            {
                "cart_link": "https://example.com/cart",
                "price_summary": {"total": 450, "count": 3},
            }
        )
        result = _summarize_tool_result("vkusvill_cart_link_create", content)
        assert "Корзина" in result
        assert "450" in result

    def test_recipe_by_name(self):
        """name='recipe_ingredients' → суммаризация рецепта."""
        content = json.dumps(
            {
                "dish": "борщ",
                "ingredients": [{"name": "свёкла"}, {"name": "капуста"}],
            }
        )
        result = _summarize_tool_result("recipe_ingredients", content)
        assert "Рецепт" in result
        assert "борщ" in result
        assert "2 ингредиентов" in result

    def test_preferences_by_name(self):
        """name='user_preferences_get' → суммаризация предпочтений."""
        content = json.dumps({"preferences": {"diet": "вегетарианство"}})
        result = _summarize_tool_result("user_preferences_get", content)
        assert "Предпочтения" in result
        assert "вегетарианство" in result

    def test_nutrition_by_name(self):
        """name='nutrition_lookup' → суммаризация КБЖУ."""
        content = json.dumps({"product": "яблоко", "kcal": 52})
        result = _summarize_tool_result("nutrition_lookup", content)
        assert "КБЖУ" in result
        assert "яблоко" in result
        assert "52" in result

    # ---- name имеет приоритет над ключами JSON (главный баг-фикс) ----

    def test_name_priority_over_products_key(self):
        """name='recipe_ingredients' не перехватывается ключом 'products'.

        Регрессия: OR-условие ранее направляло на суммаризатор
        поиска при наличии ключа 'products' в JSON.
        """
        content = json.dumps(
            {
                "dish": "салат",
                "ingredients": [{"name": "огурец"}],
                "products": [{"name": "Огурцы", "price": 50}],  # ключ-ловушка
            }
        )
        result = _summarize_tool_result("recipe_ingredients", content)
        assert "Рецепт" in result
        assert "салат" in result
        assert "Поиск" not in result  # НЕ суммаризатор поиска

    def test_name_priority_over_cart_link_key(self):
        """name='vkusvill_products_search' не перехватывается ключом 'cart_link'."""
        content = json.dumps(
            {
                "query": "хлеб",
                "products": [{"name": "Хлеб", "price": 45}],
                "cart_link": "https://example.com",  # ключ-ловушка
            }
        )
        result = _summarize_tool_result("vkusvill_products_search", content)
        assert "Поиск" in result
        assert "Корзина" not in result

    def test_name_priority_over_ingredients_key(self):
        """name='vkusvill_cart_link_create' не перехватывается ключом 'ingredients'."""
        content = json.dumps(
            {
                "cart_link": "https://example.com/cart",
                "price_summary": {"total": 200, "count": 2},
                "ingredients": ["огурцы", "помидоры"],  # ключ-ловушка
            }
        )
        result = _summarize_tool_result("vkusvill_cart_link_create", content)
        assert "Корзина" in result
        assert "Рецепт" not in result

    # ---- Эвристика по ключам (name=None) ----

    def test_key_heuristic_products(self):
        """name=None + ключ 'products' → суммаризация поиска."""
        content = json.dumps(
            {
                "query": "сыр",
                "products": [{"name": "Сыр Голландский", "price": 320}],
            }
        )
        result = _summarize_tool_result(None, content)
        assert "Поиск" in result
        assert "сыр" in result

    def test_key_heuristic_cart_link(self):
        """name=None + ключ 'cart_link' → суммаризация корзины."""
        content = json.dumps(
            {
                "cart_link": "https://example.com/cart",
                "price_summary": {"total": 100, "count": 1},
            }
        )
        result = _summarize_tool_result(None, content)
        assert "Корзина" in result

    def test_key_heuristic_preferences(self):
        """name=None + ключ 'preferences' → суммаризация предпочтений."""
        content = json.dumps({"preferences": {"allergies": "орехи"}})
        result = _summarize_tool_result(None, content)
        assert "Предпочтения" in result

    def test_key_heuristic_ingredients(self):
        """name=None + ключ 'ingredients' → суммаризация рецепта."""
        content = json.dumps(
            {
                "dish": "паста",
                "ingredients": [{"name": "мука"}],
            }
        )
        result = _summarize_tool_result(None, content)
        assert "Рецепт" in result

    # ---- Fallback ----

    def test_unknown_name_no_keys_fallback(self):
        """Неизвестный name + нет характерных ключей → fallback (обрезка)."""
        content = json.dumps({"status": "ok", "data": "x" * 300})
        result = _summarize_tool_result("unknown_tool", content)
        assert len(result) <= MAX_SUMMARY_LENGTH + 5  # +5 для "…"

    def test_none_name_no_keys_fallback(self):
        """name=None + нет характерных ключей → fallback."""
        content = json.dumps({"status": "ok"})
        result = _summarize_tool_result(None, content)
        assert result == content  # короткий JSON — без обрезки

    def test_not_json_fallback(self):
        """Не-JSON контент → fallback."""
        content = "Просто текстовый ответ"
        result = _summarize_tool_result("some_tool", content)
        assert result == content

    def test_long_not_json_truncated(self):
        """Длинный не-JSON контент обрезается до MAX_SUMMARY_LENGTH."""
        content = "A" * 500
        result = _summarize_tool_result(None, content)
        assert len(result) == MAX_SUMMARY_LENGTH + 1  # +1 для "…"
        assert result.endswith("…")

    def test_non_dict_json_fallback(self):
        """JSON-массив (не dict) → fallback."""
        content = json.dumps([1, 2, 3])
        result = _summarize_tool_result("some_tool", content)
        assert result == content

    def test_products_search_empty_results(self):
        """Поиск с пустым списком товаров."""
        content = json.dumps({"query": "единорог", "products": []})
        result = _summarize_tool_result("vkusvill_products_search", content)
        assert "0 товаров" in result

    def test_preferences_empty(self):
        """Пустые предпочтения."""
        content = json.dumps({"preferences": {}})
        result = _summarize_tool_result("user_preferences_get", content)
        assert "пусто" in result
