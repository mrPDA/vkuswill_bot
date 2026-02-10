"""Тесты RecipeStore.

Тестируем:
- CRUD-операции (save/get)
- Масштабирование ингредиентов (scale_ingredients)
- Нормализация названий блюд
- Кеш-промах (возврат None)
- Upsert (перезапись рецепта)
- WAL mode (F-03)
- Закрытие соединения
- Автосоздание директории
"""

import json
import os

import pytest

from vkuswill_bot.services.recipe_store import RecipeStore


# ---- Фикстуры ----

@pytest.fixture
async def store(tmp_path):
    """RecipeStore с временной БД."""
    db_path = str(tmp_path / "test_recipes.db")
    s = RecipeStore(db_path)
    yield s
    await s.close()


SAMPLE_INGREDIENTS = [
    {"name": "говядина", "quantity": 0.8, "unit": "кг", "search_query": "говядина"},
    {"name": "картофель", "quantity": 0.8, "unit": "кг", "search_query": "картофель"},
    {"name": "лук репчатый", "quantity": 2, "unit": "шт", "search_query": "лук"},
    {"name": "огурцы солёные", "quantity": 4, "unit": "шт", "search_query": "огурцы солёные"},
    {"name": "томатная паста", "quantity": 2, "unit": "ст.л.", "search_query": "томатная паста"},
    {"name": "чеснок", "quantity": 3, "unit": "зубчик", "search_query": "чеснок"},
]


# ============================================================================
# CRUD
# ============================================================================


class TestCRUD:
    """Тесты базовых операций сохранения и чтения."""

    async def test_save_and_get(self, store):
        """Сохранение и чтение рецепта."""
        await store.save("азу из говядины", 4, SAMPLE_INGREDIENTS)
        result = await store.get("азу из говядины")

        assert result is not None
        assert result["dish_name"] == "азу из говядины"
        assert result["servings"] == 4
        assert len(result["ingredients"]) == 6
        assert result["ingredients"][0]["name"] == "говядина"

    async def test_get_nonexistent(self, store):
        """Чтение несуществующего рецепта возвращает None."""
        result = await store.get("несуществующее блюдо")
        assert result is None

    async def test_upsert(self, store):
        """Перезапись рецепта с тем же названием."""
        await store.save("борщ", 4, [{"name": "свёкла"}])
        await store.save("борщ", 6, [{"name": "свёкла"}, {"name": "капуста"}])

        result = await store.get("борщ")
        assert result is not None
        assert result["servings"] == 6
        assert len(result["ingredients"]) == 2

    async def test_multiple_dishes(self, store):
        """Несколько разных рецептов."""
        await store.save("борщ", 4, [{"name": "свёкла"}])
        await store.save("окрошка", 4, [{"name": "кефир"}])
        await store.save("азу", 4, [{"name": "говядина"}])

        assert (await store.get("борщ")) is not None
        assert (await store.get("окрошка")) is not None
        assert (await store.get("азу")) is not None
        assert (await store.get("плов")) is None


# ============================================================================
# Нормализация
# ============================================================================


class TestNormalization:
    """Тесты нормализации названий блюд."""

    def test_normalize_lowercase(self):
        """Название приводится к lowercase."""
        assert RecipeStore.normalize_dish_name("Борщ") == "борщ"

    def test_normalize_strip(self):
        """Пробелы удаляются."""
        assert RecipeStore.normalize_dish_name("  борщ  ") == "борщ"

    def test_normalize_combined(self):
        """Lowercase + strip."""
        assert RecipeStore.normalize_dish_name("  Азу из Говядины  ") == "азу из говядины"

    async def test_case_insensitive_lookup(self, store):
        """Поиск нечувствителен к регистру."""
        await store.save("Борщ Украинский", 4, [{"name": "свёкла"}])

        result = await store.get("борщ украинский")
        assert result is not None

        result = await store.get("БОРЩ УКРАИНСКИЙ")
        assert result is not None


# ============================================================================
# Масштабирование
# ============================================================================


class TestScaling:
    """Тесты scale_ingredients."""

    def test_same_servings_no_change(self):
        """При одинаковом количестве порций — без изменений."""
        ingredients = [{"name": "мясо", "quantity": 1.0, "unit": "кг"}]
        result = RecipeStore.scale_ingredients(ingredients, 4, 4)
        assert result[0]["quantity"] == 1.0

    def test_double_servings(self):
        """Удвоение порций — удвоение количеств."""
        ingredients = [
            {"name": "мясо", "quantity": 0.8, "unit": "кг"},
            {"name": "лук", "quantity": 2, "unit": "шт"},
        ]
        result = RecipeStore.scale_ingredients(ingredients, 4, 8)
        assert result[0]["quantity"] == 1.6
        assert result[1]["quantity"] == 4

    def test_half_servings(self):
        """Половина порций — половина количеств."""
        ingredients = [{"name": "мясо", "quantity": 0.8, "unit": "кг"}]
        result = RecipeStore.scale_ingredients(ingredients, 4, 2)
        assert result[0]["quantity"] == 0.4

    def test_non_numeric_quantity_unchanged(self):
        """Нечисловые количества не меняются."""
        ingredients = [{"name": "соль", "quantity": "по вкусу", "unit": ""}]
        result = RecipeStore.scale_ingredients(ingredients, 4, 8)
        assert result[0]["quantity"] == "по вкусу"

    def test_zero_from_servings(self):
        """При from_servings=0 — без изменений (защита от деления на 0)."""
        ingredients = [{"name": "мясо", "quantity": 1.0, "unit": "кг"}]
        result = RecipeStore.scale_ingredients(ingredients, 0, 4)
        assert result[0]["quantity"] == 1.0

    def test_missing_quantity_field(self):
        """Ингредиент без quantity — не падает."""
        ingredients = [{"name": "лавровый лист", "unit": "шт"}]
        result = RecipeStore.scale_ingredients(ingredients, 4, 8)
        assert "quantity" not in result[0] or result[0].get("quantity") is None

    def test_preserves_other_fields(self):
        """Масштабирование не теряет другие поля."""
        ingredients = [
            {"name": "мясо", "quantity": 1.0, "unit": "кг", "search_query": "говядина"}
        ]
        result = RecipeStore.scale_ingredients(ingredients, 4, 8)
        assert result[0]["name"] == "мясо"
        assert result[0]["unit"] == "кг"
        assert result[0]["search_query"] == "говядина"
        assert result[0]["quantity"] == 2.0

    def test_does_not_mutate_original(self):
        """Оригинальный список не мутируется."""
        ingredients = [{"name": "мясо", "quantity": 1.0, "unit": "кг"}]
        RecipeStore.scale_ingredients(ingredients, 4, 8)
        assert ingredients[0]["quantity"] == 1.0

    def test_rounding(self):
        """Результат округляется до 2 знаков."""
        ingredients = [{"name": "мясо", "quantity": 1.0, "unit": "кг"}]
        result = RecipeStore.scale_ingredients(ingredients, 3, 7)
        # 1.0 * 7/3 = 2.333...
        assert result[0]["quantity"] == 2.33


