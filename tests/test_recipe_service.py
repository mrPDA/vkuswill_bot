"""Тесты RecipeService.

Тестируем:
- Получение ингредиентов (кеш-попадание и промах)
- Масштабирование порций
- Обогащение kg_equivalent
- Форматирование результата
- Парсинг JSON из LLM
- Обработку ошибок
- Фильтрация ферментированных/консервированных продуктов
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vkuswill_bot.services.recipe_service import (
    FERMENTED_KEYWORDS,
    RecipeService,
)


# ============================================================================
# Фикстуры
# ============================================================================


@pytest.fixture
def mock_gigachat_client() -> MagicMock:
    """Замоканный GigaChat клиент."""
    return MagicMock()


@pytest.fixture
def mock_recipe_store() -> AsyncMock:
    """Замоканное хранилище рецептов."""
    store = AsyncMock()
    store.get.return_value = None
    store.save.return_value = None
    return store


@pytest.fixture
def service(mock_gigachat_client, mock_recipe_store) -> RecipeService:
    """RecipeService с замоканными зависимостями."""
    return RecipeService(
        gigachat_client=mock_gigachat_client,
        recipe_store=mock_recipe_store,
    )


# ============================================================================
# get_ingredients: основные сценарии
# ============================================================================


class TestGetIngredients:
    """Тесты get_ingredients: основной метод."""

    async def test_empty_dish_returns_error(self, service):
        result = await service.get_ingredients({"dish": ""})
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "Не указано" in parsed["error"]

    async def test_whitespace_dish_returns_error(self, service):
        result = await service.get_ingredients({"dish": "   "})
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_cache_hit(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [
                {"name": "свёкла", "quantity": 0.5, "unit": "кг", "search_query": "свёкла"},
            ],
        }

        result = await service.get_ingredients({"dish": "борщ", "servings": 4})
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert parsed["cached"] is True
        assert len(parsed["ingredients"]) == 1
        assert parsed["ingredients"][0]["name"] == "свёкла"

    async def test_cache_hit_with_scaling(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [
                {"name": "свёкла", "quantity": 0.5, "unit": "кг"},
            ],
        }

        result = await service.get_ingredients({"dish": "борщ", "servings": 8})
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert parsed["ingredients"][0]["quantity"] == 1.0  # 0.5 * 8/4

    async def test_cache_miss_calls_llm(
        self,
        service,
        mock_recipe_store,
        mock_gigachat_client,
    ):
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps(
            [
                {"name": "свёкла", "quantity": 0.5, "unit": "кг", "search_query": "свёкла"},
            ],
            ensure_ascii=False,
        )

        with patch.object(service._client, "chat", return_value=llm_response):
            result = await service.get_ingredients({"dish": "борщ", "servings": 4})

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["cached"] is False
        mock_recipe_store.save.assert_called_once()

    async def test_llm_error_returns_fallback(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = None

        with patch.object(
            service._client,
            "chat",
            side_effect=RuntimeError("LLM unavailable"),
        ):
            result = await service.get_ingredients({"dish": "борщ"})

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "самостоятельно" in parsed["error"]

    async def test_default_servings(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 2,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service.get_ingredients({"dish": "борщ"})
        parsed = json.loads(result)
        assert parsed["servings"] == 2

    async def test_invalid_servings_defaults_to_2(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 2,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service.get_ingredients({"dish": "борщ", "servings": -1})
        parsed = json.loads(result)
        assert parsed["servings"] == 2

    async def test_servings_zero_defaults_to_2(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 2,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service.get_ingredients({"dish": "борщ", "servings": 0})
        parsed = json.loads(result)
        assert parsed["servings"] == 2

    async def test_servings_string_defaults_to_2(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 2,
            "ingredients": [{"name": "свёкла", "quantity": 0.5}],
        }

        result = await service.get_ingredients({"dish": "борщ", "servings": "два"})
        parsed = json.loads(result)
        assert parsed["servings"] == 2

    async def test_cache_save_failure_handled(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = None
        mock_recipe_store.save.side_effect = RuntimeError("DB write error")

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps(
            [
                {"name": "мясо", "quantity": 1, "unit": "кг"},
            ],
            ensure_ascii=False,
        )

        with patch.object(service._client, "chat", return_value=llm_response):
            result = await service.get_ingredients({"dish": "азу", "servings": 4})

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["cached"] is False

    async def test_llm_returns_empty_list(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = "[]"

        with patch.object(service._client, "chat", return_value=llm_response):
            result = await service.get_ingredients({"dish": "борщ"})

        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "самостоятельно" in parsed["error"]

    async def test_llm_returns_dict_instead_of_list(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = '{"error": "bad request"}'

        with patch.object(service._client, "chat", return_value=llm_response):
            result = await service.get_ingredients({"dish": "борщ"})

        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_cache_miss_with_markdown_response(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[
            0
        ].message.content = '```json\n[{"name": "свёкла", "quantity": 0.5}]\n```'

        with patch.object(service._client, "chat", return_value=llm_response):
            result = await service.get_ingredients({"dish": "борщ"})

        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["ingredients"][0]["name"] == "свёкла"


# ============================================================================
# _enrich_with_kg
# ============================================================================


class TestEnrichWithKg:
    """Тесты _enrich_with_kg: добавление kg_equivalent."""

    WEIGHTS = {  # noqa: RUF012
        "картофель": 0.15,
        "лук": 0.1,
        "морковь": 0.15,
        "свекла": 0.3,
        "помидор": 0.15,
    }

    def test_adds_kg_equivalent_for_piece_items(self):
        items = [{"name": "лук репчатый", "quantity": 3, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.3

    def test_skips_kg_unit(self):
        """Товары в кг — уже готовое значение, kg_equivalent не нужен."""
        items = [{"name": "картофель", "quantity": 1, "unit": "кг"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert "kg_equivalent" not in result[0]

    def test_converts_grams_to_kg(self):
        """Граммы конвертируются в кг (200 г → 0.2 кг)."""
        items = [{"name": "морковь", "quantity": 200, "unit": "г"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.2

    def test_converts_ml_to_liters(self):
        """Миллилитры конвертируются в литры (500 мл → 0.5 л)."""
        items = [{"name": "молоко", "quantity": 500, "unit": "мл"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["l_equivalent"] == 0.5
        assert "kg_equivalent" not in result[0]

    def test_skips_liters_unit(self):
        """Товары в литрах — уже готовое значение, l_equivalent не нужен."""
        items = [{"name": "молоко", "quantity": 1, "unit": "л"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert "l_equivalent" not in result[0]

    def test_skips_non_dict_items(self):
        items = ["строка", 42, None, {"name": "лук", "quantity": 2, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[-1]["kg_equivalent"] == 0.2

    def test_skips_no_match(self):
        items = [{"name": "сметана", "quantity": 1, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert "kg_equivalent" not in result[0]

    def test_skips_zero_quantity(self):
        items = [{"name": "лук", "quantity": 0, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert "kg_equivalent" not in result[0]

    def test_empty_list(self):
        result = RecipeService._enrich_with_kg([], self.WEIGHTS)
        assert result == []

    def test_mutates_in_place(self):
        items = [{"name": "лук", "quantity": 2, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result is items

    def test_missing_unit(self):
        items = [{"name": "помидор", "quantity": 4}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.6

    def test_adds_kg_equivalent_for_potato(self):
        """Картофель 5 шт → kg_equivalent=0.75."""
        items = [{"name": "Картофель молодой", "quantity": 5, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.75

    def test_skips_negative_quantity(self):
        """Отрицательное quantity — не обогащается."""
        items = [{"name": "лук", "quantity": -1, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert "kg_equivalent" not in result[0]

    def test_substring_matching(self):
        """Подстрока: 'морковь' найдена в 'морковь свежая'."""
        items = [{"name": "морковь свежая", "quantity": 2, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.3

    def test_mixed_items(self):
        """Смешанный список: одни обогащаются, другие — нет."""
        items = [
            {"name": "картофель", "quantity": 4, "unit": "шт"},
            {"name": "сливочное масло", "quantity": 1, "unit": "шт"},
            {"name": "помидор", "quantity": 3, "unit": "шт"},
            {"name": "курица", "quantity": 0.8, "unit": "кг"},
        ]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.6
        assert "kg_equivalent" not in result[1]
        assert result[2]["kg_equivalent"] == 0.45
        assert "kg_equivalent" not in result[3]

    def test_empty_weights(self):
        """Пустая таблица весов — ничего не обогащается."""
        items = [{"name": "лук", "quantity": 2, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, {})
        assert "kg_equivalent" not in result[0]

    def test_rounding(self):
        """Результат округляется до 2 знаков."""
        items = [{"name": "свекла", "quantity": 3, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.9

    def test_fractional_quantity(self):
        """Дробное quantity корректно обрабатывается."""
        items = [{"name": "лук", "quantity": 1.5, "unit": "шт"}]
        result = RecipeService._enrich_with_kg(items, self.WEIGHTS)
        assert result[0]["kg_equivalent"] == 0.15


# ============================================================================
# _format_result
# ============================================================================


class TestFormatResult:
    """Тесты _format_result: формирование JSON-ответа."""

    def test_basic_structure(self):
        result = RecipeService._format_result(
            dish="борщ",
            servings=4,
            ingredients=[{"name": "свёкла"}],
            cached=True,
        )
        parsed = json.loads(result)
        assert parsed["ok"] is True
        assert parsed["dish"] == "борщ"
        assert parsed["servings"] == 4
        assert parsed["cached"] is True
        assert "hint" in parsed
        assert "kg_equivalent" in parsed["hint"]

    def test_hint_forbids_extra_items(self):
        """Hint явно запрещает добавлять товары не из списка."""
        result = RecipeService._format_result(
            dish="блинчики",
            servings=5,
            ingredients=[{"name": "мука"}],
            cached=False,
        )
        parsed = json.loads(result)
        hint = parsed["hint"].lower()
        assert "только" in hint or "не добавляй" in hint
        assert "не ищи" in hint or "нет в списке" in hint

    def test_cached_false(self):
        result = RecipeService._format_result(
            dish="плов",
            servings=6,
            ingredients=[{"name": "рис"}, {"name": "морковь"}],
            cached=False,
        )
        parsed = json.loads(result)
        assert parsed["cached"] is False
        assert len(parsed["ingredients"]) == 2

    def test_unicode_preserved(self):
        result = RecipeService._format_result(
            dish="Щи из квашеной капусты",
            servings=4,
            ingredients=[],
            cached=True,
        )
        assert "Щи из квашеной капусты" in result


# ============================================================================
# _parse_json
# ============================================================================


class TestParseJson:
    """Тесты _parse_json: извлечение JSON из ответа GigaChat."""

    def test_plain_json_array(self):
        content = '[{"name": "мясо", "quantity": 1}]'
        result = RecipeService._parse_json(content)
        assert result == [{"name": "мясо", "quantity": 1}]

    def test_json_with_markdown_code_block(self):
        content = '```json\n[{"name": "мясо"}]\n```'
        result = RecipeService._parse_json(content)
        assert result == [{"name": "мясо"}]

    def test_json_with_plain_code_block(self):
        content = '```\n[{"name": "мясо"}]\n```'
        result = RecipeService._parse_json(content)
        assert result == [{"name": "мясо"}]

    def test_json_with_whitespace(self):
        content = '  \n [{"name": "мясо"}] \n  '
        result = RecipeService._parse_json(content)
        assert result == [{"name": "мясо"}]

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            RecipeService._parse_json("not json at all")

    def test_json_object(self):
        content = '{"ok": true}'
        result = RecipeService._parse_json(content)
        assert result == {"ok": True}


# ============================================================================
# Hint и обогащение в get_ingredients
# ============================================================================


class TestGetIngredientsEnrichment:
    """Тесты обогащения ингредиентов в get_ingredients."""

    async def test_hint_in_result(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [{"name": "свёкла"}],
        }

        result = await service.get_ingredients({"dish": "борщ"})
        parsed = json.loads(result)
        assert "hint" in parsed
        assert "kg_equivalent" in parsed["hint"]

    async def test_cache_hit_enriched_with_kg(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [
                {"name": "картофель", "quantity": 4, "unit": "шт"},
                {"name": "свёкла", "quantity": 0.5, "unit": "кг"},
            ],
        }

        result = await service.get_ingredients({"dish": "борщ", "servings": 4})
        parsed = json.loads(result)

        assert parsed["ingredients"][0].get("kg_equivalent") == 0.6
        assert "kg_equivalent" not in parsed["ingredients"][1]

    async def test_llm_result_enriched_with_kg(self, service, mock_recipe_store):
        mock_recipe_store.get.return_value = None

        llm_response = MagicMock()
        llm_response.choices = [MagicMock()]
        llm_response.choices[0].message.content = json.dumps(
            [
                {"name": "лук репчатый", "quantity": 2, "unit": "шт"},
                {"name": "говядина", "quantity": 0.8, "unit": "кг"},
            ],
            ensure_ascii=False,
        )

        with patch.object(service._client, "chat", return_value=llm_response):
            result = await service.get_ingredients({"dish": "азу", "servings": 4})

        parsed = json.loads(result)
        assert parsed["ingredients"][0].get("kg_equivalent") == 0.2
        assert "kg_equivalent" not in parsed["ingredients"][1]


# ============================================================================
# Фильтр ферментированных продуктов
# ============================================================================


class TestIsFermentedProduct:
    """Тесты is_fermented_product — блокировка ферментированных продуктов."""

    @pytest.mark.parametrize(
        "dish",
        [
            "квашеная капуста",
            "Квашеная Капуста",
            "КВАШЕНАЯ КАПУСТА",
            "солёные огурцы",
            "соленые огурцы",
            "маринованные грибы",
            "кимчи",
            "аджика",
            "варенье из малины",
            "джем клубничный",
            "горчица",
            "мочёные яблоки",
            "моченые яблоки",
        ],
    )
    def test_fermented_detected(self, dish):
        """Ферментированные/консервированные продукты определяются."""
        assert RecipeService.is_fermented_product(dish) is True

    @pytest.mark.parametrize(
        "dish",
        [
            "борщ",
            "паста карбонара",
            "стейк вагю",
            "картофельное пюре",
            "плов узбекский",
            "салат цезарь",
            "омлет с грибами",
            "капуста тушёная",
            "огурцы свежие",
            "грибной суп",
        ],
    )
    def test_normal_dishes_not_blocked(self, dish):
        """Обычные блюда НЕ блокируются."""
        assert RecipeService.is_fermented_product(dish) is False

    def test_empty_string(self):
        """Пустая строка не является ферментированным продуктом."""
        assert RecipeService.is_fermented_product("") is False

    def test_constants_not_empty(self):
        """Константа FERMENTED_KEYWORDS не пустая."""
        assert len(FERMENTED_KEYWORDS) > 0
        assert isinstance(FERMENTED_KEYWORDS, frozenset)


class TestGetIngredientsFermentedBlock:
    """Тесты блокировки ферментированных продуктов в get_ingredients."""

    async def test_fermented_product_returns_error(
        self,
        service,
        mock_recipe_store,
    ):
        """recipe_ingredients('квашеная капуста') возвращает ошибку."""
        result = await service.get_ingredients({"dish": "квашеная капуста"})
        parsed = json.loads(result)

        assert parsed["ok"] is False
        assert "ферментированный" in parsed["error"]
        assert "vkusvill_products_search" in parsed["error"]
        # НЕ должен обращаться к кешу рецептов
        mock_recipe_store.get.assert_not_called()

    async def test_fermented_product_does_not_call_llm(
        self,
        service,
        mock_recipe_store,
    ):
        """Для ферментированных продуктов НЕ вызывается GigaChat."""
        with patch.object(service._client, "chat") as mock_chat:
            await service.get_ingredients({"dish": "маринованные грибы"})
            mock_chat.assert_not_called()

    async def test_soljonye_ogurcy_blocked(self, service, mock_recipe_store):
        """Солёные огурцы блокируются."""
        result = await service.get_ingredients({"dish": "солёные огурцы"})
        parsed = json.loads(result)
        assert parsed["ok"] is False

    async def test_normal_dish_not_blocked(self, service, mock_recipe_store):
        """Обычное блюдо (борщ) проходит фильтр и идёт в кеш."""
        mock_recipe_store.get.return_value = {
            "dish_name": "борщ",
            "servings": 4,
            "ingredients": [{"name": "свёкла"}],
        }
        result = await service.get_ingredients({"dish": "борщ"})
        parsed = json.loads(result)
        assert parsed["ok"] is True
        mock_recipe_store.get.assert_called_once()

    async def test_kimchi_blocked(self, service, mock_recipe_store):
        """Кимчи блокируется."""
        result = await service.get_ingredients({"dish": "кимчи"})
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "кимчи" in parsed["error"]

    async def test_varenie_blocked(self, service, mock_recipe_store):
        """Варенье блокируется."""
        result = await service.get_ingredients({"dish": "варенье из малины"})
        parsed = json.loads(result)
        assert parsed["ok"] is False
