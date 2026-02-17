"""Тесты RedisDialogManager.

Тестируем:
- Per-user lock с LRU-вытеснением
- aget_history: загрузка из Redis / создание нового диалога
- save_history: сохранение в Redis с TTL
- trim_list: обрезка истории (чистая функция)
- areset: удаление из Redis + lock
- _serialize / _deserialize: round-trip сериализация Messages
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from gigachat.models import FunctionCall, Messages, MessagesRole

from vkuswill_bot.services.prompts import SYSTEM_PROMPT
from vkuswill_bot.services.redis_dialog_manager import (
    DEFAULT_DIALOG_TTL,
    MAX_LOCKS,
    RedisDialogManager,
    _deserialize,
    _serialize,
)


# ============================================================================
# Фикстуры
# ============================================================================


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Замоканный Redis-клиент."""
    redis = AsyncMock()
    redis.get.return_value = None  # пустой Redis по умолчанию
    redis.set.return_value = True
    redis.delete.return_value = 1
    redis.expire.return_value = True
    return redis


@pytest.fixture
def manager(mock_redis) -> RedisDialogManager:
    """RedisDialogManager с замоканным Redis и max_history=10."""
    return RedisDialogManager(
        redis=mock_redis,
        max_history=10,
        dialog_ttl=3600,
    )


# ============================================================================
# Per-user lock с LRU-вытеснением
# ============================================================================


class TestGetLock:
    """Тесты get_lock: per-user asyncio.Lock с LRU."""

    def test_creates_lock(self, manager):
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

    def test_lru_eviction(self, mock_redis):
        """LRU-вытеснение при превышении MAX_LOCKS."""
        mgr = RedisDialogManager(redis=mock_redis, max_history=10)

        # Создаём MAX_LOCKS + 1 lock
        for uid in range(MAX_LOCKS + 1):
            mgr.get_lock(uid)

        # Самый старый (0) должен быть вытеснен
        assert len(mgr._locks) == MAX_LOCKS
        assert 0 not in mgr._locks
        assert MAX_LOCKS in mgr._locks

    def test_access_refreshes_lru(self, manager):
        """Обращение к lock перемещает его в конец (LRU)."""
        manager.get_lock(1)
        manager.get_lock(2)
        manager.get_lock(3)

        # Обращаемся к 1 — он перемещается в конец
        manager.get_lock(1)

        keys = list(manager._locks.keys())
        assert keys == [2, 3, 1]

    async def test_lock_serializes_operations(self, manager):
        """Lock сериализует конкурентные операции."""
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
# aget_history: загрузка из Redis
# ============================================================================


class TestAgetHistory:
    """Тесты aget_history: загрузка/создание истории."""

    async def test_new_user_creates_system_prompt(self, manager, mock_redis):
        """Новый пользователь (нет в Redis) — возвращает [system prompt]."""
        mock_redis.get.return_value = None

        history = await manager.aget_history(user_id=1)

        assert len(history) == 1
        assert history[0].role == MessagesRole.SYSTEM
        assert history[0].content == SYSTEM_PROMPT
        mock_redis.get.assert_called_once_with("dialog:1")

    async def test_loads_from_redis(self, manager, mock_redis):
        """Существующая история загружается из Redis."""
        stored_history = [
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content="Привет"),
            Messages(role=MessagesRole.ASSISTANT, content="Здравствуйте!"),
        ]
        mock_redis.get.return_value = _serialize(stored_history)

        history = await manager.aget_history(user_id=1)

        assert len(history) == 3
        assert history[0].role == MessagesRole.SYSTEM
        assert history[1].content == "Привет"
        assert history[2].content == "Здравствуйте!"

    async def test_extends_ttl_on_access(self, manager, mock_redis):
        """TTL продлевается при каждом доступе (sliding window)."""
        stored = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        mock_redis.get.return_value = _serialize(stored)

        await manager.aget_history(user_id=42)

        mock_redis.expire.assert_called_once_with("dialog:42", 3600)

    async def test_handles_corrupted_data(self, manager, mock_redis):
        """Повреждённые данные в Redis — создаёт новый диалог."""
        mock_redis.get.return_value = b"not valid json"

        history = await manager.aget_history(user_id=1)

        # Должен вернуть новый диалог, а не упасть
        assert len(history) == 1
        assert history[0].role == MessagesRole.SYSTEM

    async def test_handles_bytes_from_redis(self, manager, mock_redis):
        """Redis возвращает bytes (decode_responses=False) — корректная обработка."""
        stored = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        mock_redis.get.return_value = _serialize(stored).encode("utf-8")

        history = await manager.aget_history(user_id=1)

        assert len(history) == 1
        assert history[0].content == SYSTEM_PROMPT


