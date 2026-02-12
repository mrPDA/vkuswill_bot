"""Тесты для NutritionService (USDA FoodData Central API)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from vkuswill_bot.services.nutrition_service import (
    NUTRIENT_MAP,
    SEARCH_PAGE_SIZE,
    USDA_BASE_URL,
    USDA_DATA_TYPES,
    NutritionService,
)


# ---- Фикстуры ----


@pytest.fixture
def service() -> NutritionService:
    """Создать NutritionService с тестовым ключом."""
    return NutritionService(api_key="TEST_KEY")


@pytest.fixture
def mock_usda_response() -> dict:
    """Пример ответа USDA FoodData Central для chicken breast."""
    return {
        "foods": [
            {
                "fdcId": 171077,
                "description": "Chicken, broilers or fryers, breast, skinless, boneless",
                "dataType": "SR Legacy",
                "foodNutrients": [
                    {
                        "nutrientId": 1008,
                        "nutrientName": "Energy",
                        "value": 165.0,
                        "unitName": "KCAL",
                    },
                    {"nutrientId": 1003, "nutrientName": "Protein", "value": 31.0, "unitName": "G"},
                    {
                        "nutrientId": 1004,
                        "nutrientName": "Total lipid (fat)",
                        "value": 3.6,
                        "unitName": "G",
                    },
                    {
                        "nutrientId": 1005,
                        "nutrientName": "Carbohydrate, by difference",
                        "value": 0.0,
                        "unitName": "G",
                    },
                    {
                        "nutrientId": 1079,
                        "nutrientName": "Fiber, total dietary",
                        "value": 0.0,
                        "unitName": "G",
                    },
                    {
                        "nutrientId": 2000,
                        "nutrientName": "Sugars, total",
                        "value": 0.0,
                        "unitName": "G",
                    },
                ],
            },
            {
                "fdcId": 171078,
                "description": "Chicken, broilers or fryers, breast, with skin",
                "dataType": "Foundation",
                "foodNutrients": [
                    {
                        "nutrientId": 1008,
                        "nutrientName": "Energy",
                        "value": 197.0,
                        "unitName": "KCAL",
                    },
                    {"nutrientId": 1003, "nutrientName": "Protein", "value": 29.8, "unitName": "G"},
                    {
                        "nutrientId": 1004,
                        "nutrientName": "Total lipid (fat)",
                        "value": 7.8,
                        "unitName": "G",
                    },
                    {
                        "nutrientId": 1005,
                        "nutrientName": "Carbohydrate, by difference",
                        "value": 0.0,
                        "unitName": "G",
                    },
                ],
            },
        ],
    }


@pytest.fixture
def mock_empty_response() -> dict:
    """Пустой ответ USDA (ничего не найдено)."""
    return {"foods": []}


# ---- Тесты _extract_nutrients ----


class TestExtractNutrients:
    """Тесты извлечения нутриентов из ответа USDA."""

    def test_extract_basic_nutrients(self, mock_usda_response: dict) -> None:
        """Извлечение всех КБЖУ на 100 г."""
        food = mock_usda_response["foods"][0]
        result = NutritionService._extract_nutrients(food)
        assert result["calories"] == 165.0
        assert result["protein"] == 31.0
        assert result["fat"] == 3.6
        assert result["carbs"] == 0.0

    def test_extract_with_portion(self, mock_usda_response: dict) -> None:
        """Пересчёт КБЖУ на другую порцию (200 г)."""
        food = mock_usda_response["foods"][0]
        result = NutritionService._extract_nutrients(food, portion_g=200)
        assert result["calories"] == 330.0  # 165 * 2
        assert result["protein"] == 62.0  # 31 * 2
        assert result["fat"] == 7.2  # 3.6 * 2

    def test_extract_with_small_portion(self, mock_usda_response: dict) -> None:
        """Пересчёт КБЖУ на маленькую порцию (50 г)."""
        food = mock_usda_response["foods"][0]
        result = NutritionService._extract_nutrients(food, portion_g=50)
        assert result["calories"] == 82.5  # 165 * 0.5
        assert result["protein"] == 15.5  # 31 * 0.5

    def test_missing_nutrients(self) -> None:
        """Отсутствующие нутриенты возвращаются как None."""
        food = {"foodNutrients": [{"nutrientId": 1008, "value": 100.0}]}
        result = NutritionService._extract_nutrients(food)
        assert result["calories"] == 100.0
        assert result["protein"] is None
        assert result["fat"] is None
        assert result["carbs"] is None

    def test_empty_nutrients(self) -> None:
        """Пустой список нутриентов."""
        food = {"foodNutrients": []}
        result = NutritionService._extract_nutrients(food)
        assert all(v is None for v in result.values())

    def test_no_nutrients_key(self) -> None:
        """Нет ключа foodNutrients."""
        food = {}
        result = NutritionService._extract_nutrients(food)
        assert all(v is None for v in result.values())


# ---- Тесты lookup ----


class TestLookup:
    """Тесты метода lookup (основной API)."""

    @pytest.mark.asyncio
    async def test_lookup_success(
        self,
        service: NutritionService,
        mock_usda_response: dict,
    ) -> None:
        """Успешный поиск КБЖУ."""
        with patch.object(service, "_search_usda", new_callable=AsyncMock) as mock:
            mock.return_value = mock_usda_response["foods"]
            result = json.loads(await service.lookup({"query": "chicken breast"}))

        assert result["ok"] is True
        data = result["data"]
        assert data["query"] == "chicken breast"
        assert data["found"] is True
        assert len(data["items"]) == 2
        assert data["items"][0]["name"] == "Chicken, broilers or fryers, breast, skinless, boneless"
        assert data["items"][0]["nutrients"]["calories"] == 165.0
        assert "hint" in data

    @pytest.mark.asyncio
    async def test_lookup_not_found(
        self,
        service: NutritionService,
    ) -> None:
        """Продукт не найден."""
        with patch.object(service, "_search_usda", new_callable=AsyncMock) as mock:
            mock.return_value = []
            result = json.loads(await service.lookup({"query": "alien food"}))

        assert result["ok"] is True
        data = result["data"]
        assert data["found"] is False
        assert "не найден" in data["message"]

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
    async def test_lookup_custom_portion(
        self,
        service: NutritionService,
        mock_usda_response: dict,
    ) -> None:
        """Поиск с кастомной порцией."""
        with patch.object(service, "_search_usda", new_callable=AsyncMock) as mock:
            mock.return_value = mock_usda_response["foods"]
            result = json.loads(await service.lookup({"query": "chicken breast", "portion_g": 200}))

        data = result["data"]
        assert data["portion_g"] == 200
        assert data["items"][0]["per_portion_g"] == 200
        # 165 kcal на 100 г → 330 kcal на 200 г
        assert data["items"][0]["nutrients"]["calories"] == 330.0

    @pytest.mark.asyncio
    async def test_lookup_invalid_portion(
        self,
        service: NutritionService,
        mock_usda_response: dict,
    ) -> None:
        """Невалидная порция — используется 100 г."""
        with patch.object(service, "_search_usda", new_callable=AsyncMock) as mock:
            mock.return_value = mock_usda_response["foods"]
            result = json.loads(await service.lookup({"query": "chicken breast", "portion_g": -50}))

        assert result["data"]["portion_g"] == 100

    @pytest.mark.asyncio
    async def test_lookup_api_error(self, service: NutritionService) -> None:
        """Ошибка USDA API."""
        with patch.object(service, "_search_usda", new_callable=AsyncMock) as mock:
            mock.side_effect = httpx.HTTPError("Connection failed")
            result = json.loads(await service.lookup({"query": "chicken"}))

        assert result["ok"] is False
        assert "USDA API" in result["error"]

    @pytest.mark.asyncio
    async def test_lookup_timeout(self, service: NutritionService) -> None:
        """Таймаут USDA API."""
        with patch.object(service, "_search_usda", new_callable=AsyncMock) as mock:
            mock.side_effect = httpx.TimeoutException("Read timed out")
            result = json.loads(await service.lookup({"query": "rice"}))

        assert result["ok"] is False


# ---- Тесты _search_usda ----


class TestSearchUsda:
    """Тесты прямого вызова USDA API."""

    @pytest.mark.asyncio
    async def test_search_params(self, service: NutritionService) -> None:
        """Проверка параметров запроса к USDA."""
        from unittest.mock import MagicMock

        mock_response = MagicMock()
        mock_response.json.return_value = {"foods": []}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        mock_client.get.return_value = mock_response
        service._client = mock_client

        await service._search_usda("chicken breast")

        mock_client.get.assert_called_once()
        call_args = mock_client.get.call_args
        assert call_args[0][0] == f"{USDA_BASE_URL}/foods/search"
        params = call_args[1]["params"]
        assert params["api_key"] == "TEST_KEY"
        assert params["query"] == "chicken breast"
        assert params["pageSize"] == SEARCH_PAGE_SIZE
        assert params["dataType"] == ",".join(USDA_DATA_TYPES)


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

    def test_portion_optional(self) -> None:
        """portion_g — опциональный."""
        from vkuswill_bot.services.prompts import NUTRITION_TOOL

        required = NUTRITION_TOOL["parameters"]["required"]
        assert "portion_g" not in required

    def test_description_mentions_english(self) -> None:
        """Описание упоминает английский язык."""
        from vkuswill_bot.services.prompts import NUTRITION_TOOL

        desc = NUTRITION_TOOL["description"]
        assert "АНГЛИЙСКОМ" in desc or "english" in desc.lower()

    def test_description_mentions_kbzhu(self) -> None:
        """Описание упоминает КБЖУ."""
        from vkuswill_bot.services.prompts import NUTRITION_TOOL

        desc = NUTRITION_TOOL["description"]
        assert "КБЖУ" in desc


# ---- Тесты NUTRIENT_MAP ----


class TestNutrientMap:
    """Тесты маппинга нутриентов."""

    def test_contains_basic_nutrients(self) -> None:
        """Маппинг содержит основные нутриенты."""
        values = set(NUTRIENT_MAP.values())
        assert "calories" in values
        assert "protein" in values
        assert "fat" in values
        assert "carbs" in values

    def test_nutrient_ids_are_ints(self) -> None:
        """Ключи — целые числа (nutrient IDs)."""
        assert all(isinstance(k, int) for k in NUTRIENT_MAP)


# ---- Тесты close ----


class TestServiceLifecycle:
    """Тесты жизненного цикла сервиса."""

    @pytest.mark.asyncio
    async def test_close_no_client(self, service: NutritionService) -> None:
        """close() без клиента не вызывает ошибку."""
        await service.close()  # не должна упасть

    @pytest.mark.asyncio
    async def test_close_with_client(self, service: NutritionService) -> None:
        """close() закрывает HTTP-клиент."""
        mock_client = AsyncMock(spec=httpx.AsyncClient)
        mock_client.is_closed = False
        service._client = mock_client

        await service.close()
        mock_client.aclose.assert_called_once()
        assert service._client is None
