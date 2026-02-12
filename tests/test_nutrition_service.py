"""Тесты для NutritionService (Open Food Facts API)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from vkuswill_bot.services.nutrition_service import (
    NutritionService,
    OFF_FIELDS,
    OFF_SEARCH_URL,
    SEARCH_PAGE_SIZE,
    _NUTRIENT_KEYS,
    _WEIGHT_RE,
)


# ---- Фикстуры ----


@pytest.fixture
def service() -> NutritionService:
    """Создать NutritionService."""
    return NutritionService()


@pytest.fixture
def mock_off_response() -> dict:
    """Пример ответа Open Food Facts для борщ."""
    return {
        "count": 23,
        "products": [
            {
                "product_name": "Борщ с сухариками",
                "brands": "Knorr",
                "nutriments": {
                    "energy-kcal_100g": 325,
                    "proteins_100g": 7.0,
                    "fat_100g": 9.5,
                    "carbohydrates_100g": 53.0,
                    "fiber_100g": 2.1,
                    "sugars_100g": 12.0,
                    "salt_100g": 1.5,
                },
                "serving_size": "250 ml",
                "nutrition_grades": "d",
            },
            {
                "product_name": "Борщ вегетарианский",
                "brands": "ВкусВилл",
                "nutriments": {
                    "energy-kcal_100g": 74.1,
                    "proteins_100g": 1.8,
                    "fat_100g": 3.3,
                    "carbohydrates_100g": 9.3,
                    "fiber_100g": None,
                    "sugars_100g": "",
                    "salt_100g": 0.8,
                },
                "serving_size": "",
                "nutrition_grades": "a",
            },
        ],
    }


@pytest.fixture
def mock_empty_response() -> dict:
    """Пустой ответ Open Food Facts."""
    return {"count": 0, "products": []}


@pytest.fixture
def mock_no_nutrition_response() -> dict:
    """Продукты без КБЖУ."""
    return {
        "count": 1,
        "products": [
            {
                "product_name": "Unknown product",
                "brands": "",
                "nutriments": {},
            },
        ],
    }


# ---- Тесты _extract_nutrients ----


class TestExtractNutrients:
    """Тесты извлечения нутриентов из ответа Open Food Facts."""

    def test_extract_full_nutrients(self, mock_off_response: dict) -> None:
        """Извлечение всех КБЖУ."""
        product = mock_off_response["products"][0]
        result = NutritionService._extract_nutrients(product)
        assert result["calories"] == 325.0
        assert result["protein"] == 7.0
        assert result["fat"] == 9.5
        assert result["carbs"] == 53.0
        assert result["fiber"] == 2.1
        assert result["sugars"] == 12.0
        assert result["salt"] == 1.5

    def test_extract_with_none_values(self, mock_off_response: dict) -> None:
        """None и пустые строки → None."""
        product = mock_off_response["products"][1]
        result = NutritionService._extract_nutrients(product)
        assert result["calories"] == 74.1
        assert result["fiber"] is None  # None в исходных данных
        assert result["sugars"] is None  # Пустая строка

    def test_empty_nutriments(self) -> None:
        """Пустой nutriments → все None."""
        product = {"nutriments": {}}
        result = NutritionService._extract_nutrients(product)
        assert all(v is None for v in result.values())

    def test_no_nutriments_key(self) -> None:
        """Нет ключа nutriments."""
        product = {}
        result = NutritionService._extract_nutrients(product)
        assert all(v is None for v in result.values())

    def test_non_dict_nutriments(self) -> None:
        """nutriments не dict."""
        product = {"nutriments": "invalid"}
        result = NutritionService._extract_nutrients(product)
        assert all(v is None for v in result.values())

    def test_invalid_value_type(self) -> None:
        """Нечисловые значения → None."""
        product = {"nutriments": {"energy-kcal_100g": "not a number"}}
        result = NutritionService._extract_nutrients(product)
        assert result["calories"] is None


# ---- Тесты _has_nutrition ----


class TestHasNutrition:
    """Тесты проверки наличия КБЖУ."""

    def test_has_kcal(self) -> None:
        """Продукт с калориями."""
        product = {"nutriments": {"energy-kcal_100g": 100}}
        assert NutritionService._has_nutrition(product) is True

    def test_no_kcal(self) -> None:
        """Продукт без калорий."""
        product = {"nutriments": {"proteins_100g": 10}}
        assert NutritionService._has_nutrition(product) is False

    def test_kcal_none(self) -> None:
        """Калории = None."""
        product = {"nutriments": {"energy-kcal_100g": None}}
        assert NutritionService._has_nutrition(product) is False

    def test_kcal_empty_string(self) -> None:
        """Калории = пустая строка."""
        product = {"nutriments": {"energy-kcal_100g": ""}}
        assert NutritionService._has_nutrition(product) is False

    def test_no_nutriments(self) -> None:
        """Нет nutriments."""
        assert NutritionService._has_nutrition({}) is False

    def test_non_dict_nutriments(self) -> None:
        """nutriments не dict."""
        assert NutritionService._has_nutrition({"nutriments": []}) is False


# ---- Тесты lookup ----


class TestLookup:
    """Тесты метода lookup (основной API)."""

    @pytest.mark.asyncio
    async def test_lookup_success(
        self,
        service: NutritionService,
        mock_off_response: dict,
    ) -> None:
        """Успешный поиск КБЖУ."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(service, "_search", AsyncMock(return_value=mock_off_response["products"]))
            result = json.loads(await service.lookup({"query": "борщ"}))

        assert result["ok"] is True
        data = result["data"]
        assert data["query"] == "борщ"
        assert data["found"] is True
        assert data["count"] == 2
        assert len(data["items"]) == 2
        assert data["items"][0]["name"] == "Борщ с сухариками"
        assert data["items"][0]["brand"] == "Knorr"
        assert data["items"][0]["nutriscore"] == "D"
        assert data["items"][0]["nutrients_per_100g"]["calories"] == 325.0
        assert data["items"][1]["brand"] == "ВкусВилл"
        assert "hint" in data

    @pytest.mark.asyncio
    async def test_lookup_not_found(
        self,
        service: NutritionService,
    ) -> None:
        """Продукт не найден."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(service, "_search", AsyncMock(return_value=[]))
            result = json.loads(await service.lookup({"query": "инопланетная еда"}))

        assert result["ok"] is True
        data = result["data"]
        assert data["found"] is False
        assert "не найдены" in data["message"]

    @pytest.mark.asyncio
    async def test_lookup_no_nutrition_data(
        self,
        service: NutritionService,
        mock_no_nutrition_response: dict,
    ) -> None:
        """Продукты есть, но без КБЖУ."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                service,
                "_search",
                AsyncMock(return_value=mock_no_nutrition_response["products"]),
            )
            result = json.loads(await service.lookup({"query": "unknown"}))

        assert result["ok"] is True
        assert result["data"]["found"] is False

    @pytest.mark.asyncio
    async def test_lookup_empty_query(self, service: NutritionService) -> None:
        """Пустой запрос."""
        result = json.loads(await service.lookup({"query": ""}))
        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_lookup_no_query(self, service: NutritionService) -> None:
        """Запрос без query."""
        result = json.loads(await service.lookup({}))
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_lookup_api_error(self, service: NutritionService) -> None:
        """Ошибка Open Food Facts API."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                service,
                "_search",
                AsyncMock(side_effect=httpx.HTTPError("Connection failed")),
            )
            result = json.loads(await service.lookup({"query": "борщ"}))

        assert result["ok"] is False
        assert "Open Food Facts" in result["error"]

    @pytest.mark.asyncio
    async def test_lookup_timeout(self, service: NutritionService) -> None:
        """Таймаут Open Food Facts API."""
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                service,
                "_search",
                AsyncMock(side_effect=httpx.TimeoutException("Read timed out")),
            )
            result = json.loads(await service.lookup({"query": "рис"}))

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_lookup_normalizes_html_entities(
        self,
        service: NutritionService,
    ) -> None:
        """HTML-entities в запросе очищаются перед поиском."""
        products = [
            {
                "product_name": "Молоко",
                "brands": "ВкусВилл",
                "nutriments": {"energy-kcal_100g": 60},
                "serving_size": "",
                "nutrition_grades": "",
            },
        ]
        search_mock = AsyncMock(return_value=products)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(service, "_search", search_mock)
            result = json.loads(await service.lookup({"query": "Молоко 3,2%, 1\xa0л"}))

        assert result["ok"] is True
        # Запрос в _search должен быть нормализован (без &nbsp; и веса)
        search_mock.assert_called_once_with("Молоко")

    @pytest.mark.asyncio
    async def test_lookup_skips_empty_brand(
        self,
        service: NutritionService,
    ) -> None:
        """Пустой brand не добавляется в результат."""
        products = [
            {
                "product_name": "Рис",
                "brands": "",
                "nutriments": {"energy-kcal_100g": 130},
                "serving_size": "",
                "nutrition_grades": "",
            },
        ]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(service, "_search", AsyncMock(return_value=products))
            result = json.loads(await service.lookup({"query": "рис"}))

        item = result["data"]["items"][0]
        assert "brand" not in item
        assert "nutriscore" not in item
        assert "serving_size" not in item


# ---- Тесты _normalize_query ----


class TestNormalizeQuery:
    """Тесты нормализации запросов (HTML-entities, вес, проценты)."""

    def test_html_nbsp(self) -> None:
        """&nbsp; декодируется в пробел и не ломает запрос."""
        assert NutritionService._normalize_query("Молоко 3,2%, 1\xa0л") == "Молоко"
        # &nbsp; в виде HTML entity
        assert NutritionService._normalize_query("Плов с курицей, 600&nbsp;г") == "Плов с курицей"

    def test_html_entities(self) -> None:
        """HTML-entities декодируются."""
        assert "грудка" in NutritionService._normalize_query("Куриная грудка &amp; филе")

    def test_weight_grams(self) -> None:
        """Удаление веса в граммах."""
        assert NutritionService._normalize_query("Масло сливочное, 200 г") == "Масло сливочное"

    def test_weight_kg(self) -> None:
        """Удаление веса в кг."""
        assert NutritionService._normalize_query("Борщ с говядиной, 1 кг") == "Борщ с говядиной"

    def test_weight_ml(self) -> None:
        """Удаление объёма в мл."""
        assert NutritionService._normalize_query("Кефир, 500 мл") == "Кефир"

    def test_weight_liters(self) -> None:
        """Удаление объёма в литрах."""
        assert NutritionService._normalize_query("Молоко, 1 л") == "Молоко"

    def test_percentage(self) -> None:
        """Удаление процентов жирности."""
        assert NutritionService._normalize_query("Молоко 3,2%") == "Молоко"
        assert NutritionService._normalize_query("Масло сливочное 82,5%, 200 г") == "Масло сливочное"

    def test_clean_query_unchanged(self) -> None:
        """Чистый запрос не меняется."""
        assert NutritionService._normalize_query("куриная грудка") == "куриная грудка"
        assert NutritionService._normalize_query("борщ") == "борщ"

    def test_empty_string(self) -> None:
        """Пустая строка."""
        assert NutritionService._normalize_query("") == ""

    def test_only_weight(self) -> None:
        """Если остаётся пустая строка после нормализации."""
        assert NutritionService._normalize_query("200 г") == ""

    def test_multiple_spaces_collapsed(self) -> None:
        """Множественные пробелы схлопываются."""
        assert NutritionService._normalize_query("Творог  обезжиренный") == "Творог обезжиренный"


# ---- Тесты _search ----


class TestSearch:
    """Тесты прямого вызова Open Food Facts API."""

    @pytest.mark.asyncio
    async def test_search_params_include_ru(self, service: NutritionService) -> None:
        """Проверка параметров запроса — включает lc=ru, cc=ru."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "products": [{"nutriments": {"energy-kcal_100g": 100}}],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get.return_value = mock_response
        service._client = mock_client

        await service._search("куриная грудка")

        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert call_args[0][0] == OFF_SEARCH_URL
        params = call_args[1]["params"]
        assert params["search_terms"] == "куриная грудка"
        assert params["json"] == 1
        assert params["page_size"] == SEARCH_PAGE_SIZE
        assert params["fields"] == OFF_FIELDS
        assert params["lc"] == "ru"
        assert params["cc"] == "ru"

    @pytest.mark.asyncio
    async def test_search_fallback_no_nutrition(self, service: NutritionService) -> None:
        """Fallback на глобальный поиск, если РФ не имеет КБЖУ."""
        # Первый ответ (РФ) — без КБЖУ
        resp_ru = MagicMock()
        resp_ru.json.return_value = {
            "products": [{"product_name": "Test", "nutriments": {}}],
        }
        resp_ru.raise_for_status = MagicMock()

        # Второй ответ (глобальный) — с КБЖУ
        resp_global = MagicMock()
        resp_global.json.return_value = {
            "products": [{"product_name": "Potato", "nutriments": {"energy-kcal_100g": 77}}],
        }
        resp_global.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get.side_effect = [resp_ru, resp_global]
        service._client = mock_client

        result = await service._search("картофель")

        assert mock_client.get.call_count == 2
        # Второй вызов не должен содержать cc
        second_params = mock_client.get.call_args_list[1][1]["params"]
        assert "cc" not in second_params
        assert second_params["lc"] == "ru"
        # Результат из глобального поиска
        assert result[0]["product_name"] == "Potato"

    @pytest.mark.asyncio
    async def test_search_no_fallback_when_nutrition_found(self, service: NutritionService) -> None:
        """Не делает fallback, если РФ-поиск вернул КБЖУ."""
        resp_ru = MagicMock()
        resp_ru.json.return_value = {
            "products": [
                {"product_name": "Борщ", "nutriments": {"energy-kcal_100g": 50}},
            ],
        }
        resp_ru.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get.return_value = resp_ru
        service._client = mock_client

        result = await service._search("борщ")

        mock_client.get.assert_called_once()  # Только 1 вызов
        assert result[0]["product_name"] == "Борщ"