# ============================================================================
# save_history: сохранение в Redis
# ============================================================================


class TestSaveHistory:
    """Тесты save_history: сохранение в Redis с TTL."""

    async def test_saves_to_redis(self, manager, mock_redis):
        """save_history сохраняет сериализованную историю в Redis."""
        history = [
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content="Молоко"),
        ]

        await manager.save_history(user_id=42, history=history)

        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert call_args.args[0] == "dialog:42"
        assert call_args.kwargs["ex"] == 3600

        # Проверяем, что JSON валиден
        saved_json = call_args.args[1]
        items = json.loads(saved_json)
        assert len(items) == 2
        assert items[0]["role"] == "system"
        assert items[1]["content"] == "Молоко"

    async def test_uses_configured_ttl(self, mock_redis):
        """TTL берётся из конфигурации."""
        mgr = RedisDialogManager(
            redis=mock_redis,
            max_history=50,
            dialog_ttl=7200,  # 2 часа
        )
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]

        await mgr.save_history(user_id=1, history=history)

        assert mock_redis.set.call_args.kwargs["ex"] == 7200

    async def test_default_ttl(self, mock_redis):
        """По умолчанию TTL = DEFAULT_DIALOG_TTL (24 часа)."""
        mgr = RedisDialogManager(redis=mock_redis, max_history=50)
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]

        await mgr.save_history(user_id=1, history=history)

        assert mock_redis.set.call_args.kwargs["ex"] == DEFAULT_DIALOG_TTL


# ============================================================================
# trim_list: обрезка истории (чистая функция)
# ============================================================================


class TestTrimList:
    """Тесты trim_list: обрезка истории."""

    def test_trims_long_history(self, manager):
        """Длинная история обрезается до max_history."""
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        for i in range(15):
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

        result = manager.trim_list(history)

        assert len(result) == 10
        assert result[0].role == MessagesRole.SYSTEM
        assert result[-1].content == "msg-14"

    def test_noop_when_short(self, manager):
        """Короткая история — без изменений."""
        history = [
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content="тест"),
        ]

        result = manager.trim_list(history)
        assert result is history

    def test_returns_new_list_when_trimmed(self, manager):
        """При обрезке возвращается новый список."""
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        for i in range(15):
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

        result = manager.trim_list(history)
        assert result is not history

    def test_identical_to_dialog_manager(self, manager):
        """trim_list идентичен DialogManager.trim_list."""
        from vkuswill_bot.services.dialog_manager import DialogManager

        dm = DialogManager(max_history=10)
        history = [Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)]
        for i in range(15):
            history.append(Messages(role=MessagesRole.USER, content=f"msg-{i}"))

        redis_result = manager.trim_list(list(history))
        memory_result = dm.trim_list(list(history))

        assert len(redis_result) == len(memory_result)
        for r, m in zip(redis_result, memory_result, strict=False):
            assert r.content == m.content
            assert r.role == m.role


# ============================================================================
# areset: удаление диалога
# ============================================================================


class TestAreset:
    """Тесты areset: удаление диалога из Redis."""

    async def test_deletes_from_redis(self, manager, mock_redis):
        """areset удаляет ключ из Redis."""
        await manager.areset(user_id=42)

        mock_redis.delete.assert_called_once_with("dialog:42")

    async def test_removes_lock(self, manager, mock_redis):
        """areset удаляет lock пользователя."""
        manager.get_lock(42)
        assert 42 in manager._locks

        await manager.areset(user_id=42)
        assert 42 not in manager._locks

    async def test_nonexistent_user(self, manager, mock_redis):
        """areset несуществующего пользователя — не падает."""
        await manager.areset(user_id=999)
        mock_redis.delete.assert_called_once_with("dialog:999")


