"""Пакетный поиск товаров для ингредиентов рецепта."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
from collections.abc import Callable, Coroutine
from typing import Any

from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.search_processor import SEARCH_LIMIT, SearchProcessor

logger = logging.getLogger(__name__)


class RecipeSearchService:
    """Пакетный поиск ингредиентов рецепта через MCP."""

    _DISCRETE_UNITS = frozenset({"шт", "уп", "пач", "бут", "бан", "пак"})
    _MICRO_UNITS = frozenset(
        {
            "зубчик",
            "ст.л.",
            "ч.л.",
            "пучок",
            "щепотка",
            "веточка",
            "долька",
            "стебель",
            "лист",
        }
    )
    _MAX_DISCRETE_Q = 5

    # Слова в названии товара, указывающие на нерелевантный
    # для рецептов ассортимент (семена, рассада, корм и т.д.).
    _NON_FOOD_KEYWORDS = (
        "семена",
        "семя",
        "рассада",
        "саженц",
        "саженец",
        "грунт",
        "удобрени",
        "корм для",
        "наполнитель",
        "горшок",
        "кашпо",
    )

    def __init__(
        self,
        mcp_client: VkusvillMCPClient,
        search_processor: SearchProcessor,
        max_concurrency: int = 5,
    ) -> None:
        self._mcp_client = mcp_client
        self._search_processor = search_processor
        self._max_concurrency = max(1, max_concurrency)

    async def search_ingredients(
        self,
        ingredients: list[dict],
        on_found: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> str:
        """Найти товары для всех ингредиентов рецепта параллельно."""
        if not isinstance(ingredients, list) or not ingredients:
            return json.dumps(
                {"ok": False, "error": "Пустой список ingredients"},
                ensure_ascii=False,
            )

        sem = asyncio.Semaphore(self._max_concurrency)
        tasks = [self._search_one(ingredient, sem, on_found=on_found) for ingredient in ingredients]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[dict] = []
        not_found: list[str] = []
        search_log: dict[str, list[int]] = {}

        for ingredient, outcome in zip(ingredients, raw_results, strict=True):
            query = str(ingredient.get("search_query", "")).strip()
            if isinstance(outcome, Exception):
                logger.warning("Ошибка recipe_search для %r: %s", query, outcome)
                if query:
                    not_found.append(query)
                results.append(
                    {
                        "ingredient": ingredient.get("name", query),
                        "search_query": query,
                        "best_match": None,
                        "alternatives": [],
                        "error": str(outcome),
                    }
                )
                continue

            results.append(outcome["result"])
            found_ids = outcome["found_ids"]
            if found_ids:
                search_log[query] = found_ids
            elif query:
                not_found.append(query)

        return json.dumps(
            {
                "ok": True,
                "results": results,
                "not_found": not_found,
                "search_log": search_log,
            },
            ensure_ascii=False,
        )

    async def _search_one(
        self,
        ingredient: dict,
        sem: asyncio.Semaphore,
        on_found: Callable[[], Coroutine[Any, Any, None]] | None = None,
    ) -> dict:
        query = str(ingredient.get("search_query", "")).strip()
        ingredient_name = ingredient.get("name", query)

        if not query:
            if on_found:
                with contextlib.suppress(Exception):
                    await on_found()
            return {
                "result": {
                    "ingredient": ingredient_name,
                    "search_query": query,
                    "best_match": None,
                    "alternatives": [],
                    "error": "Не указан search_query",
                },
                "found_ids": [],
            }

        cleaned_query = self._search_processor.clean_search_query(query)
        args = {"q": cleaned_query, "limit": SEARCH_LIMIT}

        async with sem:
            raw = await self._mcp_client.call_tool("vkusvill_products_search", args)

        # Сохраняем в кэш цен и триммим тяжёлые поля.
        await self._search_processor.cache_prices(raw)
        trimmed = self._search_processor.trim_search_result(raw)
        parsed = self._search_processor.parse_search_items(trimmed)
        if parsed is None:
            return {
                "result": {
                    "ingredient": ingredient_name,
                    "search_query": cleaned_query,
                    "best_match": None,
                    "alternatives": [],
                    "error": "Поиск не вернул items",
                },
                "found_ids": [],
            }

        data, items = parsed
        found_ids: list[int] = [
            item["xml_id"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("xml_id"), int)
        ]

        best_match = None
        alternatives: list[dict] = []
        if items:
            food_items = self._deprioritize_non_food(items)
            best_match = await self._to_match(food_items[0], ingredient)
            alternatives = [
                await self._to_match(item, ingredient)
                for item in food_items[1:4]
                if isinstance(item, dict)
            ]

        result: dict = {
            "ingredient": ingredient_name,
            "search_query": cleaned_query,
            "best_match": best_match,
            "alternatives": alternatives,
        }
        warning = data.get("data", {}).get("relevance_warning")
        if warning:
            result["relevance_warning"] = warning

        if on_found:
            with contextlib.suppress(Exception):
                await on_found()

        return {"result": result, "found_ids": found_ids}

    async def _to_match(self, item: dict, ingredient: dict) -> dict:
        xml_id = item.get("xml_id")
        product_unit = str(item.get("unit", "шт"))
        suggested_q = await self._suggested_q(ingredient, xml_id, product_unit)
        return {
            "xml_id": xml_id,
            "name": item.get("name", ""),
            "price": item.get("price"),
            "unit": product_unit,
            "suggested_q": suggested_q,
        }

    async def _suggested_q(
        self,
        ingredient: dict,
        xml_id: int | None,
        product_unit: str,
    ) -> int | float:
        """Рассчитать рекомендуемое q для vkusvill_cart_link_create."""
        quantity = self._as_float(ingredient.get("quantity")) or 1.0
        unit = str(ingredient.get("unit", "")).lower().strip()
        kg_equivalent = self._as_float(ingredient.get("kg_equivalent"))
        l_equivalent = self._as_float(ingredient.get("l_equivalent"))
        pack_equivalent = self._as_float(ingredient.get("pack_equivalent"))

        if pack_equivalent and pack_equivalent > 0:
            return max(1, math.ceil(pack_equivalent))

        cached = (
            await self._search_processor.price_cache.get(xml_id) if xml_id is not None else None
        )
        weight_grams = cached.weight_grams if cached is not None else None
        product_unit_norm = product_unit.lower().strip()

        if product_unit_norm in self._DISCRETE_UNITS:
            # Вес упаковки известен + эквивалент веса → кол-во упаковок.
            if weight_grams and weight_grams > 0:
                if kg_equivalent and kg_equivalent > 0:
                    q = math.ceil((kg_equivalent * 1000) / weight_grams)
                    return max(1, min(q, self._MAX_DISCRETE_Q))
                if l_equivalent and l_equivalent > 0:
                    q = math.ceil((l_equivalent * 1000) / weight_grams)
                    return max(1, min(q, self._MAX_DISCRETE_Q))
                if unit == "г":
                    q = math.ceil(quantity / weight_grams)
                    return max(1, min(q, self._MAX_DISCRETE_Q))
                if unit == "мл":
                    q = math.ceil(quantity / weight_grams)
                    return max(1, min(q, self._MAX_DISCRETE_Q))
            # Нет weight_grams: микро-единицы (зубчик, ст.л. и т.д.)
            # почти всегда помещаются в 1 упаковку магазинного товара.
            if unit in self._MICRO_UNITS:
                return 1
            q = math.ceil(quantity)
            return max(1, min(q, self._MAX_DISCRETE_Q))

        if product_unit_norm == "кг":
            if kg_equivalent and kg_equivalent > 0:
                return round(kg_equivalent, 3)
            if unit == "г":
                return round(quantity / 1000, 3)
            if unit == "кг":
                return round(quantity, 3)
            return 1.0

        if product_unit_norm == "л":
            if l_equivalent and l_equivalent > 0:
                return round(l_equivalent, 3)
            if unit == "мл":
                return round(quantity / 1000, 3)
            if unit == "л":
                return round(quantity, 3)
            return 1.0

        # Фолбэк для нестандартных единиц.
        if quantity <= 0:
            return 1
        return round(quantity, 3)

    @classmethod
    def _deprioritize_non_food(cls, items: list[dict]) -> list[dict]:
        """Переместить нерелевантные товары (семена, рассада) в конец.

        Если ВСЕ товары нерелевантные — вернуть исходный порядок
        (лучше показать хоть что-то).
        """
        food: list[dict] = []
        non_food: list[dict] = []
        for item in items:
            name_lower = str(item.get("name", "")).lower()
            if any(kw in name_lower for kw in cls._NON_FOOD_KEYWORDS):
                non_food.append(item)
            else:
                food.append(item)
        if not food:
            return items
        return food + non_food

    @staticmethod
    def _as_float(value: object) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return None
        return None
