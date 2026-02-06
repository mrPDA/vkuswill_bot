"""Тесты PreferencesStore.

Тестируем:
- CRUD-операции (set/get/delete)
- Upsert (перезапись предпочтения)
- Пустой результат для нового пользователя
- Форматирование для GigaChat
- Нормализация категорий (lowercase, strip)
- Закрытие соединения
"""

import json
import os

import pytest

from vkuswill_bot.services.preferences_store import PreferencesStore


@pytest.fixture
async def store(tmp_path):
    """PreferencesStore с временной БД."""
    db_path = str(tmp_path / "test_prefs.db")
    s = PreferencesStore(db_path)
    yield s
    await s.close()


# ============================================================================
# CRUD
# ============================================================================


class TestCRUD:
    """Тесты базовых CRUD-операций."""

    async def test_set_and_get(self, store):
        """Сохранение и чтение предпочтения."""
        await store.set(user_id=1, category="мороженое", preference="пломбир")
        prefs = await store.get_all(user_id=1)

        assert len(prefs) == 1
        assert prefs[0]["category"] == "мороженое"
        assert prefs[0]["preference"] == "пломбир"

    async def test_set_multiple(self, store):
        """Несколько предпочтений одного пользователя."""
        await store.set(1, "мороженое", "пломбир в шоколаде")
        await store.set(1, "молоко", "3,2% жирности")
        await store.set(1, "хлеб", "бородинский")

        prefs = await store.get_all(1)
        assert len(prefs) == 3
        categories = {p["category"] for p in prefs}
        assert categories == {"мороженое", "молоко", "хлеб"}

    async def test_upsert(self, store):
        """Перезапись предпочтения той же категории."""
        await store.set(1, "мороженое", "пломбир")
        await store.set(1, "мороженое", "фруктовый лёд")

        prefs = await store.get_all(1)
        assert len(prefs) == 1
        assert prefs[0]["preference"] == "фруктовый лёд"

    async def test_delete_existing(self, store):
        """Удаление существующего предпочтения."""
        await store.set(1, "мороженое", "пломбир")
        result = await store.delete(1, "мороженое")

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert "удалено" in parsed["message"]

        prefs = await store.get_all(1)
        assert len(prefs) == 0

    async def test_delete_nonexistent(self, store):
        """Удаление несуществующего предпочтения."""
        result = await store.delete(1, "несуществующее")

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert "не найдено" in parsed["message"]

    async def test_different_users(self, store):
        """У разных пользователей — отдельные предпочтения."""
        await store.set(1, "мороженое", "пломбир")
        await store.set(2, "мороженое", "фруктовый лёд")

        prefs_1 = await store.get_all(1)
        prefs_2 = await store.get_all(2)

        assert prefs_1[0]["preference"] == "пломбир"
        assert prefs_2[0]["preference"] == "фруктовый лёд"


# ============================================================================
# Пустые результаты
# ============================================================================


class TestEmpty:
    """Тесты для нового/пустого пользователя."""

    async def test_empty_get_all(self, store):
        """Новый пользователь — пустой список."""
        prefs = await store.get_all(user_id=999)
        assert prefs == []

    async def test_empty_formatted(self, store):
        """Новый пользователь — JSON с пустым списком."""
        result = await store.get_formatted(user_id=999)
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["preferences"] == []
        assert "Нет сохранённых" in parsed["message"]


# ============================================================================
# Форматирование для GigaChat
# ============================================================================


class TestFormatting:
    """Тесты get_formatted."""

    async def test_formatted_single(self, store):
        """Одно предпочтение — JSON с одним элементом."""
        await store.set(1, "мороженое", "пломбир в шоколаде на палочке")
        result = await store.get_formatted(1)
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert len(parsed["preferences"]) == 1
        assert parsed["preferences"][0]["category"] == "мороженое"
        assert parsed["preferences"][0]["preference"] == "пломбир в шоколаде на палочке"

    async def test_formatted_multiple(self, store):
        """Несколько предпочтений — все на месте."""
        await store.set(1, "молоко", "3,2%")
        await store.set(1, "хлеб", "бородинский")
        result = await store.get_formatted(1)
        parsed = json.loads(result)

        assert len(parsed["preferences"]) == 2
        categories = {p["category"] for p in parsed["preferences"]}
        assert "молоко" in categories
        assert "хлеб" in categories


# ============================================================================
# Нормализация
# ============================================================================