# ============================================================================
# _serialize: сериализация Messages → JSON
# ============================================================================


class TestSerialize:
    """Тесты _serialize: сериализация list[Messages] → JSON."""

    def test_basic_messages(self):
        """Базовые сообщения сериализуются корректно."""
        history = [
            Messages(role=MessagesRole.SYSTEM, content="Системный промпт"),
            Messages(role=MessagesRole.USER, content="Привет"),
            Messages(role=MessagesRole.ASSISTANT, content="Здравствуйте!"),
        ]

        result = _serialize(history)
        items = json.loads(result)

        assert len(items) == 3
        assert items[0]["role"] == "system"
        assert items[0]["content"] == "Системный промпт"
        assert items[1]["role"] == "user"
        assert items[2]["role"] == "assistant"

    def test_function_message_with_name(self):
        """FUNCTION-сообщение сохраняет name."""
        msg = Messages(
            role=MessagesRole.FUNCTION,
            content='{"ok": true}',
        )
        msg.name = "vkusvill_products_search"

        result = _serialize([msg])
        items = json.loads(result)

        assert items[0]["name"] == "vkusvill_products_search"

    def test_function_call_with_dict_args(self):
        """function_call с dict arguments сериализуется в JSON-строку."""
        msg = Messages(
            role=MessagesRole.ASSISTANT,
            content="",
        )
        msg.function_call = FunctionCall(
            name="vkusvill_products_search",
            arguments={"q": "молоко"},
        )

        result = _serialize([msg])
        items = json.loads(result)

        fc = items[0]["function_call"]
        assert fc["name"] == "vkusvill_products_search"
        # arguments — JSON-строка
        assert json.loads(fc["arguments"]) == {"q": "молоко"}

    def test_functions_state_id(self):
        """functions_state_id сохраняется."""
        msg = Messages(
            role=MessagesRole.ASSISTANT,
            content="",
        )
        msg.functions_state_id = "state-abc-123"

        result = _serialize([msg])
        items = json.loads(result)

        assert items[0]["functions_state_id"] == "state-abc-123"

    def test_unicode_preserved(self):
        """Русский текст сохраняется (ensure_ascii=False)."""
        history = [
            Messages(role=MessagesRole.USER, content="Молоко 3,2%"),
        ]

        result = _serialize(history)
        assert "Молоко 3,2%" in result

    def test_empty_history(self):
        """Пустая история → пустой массив."""
        result = _serialize([])
        assert json.loads(result) == []


# ============================================================================
# _deserialize: десериализация JSON → Messages
# ============================================================================


