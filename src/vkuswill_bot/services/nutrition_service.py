"""Сервис получения КБЖУ через USDA FoodData Central API.

Предоставляет данные о калориях, белках, жирах и углеводах
для продуктов по названию (на английском языке).

USDA FDC API: https://fdc.nal.usda.gov/api-guide
- Бесплатный ключ (регистрация на api.data.gov)
- 1000 запросов/час
- Comprehensive nutritional data
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# USDA FoodData Central API
USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"

# Таймауты (секунды)
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 15

# Количество результатов поиска
SEARCH_PAGE_SIZE = 5

# Типы данных USDA: Foundation и SR Legacy дают generic продукты (не branded)
USDA_DATA_TYPES = ["Foundation", "SR Legacy"]

# Nutrient IDs в USDA (стабильные)
NUTRIENT_MAP = {
    1008: "calories",  # Energy (kcal)
    1003: "protein",  # Protein (g)
    1004: "fat",  # Total lipid / fat (g)
    1005: "carbs",  # Carbohydrate, by difference (g)
    1079: "fiber",  # Fiber, total dietary (g)
    2000: "sugars",  # Sugars, total (g)
}


class NutritionService:
    """Сервис КБЖУ на базе USDA FoodData Central.

    Принимает название продукта на английском, ищет в USDA,
    возвращает КБЖУ на 100 г.
    """

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Получить или создать HTTP-клиент с keep-alive."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def lookup(self, args: dict) -> str:
        """Найти КБЖУ продукта по названию.

        Args:
            args: {"query": "chicken breast", "portion_g": 100}

        Returns:
            JSON-строка с КБЖУ или ошибкой.
        """
        query = args.get("query", "").strip()
        if not query:
            return json.dumps(
                {"ok": False, "error": "Не указано название продукта (query)"},
                ensure_ascii=False,
            )

        portion_g = args.get("portion_g", 100)
        if not isinstance(portion_g, int | float) or portion_g <= 0:
            portion_g = 100

        try:
            results = await self._search_usda(query)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.error("USDA API error: %s", e)
            return json.dumps(
                {"ok": False, "error": f"Ошибка USDA API: {e}"},
                ensure_ascii=False,
            )

        if not results:
            return json.dumps(
                {
                    "ok": True,
                    "data": {
                        "query": query,
                        "found": False,
                        "message": (
                            f"Продукт '{query}' не найден в базе USDA. "
                            "Попробуй уточнить название на английском."
                        ),
                    },
                },
                ensure_ascii=False,
            )

        # Форматируем результаты
        items = []
        for food in results:
            nutrients = self._extract_nutrients(food, portion_g)
            items.append(
                {
                    "name": food.get("description", ""),
                    "data_type": food.get("dataType", ""),
                    "per_portion_g": portion_g,
                    "nutrients": nutrients,
                }
            )

        return json.dumps(
            {
                "ok": True,
                "data": {
                    "query": query,
                    "found": True,
                    "portion_g": portion_g,
                    "items": items,
                    "hint": (
                        "Данные КБЖУ приблизительные (база USDA). "
                        "Точные значения могут отличаться для конкретных "
                        "продуктов ВкусВилл. Рекомендуй проверить на упаковке."
                    ),
                },
            },
            ensure_ascii=False,
        )

    async def _search_usda(self, query: str) -> list[dict[str, Any]]:
        """Поиск продуктов в USDA FoodData Central.

        Returns:
            Список найденных продуктов (до SEARCH_PAGE_SIZE).
        """
        client = await self._get_client()
        params: dict[str, Any] = {
            "api_key": self._api_key,
            "query": query,
            "pageSize": SEARCH_PAGE_SIZE,
            "dataType": ",".join(USDA_DATA_TYPES),
        }
        response = await client.get(f"{USDA_BASE_URL}/foods/search", params=params)
        response.raise_for_status()
        data = response.json()
        return data.get("foods", [])

    @staticmethod
    def _extract_nutrients(
        food: dict[str, Any],
        portion_g: int | float = 100,
    ) -> dict[str, float | None]:
        """Извлечь КБЖУ из ответа USDA и пересчитать на порцию.

        USDA всегда отдаёт значения на 100 г. Если portion_g != 100,
        пропорционально пересчитываем.
        """
        raw: dict[str, float | None] = dict.fromkeys(NUTRIENT_MAP.values())
        factor = portion_g / 100.0

        for nutrient in food.get("foodNutrients", []):
            nid = nutrient.get("nutrientId")
            if nid in NUTRIENT_MAP:
                value = nutrient.get("value")
                if value is not None:
                    raw[NUTRIENT_MAP[nid]] = round(value * factor, 1)

        return raw