class TestNormalization:
    """Тесты нормализации категорий."""

    async def test_category_lowercase(self, store):
        """Категория приводится к lowercase."""
        await store.set(1, "Мороженое", "пломбир")
        prefs = await store.get_all(1)
        assert prefs[0]["category"] == "мороженое"

    async def test_category_strip(self, store):
        """Пробелы вокруг категории удаляются."""
        await store.set(1, "  мороженое  ", "пломбир")
        prefs = await store.get_all(1)
        assert prefs[0]["category"] == "мороженое"

    async def test_preference_strip(self, store):
        """Пробелы вокруг предпочтения удаляются."""
        await store.set(1, "мороженое", "  пломбир  ")
        prefs = await store.get_all(1)
        assert prefs[0]["preference"] == "пломбир"

    async def test_upsert_ignores_case(self, store):
        """Upsert работает независимо от регистра категории."""
        await store.set(1, "Мороженое", "пломбир")
        await store.set(1, "мороженое", "фруктовый лёд")

        prefs = await store.get_all(1)
        assert len(prefs) == 1
        assert prefs[0]["preference"] == "фруктовый лёд"


# ============================================================================
# set() возвращает JSON
# ============================================================================


class TestSetReturn:
    """Тесты возвращаемого значения set()."""

    async def test_set_returns_json(self, store):
        """set() возвращает JSON-строку с ok=True."""
        result = await store.set(1, "мороженое", "пломбир")
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert "Запомнил" in parsed["message"]


# ============================================================================
# Закрытие
# ============================================================================


class TestClose:
    """Тесты закрытия хранилища."""

    async def test_close_idempotent(self, tmp_path):
        """Повторное закрытие не вызывает ошибку."""
        store = PreferencesStore(str(tmp_path / "test.db"))
        await store.set(1, "тест", "тест")
        await store.close()
        await store.close()  # не должно упасть

    async def test_reopen_after_close(self, tmp_path):
        """После закрытия можно снова открыть и данные на месте."""
        db_path = str(tmp_path / "test.db")

        store1 = PreferencesStore(db_path)
        await store1.set(1, "мороженое", "пломбир")
        await store1.close()

        store2 = PreferencesStore(db_path)
        prefs = await store2.get_all(1)
        assert len(prefs) == 1
        assert prefs[0]["preference"] == "пломбир"
        await store2.close()


# ============================================================================
# Создание директории
# ============================================================================


class TestDirectory:
    """Тесты автоматического создания директории."""

    async def test_creates_parent_directory(self, tmp_path):
        """Создаёт родительскую директорию если не существует."""
        db_path = str(tmp_path / "subdir" / "deep" / "prefs.db")
        store = PreferencesStore(db_path)
        await store.set(1, "тест", "значение")

        assert os.path.exists(db_path)
        await store.close()


# ============================================================================
# SQL injection
# ============================================================================


class TestSQLInjection:
    """Тесты устойчивости к SQL-инъекциям.

    PreferencesStore использует параметризованные запросы (? placeholders),
    поэтому инъекции не должны работать.
    """

    SQL_INJECTION_PAYLOADS = [
        "'; DROP TABLE preferences; --",
        "1' OR '1'='1",
        "' UNION SELECT * FROM sqlite_master --",
        "Robert'); DROP TABLE preferences;--",
        "' OR 1=1 --",
        "'; DELETE FROM preferences WHERE '1'='1",
        "test\"; DROP TABLE preferences; --",
        "1; SELECT sql FROM sqlite_master",
    ]

    @pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
    async def test_injection_in_category(self, store, payload: str):
        """SQL-инъекция в категории не разрушает БД."""
        await store.set(1, payload, "нормальное значение")
        prefs = await store.get_all(1)

        # Данные сохранились как есть (payload стал категорией)
        assert len(prefs) >= 1
        # БД не сломалась — можно делать другие операции
        await store.set(1, "нормальная категория", "нормальное значение")
        prefs = await store.get_all(1)
        assert any(p["category"] == "нормальная категория" for p in prefs)

    @pytest.mark.parametrize("payload", SQL_INJECTION_PAYLOADS)
    async def test_injection_in_preference(self, store, payload: str):
        """SQL-инъекция в предпочтении не разрушает БД."""
        await store.set(1, "категория", payload)
        prefs = await store.get_all(1)

        assert len(prefs) == 1
        assert prefs[0]["preference"] == payload

    async def test_injection_in_delete(self, store):
        """SQL-инъекция при удалении не удаляет чужие записи."""
        await store.set(1, "молоко", "3,2%")
        await store.set(1, "хлеб", "бородинский")

        # Пытаемся удалить с инъекцией
        await store.delete(1, "молоко' OR '1'='1")

        prefs = await store.get_all(1)
        # Обе записи на месте — инъекция не сработала
        assert len(prefs) == 2

    async def test_table_integrity_after_injections(self, store):
        """После серии инъекций таблица остаётся рабочей."""
        for payload in self.SQL_INJECTION_PAYLOADS:
            await store.set(1, payload, "test")

        # Таблица работает
        prefs = await store.get_all(1)
        assert len(prefs) > 0

        # Можем добавить и прочитать нормальную запись
        await store.set(2, "мороженое", "пломбир")
        prefs_2 = await store.get_all(2)
        assert len(prefs_2) == 1
        assert prefs_2[0]["preference"] == "пломбир"