# ---- Тесты промпта ----


class TestNutritionTool:
    """Тесты описания инструмента nutrition_lookup."""

    def test_tool_name(self) -> None:
        """Имя инструмента."""
        from vkuswill_bot.services.prompts import NUTRITION_TOOL

        assert NUTRITION_TOOL["name"] == "nutrition_lookup"

    def test_query_required(self) -> None:
        """query — обязательный параметр."""
        from vkuswill_bot.services.prompts import NUTRITION_TOOL

        assert "query" in NUTRITION_TOOL["parameters"]["required"]

    def test_description_mentions_russian(self) -> None:
        """Описание упоминает русский язык."""
        from vkuswill_bot.services.prompts import NUTRITION_TOOL

        desc = NUTRITION_TOOL["description"]
        assert "РУССКОМ" in desc or "русском" in desc

    def test_description_mentions_kbzhu(self) -> None:
        """Описание упоминает КБЖУ."""
        from vkuswill_bot.services.prompts import NUTRITION_TOOL

        desc = NUTRITION_TOOL["description"]
        assert "КБЖУ" in desc


# ---- Тесты NUTRIENT_KEYS ----


class TestNutrientKeys:
    """Тесты маппинга нутриентов."""

    def test_contains_basic_nutrients(self) -> None:
        """Маппинг содержит основные нутриенты."""
        assert "calories" in _NUTRIENT_KEYS
        assert "protein" in _NUTRIENT_KEYS
        assert "fat" in _NUTRIENT_KEYS
        assert "carbs" in _NUTRIENT_KEYS

    def test_keys_match_off_format(self) -> None:
        """Ключи соответствуют формату Open Food Facts (_100g)."""
        for key in _NUTRIENT_KEYS.values():
            assert key.endswith("_100g"), f"{key} должен заканчиваться на _100g"


# ---- Тесты close ----


class TestServiceLifecycle:
    """Тесты жизненного цикла сервиса."""

    @pytest.mark.asyncio
    async def test_close_no_client(self, service: NutritionService) -> None:
        """close() без клиента не вызывает ошибку."""
        await service.close()

    @pytest.mark.asyncio
    async def test_close_with_client(self, service: NutritionService) -> None:
        """close() закрывает HTTP-клиент."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        service._client = mock_client

        await service.close()
        mock_client.aclose.assert_called_once()
        assert service._client is None

    def test_no_api_key_needed(self) -> None:
        """Сервис создаётся без параметров (API key не нужен)."""
        svc = NutritionService()
        assert svc._client is None
