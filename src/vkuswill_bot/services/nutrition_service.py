"""Сервис получения КБЖУ через Open Food Facts API.

Предоставляет данные о калориях, белках, жирах и углеводах
для продуктов по названию. Поддерживает русский и английский.

Open Food Facts API: https://openfoodfacts.github.io/openfoodfacts-server/api/
- Полностью бесплатный, без API-ключа
- Доступен из РФ
- ~30 000 русских продуктов (включая ВкусВилл)
- Рекомендуемый rate limit: не более 10 запросов/минуту
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Open Food Facts Search API
OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"

# Поля, которые запрашиваем (минимизируем трафик)
OFF_FIELDS = "product_name,brands,nutriments,serving_size,nutrition_grades"

# Таймауты (секунды)
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 15

# Количество результатов поиска
SEARCH_PAGE_SIZE = 5

# Regex для удаления веса/объёма из названий (200 г, 1 л, 600 мл, 82,5% и т.п.)
_WEIGHT_RE = re.compile(
    r",?\s*\d+[.,]?\d*\s*(?:г|кг|мл|л|мг|шт)\b"
    r"|,?\s*\d+[.,]\d+\s*%"
    r"|,?\s*\d+\s*%",
    re.IGNORECASE,
)

# Ключи нутриентов в Open Food Facts
_NUTRIENT_KEYS = {
    "calories": "energy-kcal_100g",
    "protein": "proteins_100g",
    "fat": "fat_100g",
    "carbs": "carbohydrates_100g",
    "fiber": "fiber_100g",
    "sugars": "sugars_100g",
    "salt": "salt_100g",
}

# User-Agent (рекомендация Open Food Facts)
USER_AGENT = "VkusVillBot/1.0 (Telegram bot; contact@example.com)"


class NutritionService:
    """Сервис КБЖУ на базе Open Food Facts.

    Принимает название продукта (на русском или английском),
    ищет в Open Food Facts, возвращает КБЖУ на 100 г.
    """

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Получить или создать HTTP-клиент с keep-alive."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT, read=READ_TIMEOUT),
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _normalize_query(raw: str) -> str:
        """Нормализовать запрос: убрать HTML-entities, вес, проценты.

        Примеры:
            'Молоко 3,2%, 1&nbsp;л' → 'Молоко'
            'Масло сливочное 82,5%, 200&nbsp;г' → 'Масло сливочное'
            'Плов с курицей, 600&nbsp;г' → 'Плов с курицей'
            'Филе грудки цыпленка-бройлера' → 'Филе грудки цыпленка-бройлера'
        """
        # 1. Декодировать HTML-entities (&nbsp; → пробел, &amp; → &, и т.д.)
        q = html.unescape(raw)
        # 2. Убрать вес/объём/проценты
        q = _WEIGHT_RE.sub("", q)
        # 3. Убрать висячие запятые и лишние пробелы
        q = re.sub(r"\s*,\s*$", "", q)
        q = re.sub(r"\s+", " ", q).strip()
        return q

    async def lookup(self, args: dict) -> str:
        """Найти КБЖУ продукта по названию.

        Args:
            args: {"query": "борщ"} или {"query": "chicken breast"}

        Returns:
            JSON-строка с КБЖУ или ошибкой.
        """
        raw_query = args.get("query", "").strip()
        if not raw_query:
            return json.dumps(
                {"ok": False, "error": "Не указано название продукта (query)"},
                ensure_ascii=False,
            )

        query = self._normalize_query(raw_query)
        if not query:
            query = raw_query  # fallback если нормализация съела всё

        try:
            results = await self._search(query)
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.error("Open Food Facts API error: %s", e)
            return json.dumps(
                {"ok": False, "error": f"Ошибка Open Food Facts API: {e}"},
                ensure_ascii=False,
            )

        # Фильтруем продукты без КБЖУ
        valid = [r for r in results if self._has_nutrition(r)]
        if not valid:
            return json.dumps(
                {
                    "ok": True,
                    "data": {
                        "query": query,
                        "found": False,
                        "message": (
                            f"КБЖУ для '{query}' не найдены в Open Food Facts. "
                            "Попробуй уточнить название или проверь на упаковке товара."
                        ),
                    },
                },
                ensure_ascii=False,
            )

        items = []
        for product in valid:
            nutrients = self._extract_nutrients(product)
            item: dict[str, Any] = {
                "name": product.get("product_name", ""),
                "nutrients_per_100g": nutrients,
            }
            brand = product.get("brands", "")
            if brand:
                item["brand"] = brand
            grade = product.get("nutrition_grades", "")
            if grade:
                item["nutriscore"] = grade.upper()
            serving = product.get("serving_size", "")
            if serving:
                item["serving_size"] = serving
            items.append(item)

        return json.dumps(
            {
                "ok": True,
                "data": {
                    "query": query,
                    "found": True,
                    "count": len(items),
                    "items": items,
                    "hint": (
                        "Данные КБЖУ из Open Food Facts (открытая база). "
                        "Значения приблизительные — точные данные на упаковке товара."
                    ),
                },
            },
            ensure_ascii=False,
        )

    async def _search(self, query: str) -> list[dict[str, Any]]:
        """Поиск продуктов в Open Food Facts.

        Сначала ищем среди российских продуктов (lc=ru, cc=ru).
        Если не нашли — делаем fallback-поиск без фильтра страны.

        Returns:
            Список найденных продуктов (до SEARCH_PAGE_SIZE).
        """
        client = await self._get_client()
        # Основной поиск: только российские продукты
        params: dict[str, str | int] = {
            "search_terms": query,
            "search_simple": 1,
            "action": "process",
            "json": 1,
            "page_size": SEARCH_PAGE_SIZE,
            "fields": OFF_FIELDS,
            "lc": "ru",
            "cc": "ru",
        }
        response = await client.get(OFF_SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json()
        products = data.get("products", [])

        # Fallback: если среди РФ-продуктов нет КБЖУ — ищем глобально
        if not any(self._has_nutrition(p) for p in products):
            params.pop("cc", None)
            response = await client.get(OFF_SEARCH_URL, params=params)
            response.raise_for_status()
            data = response.json()
            products = data.get("products", [])

        return products

    @staticmethod
    def _has_nutrition(product: dict) -> bool:
        """Проверить, есть ли хотя бы калории в продукте."""
        n = product.get("nutriments", {})
        if not isinstance(n, dict):
            return False
        kcal = n.get("energy-kcal_100g")
        return kcal is not None and kcal != ""

    @staticmethod
    def _extract_nutrients(product: dict) -> dict[str, float | None]:
        """Извлечь КБЖУ из продукта Open Food Facts."""
        n = product.get("nutriments", {})
        if not isinstance(n, dict):
            return dict.fromkeys(_NUTRIENT_KEYS)

        result: dict[str, float | None] = {}
        for name, key in _NUTRIENT_KEYS.items():
            value = n.get(key)
            if value is not None and value != "":
                try:
                    result[name] = round(float(value), 1)
                except (ValueError, TypeError):
                    result[name] = None
            else:
                result[name] = None
        return result