# ============================================================================
# Закрытие
# ============================================================================


class TestClose:
    """Тесты закрытия хранилища."""

    async def test_close_idempotent(self, tmp_path):
        """Повторное закрытие не вызывает ошибку."""
        store = RecipeStore(str(tmp_path / "test.db"))
        await store.save("борщ", 4, [{"name": "свёкла"}])
        await store.close()
        await store.close()  # не должно упасть

    async def test_reopen_after_close(self, tmp_path):
        """После закрытия данные сохраняются."""
        db_path = str(tmp_path / "test.db")

        store1 = RecipeStore(db_path)
        await store1.save("борщ", 4, [{"name": "свёкла"}])
        await store1.close()

        store2 = RecipeStore(db_path)
        result = await store2.get("борщ")
        assert result is not None
        assert result["ingredients"][0]["name"] == "свёкла"
        await store2.close()


# ============================================================================
# Создание директории
# ============================================================================


class TestDirectory:
    """Тесты автоматического создания директории."""

    async def test_creates_parent_directory(self, tmp_path):
        """Создаёт родительскую директорию если не существует."""
        db_path = str(tmp_path / "subdir" / "deep" / "recipes.db")
        store = RecipeStore(db_path)
        await store.save("борщ", 4, [{"name": "свёкла"}])
        assert os.path.exists(db_path)
        await store.close()


# ============================================================================
# Повреждённые данные
# ============================================================================


class TestCorruptedData:
    """Тесты обработки повреждённых данных."""

    async def test_corrupted_json_returns_none(self, tmp_path):
        """Повреждённый JSON в ingredients возвращает None."""
        import aiosqlite

        db_path = str(tmp_path / "corrupt.db")
        store = RecipeStore(db_path)
        # Инициализируем БД
        await store.save("борщ", 4, [{"name": "свёкла"}])

        # Вручную портим данные
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE recipes SET ingredients = 'not-valid-json' "
                "WHERE dish_name = 'борщ'",
            )
            await db.commit()

        result = await store.get("борщ")
        assert result is None
        await store.close()


# ============================================================================
# F-03: WAL mode
# ============================================================================


class TestWALMode:
    """F-03: Тесты включения WAL mode для RecipeStore."""

    async def test_wal_mode_enabled(self, tmp_path):
        """БД рецептов открывается с journal_mode=WAL."""
        import aiosqlite

        db_path = str(tmp_path / "wal_recipes.db")
        store = RecipeStore(db_path)
        # Инициализируем БД
        await store.save("борщ", 4, [{"name": "свёкла"}])

        # Проверяем через отдельное соединение
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("PRAGMA journal_mode")
            row = await cursor.fetchone()
            assert row[0] == "wal"
        await store.close()


# ============================================================================
# Delete — удаление рецептов из кеша
# ============================================================================


class TestDeleteRecipe:
    """Тесты метода RecipeStore.delete."""

    async def test_delete_existing_recipe(self, tmp_path):
        """Удаление существующего рецепта возвращает True."""
        store = RecipeStore(str(tmp_path / "del.db"))
        await store.save("квашеная капуста", 4, [{"name": "капуста"}])
        assert await store.get("квашеная капуста") is not None

        result = await store.delete("квашеная капуста")
        assert result is True
        assert await store.get("квашеная капуста") is None
        await store.close()

    async def test_delete_nonexistent_recipe(self, tmp_path):
        """Удаление несуществующего рецепта возвращает False."""
        store = RecipeStore(str(tmp_path / "del2.db"))
        # Инициализируем БД
        await store.save("борщ", 4, [{"name": "свёкла"}])

        result = await store.delete("несуществующий рецепт")
        assert result is False
        await store.close()

    async def test_delete_normalizes_name(self, tmp_path):
        """Delete нормализует имя (регистр)."""
        store = RecipeStore(str(tmp_path / "del3.db"))
        await store.save("Кимчи", 4, [{"name": "капуста пекинская"}])

        result = await store.delete("КИМЧИ")
        assert result is True
        assert await store.get("кимчи") is None
        await store.close()