class TestDeserialize:
    """Тесты _deserialize: десериализация JSON → list[Messages]."""

    def test_basic_messages(self):
        """Базовые сообщения десериализуются корректно."""
        raw = json.dumps(
            [
                {"role": "system", "content": "Промпт"},
                {"role": "user", "content": "Привет"},
                {"role": "assistant", "content": "Ответ"},
            ]
        )

        messages = _deserialize(raw)

        assert len(messages) == 3
        assert messages[0].role == MessagesRole.SYSTEM
        assert messages[0].content == "Промпт"
        assert messages[1].role == MessagesRole.USER
        assert messages[2].role == MessagesRole.ASSISTANT

    def test_function_message(self):
        """FUNCTION-сообщение с name."""
        raw = json.dumps(
            [
                {"role": "function", "content": '{"ok": true}', "name": "search"},
            ]
        )

        messages = _deserialize(raw)

        assert messages[0].role == MessagesRole.FUNCTION
        assert messages[0].name == "search"

    def test_function_call(self):
        """function_call десериализуется с FunctionCall."""
        raw = json.dumps(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "function_call": {
                        "name": "vkusvill_products_search",
                        "arguments": '{"q": "молоко"}',
                    },
                },
            ]
        )

        messages = _deserialize(raw)

        assert messages[0].function_call is not None
        assert messages[0].function_call.name == "vkusvill_products_search"
        assert messages[0].function_call.arguments == {"q": "молоко"}

    def test_functions_state_id(self):
        """functions_state_id восстанавливается."""
        raw = json.dumps(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "functions_state_id": "state-123",
                },
            ]
        )

        messages = _deserialize(raw)
        assert messages[0].functions_state_id == "state-123"

    def test_bytes_input(self):
        """Принимает bytes (как из Redis без decode_responses)."""
        raw_bytes = json.dumps(
            [
                {"role": "user", "content": "тест"},
            ]
        ).encode("utf-8")

        messages = _deserialize(raw_bytes)
        assert messages[0].content == "тест"

    def test_invalid_json_raises(self):
        """Невалидный JSON → JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            _deserialize("not json")

    def test_missing_content_defaults_empty(self):
        """Отсутствующий content → пустая строка."""
        raw = json.dumps([{"role": "user"}])
        messages = _deserialize(raw)
        assert messages[0].content == ""

    def test_function_call_invalid_args(self):
        """Невалидные arguments в function_call → None."""
        raw = json.dumps(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "function_call": {
                        "name": "test",
                        "arguments": "not valid json {",
                    },
                },
            ]
        )

        messages = _deserialize(raw)
        assert messages[0].function_call.arguments is None


# ============================================================================
# Round-trip: serialize → deserialize
# ============================================================================


class TestSerializeDeserializeRoundTrip:
    """Тесты round-trip: _serialize → _deserialize."""

    def test_basic_round_trip(self):
        """Базовый round-trip: serialize → deserialize сохраняет данные."""
        history = [
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content="Найди молоко"),
            Messages(role=MessagesRole.ASSISTANT, content="Нашёл 3 варианта"),
        ]

        raw = _serialize(history)
        restored = _deserialize(raw)

        assert len(restored) == 3
        for original, restored_msg in zip(history, restored, strict=False):
            assert original.role == restored_msg.role
            assert original.content == restored_msg.content

    def test_function_call_round_trip(self):
        """Round-trip с function_call."""
        msg = Messages(role=MessagesRole.ASSISTANT, content="")
        msg.function_call = FunctionCall(
            name="vkusvill_products_search",
            arguments={"q": "творог 5%", "limit": 5},
        )

        raw = _serialize([msg])
        restored = _deserialize(raw)

        assert restored[0].function_call is not None
        assert restored[0].function_call.name == "vkusvill_products_search"
        assert restored[0].function_call.arguments == {"q": "творог 5%", "limit": 5}

    def test_function_message_round_trip(self):
        """Round-trip с FUNCTION-сообщением (name + content)."""
        msg = Messages(
            role=MessagesRole.FUNCTION,
            content='{"ok": true, "data": {"items": []}}',
        )
        msg.name = "vkusvill_products_search"

        raw = _serialize([msg])
        restored = _deserialize(raw)

        assert restored[0].role == MessagesRole.FUNCTION
        assert restored[0].name == "vkusvill_products_search"
        assert '"ok": true' in restored[0].content

    def test_complex_dialog_round_trip(self):
        """Round-trip сложного диалога: system → user → function_call → function → assistant."""
        history = [
            Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT),
            Messages(role=MessagesRole.USER, content="Купи молоко"),
        ]

        # Assistant с function_call
        assistant_msg = Messages(role=MessagesRole.ASSISTANT, content="")
        assistant_msg.function_call = FunctionCall(
            name="vkusvill_products_search",
            arguments={"q": "молоко"},
        )
        assistant_msg.functions_state_id = "state-1"
        history.append(assistant_msg)

        # Function result
        func_msg = Messages(
            role=MessagesRole.FUNCTION,
            content='{"ok": true}',
        )
        func_msg.name = "vkusvill_products_search"
        history.append(func_msg)

        # Final assistant response
        history.append(Messages(role=MessagesRole.ASSISTANT, content="Молоко за 79 руб!"))

        raw = _serialize(history)
        restored = _deserialize(raw)

        assert len(restored) == 5
        assert restored[0].role == MessagesRole.SYSTEM
        assert restored[1].content == "Купи молоко"
        assert restored[2].function_call.name == "vkusvill_products_search"
        assert restored[2].functions_state_id == "state-1"
        assert restored[3].name == "vkusvill_products_search"
        assert restored[4].content == "Молоко за 79 руб!"
