"""Tests for multi_course_executor: dish parsing and integration."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from vkuswill_bot.agents.multi_course_executor import (
    extract_dishes_from_text,
    run_multi_course_turn,
    _collect_all_ingredients,
    _build_product_index,
    _render_multi_course_response,
)


# ---------------------------------------------------------------------------
# extract_dishes_from_text
# ---------------------------------------------------------------------------


class TestExtractDishesFromText:
    def test_classic_4_courses(self):
        text = (
            "мне нужно: завтрак для двоих — каша овсяная с фруктами; "
            "обед — борщ на 4 порции; ужин — стейк с овощами гриль; "
            "плюс десерт — чизкейк"
        )
        dishes = extract_dishes_from_text(text)
        assert len(dishes) == 4
        names = [d["name"] for d in dishes]
        assert "каша овсяная с фруктами" in names
        assert "борщ" in names
        assert "стейк с овощами гриль" in names
        assert "чизкейк" in names

    def test_3_courses_with_global_servings(self):
        text = (
            "собери для завтрака овсянку с ягодами, на обед — куриный суп, "
            "на ужин — пасту карбонара, всё на двоих"
        )
        dishes = extract_dishes_from_text(text)
        assert len(dishes) == 3
        for d in dishes:
            assert d["servings"] == 2

    def test_per_dish_servings_override(self):
        text = "завтрак — каша; обед — борщ на 4 порции; ужин — стейк"
        dishes = extract_dishes_from_text(text)
        assert len(dishes) == 3
        borsch = next(d for d in dishes if "борщ" in d["name"])
        assert borsch["servings"] == 4

    def test_returns_empty_for_single_dish(self):
        text = "собери корзину для борща на 4 порции"
        assert extract_dishes_from_text(text) == []

    def test_returns_empty_for_no_meal_types(self):
        text = "мне нужны яйца, молоко и хлеб"
        assert extract_dishes_from_text(text) == []

    def test_dash_separator(self):
        text = "завтрак - омлет, обед - суп куриный"
        dishes = extract_dishes_from_text(text)
        assert len(dishes) == 2

    def test_colon_separator(self):
        text = "завтрак: овсянка; обед: борщ; ужин: паста"
        dishes = extract_dishes_from_text(text)
        assert len(dishes) == 3

    def test_minimal_input(self):
        text = "завтрак каша обед суп"
        dishes = extract_dishes_from_text(text)
        assert len(dishes) == 2

    def test_polldnik_and_perekus(self):
        text = "полдник — йогурт с гранолой, перекус — фрукты"
        dishes = extract_dishes_from_text(text)
        assert len(dishes) == 2

    def test_global_servings_with_digits(self):
        text = "завтрак — каша; обед — суп; всё для 3 человек"
        dishes = extract_dishes_from_text(text)
        assert len(dishes) == 2
        for d in dishes:
            assert d["servings"] == 3


# ---------------------------------------------------------------------------
# _build_product_index
# ---------------------------------------------------------------------------


class TestBuildProductIndex:
    def test_builds_index_from_products(self):
        products = [
            {"xml_id": 123, "name": "Молоко", "unit": "шт", "q": 2},
            {"xml_id": 456, "name": "Масло", "unit": "шт", "q": 1},
        ]
        index = _build_product_index(products)
        assert 123 in index
        assert 456 in index
        assert index[123]["name"] == "Молоко"

    def test_skips_invalid_products(self):
        products = [
            {"name": "No xml_id"},
            {"xml_id": None, "name": "None id"},
            {"xml_id": 789, "name": "Valid", "unit": "кг"},
        ]
        index = _build_product_index(products)
        assert len(index) == 1
        assert 789 in index


# ---------------------------------------------------------------------------
# _render_multi_course_response
# ---------------------------------------------------------------------------


class TestRenderResponse:
    def test_renders_all_dishes(self):
        dishes = [
            {"name": "Каша", "servings": 2},
            {"name": "Борщ", "servings": 4},
        ]
        by_dish = {
            "Каша": [{"name": "овсянка"}, {"name": "молоко"}],
            "Борщ": [{"name": "свёкла"}, {"name": "мясо"}, {"name": "картофель"}],
        }
        cart_data = {
            "link": "https://example.com/cart",
            "price_summary": {
                "items": ["- Овсянка x 1 = 80.00 руб"],
                "total_text": "Итого: 500.00 руб",
            },
        }
        result = _render_multi_course_response(
            dishes=dishes,
            by_dish=by_dish,
            cart_data=cart_data,
            not_found=["сметана"],
        )
        assert "2 блюд" in result
        assert "Каша" in result
        assert "Борщ" in result
        assert "Не найдено" in result
        assert "сметана" in result


# ---------------------------------------------------------------------------
# _collect_all_ingredients (unit test with mocks)
# ---------------------------------------------------------------------------


class TestCollectAllIngredients:
    @pytest.mark.asyncio
    async def test_collects_from_all_dishes(self):
        agent = MagicMock()
        agent._call_mcp_tool = AsyncMock(
            return_value=json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {"name": "молоко", "search_query": "молоко", "quantity": 1, "unit": "л"},
                    ],
                }
            ),
        )
        state = SimpleNamespace(mcp_call_cache={})
        dishes = [
            {"name": "Каша", "servings": 2},
            {"name": "Борщ", "servings": 4},
        ]

        flat, by_dish = await _collect_all_ingredients(
            agent=agent,
            state=state,
            user_id=1,
            llm_provider="gigachat",
            dishes=dishes,
            deadline_at=9999999999.0,
        )

        assert len(flat) == 2
        assert "Каша" in by_dish
        assert "Борщ" in by_dish
        assert agent._call_mcp_tool.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_partial_failures(self):
        call_count = 0

        async def _mock_tool(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("MCP timeout")
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {"name": "свёкла", "search_query": "свёкла", "quantity": 1, "unit": "шт"},
                    ],
                }
            )

        agent = MagicMock()
        agent._call_mcp_tool = _mock_tool
        state = SimpleNamespace(mcp_call_cache={})
        dishes = [
            {"name": "Каша", "servings": 2},
            {"name": "Борщ", "servings": 4},
        ]

        _flat, by_dish = await _collect_all_ingredients(
            agent=agent,
            state=state,
            user_id=1,
            llm_provider="gigachat",
            dishes=dishes,
            deadline_at=9999999999.0,
        )

        assert len(by_dish) == 1
        assert "Борщ" in by_dish


# ---------------------------------------------------------------------------
# run_multi_course_turn (integration-level with mocks)
# ---------------------------------------------------------------------------


class TestRunMultiCourseTurn:
    @pytest.mark.asyncio
    async def test_falls_back_when_less_than_2_dishes(self):
        agent = MagicMock()
        state = SimpleNamespace(multi_course_expected=1)
        fallback = AsyncMock(return_value="fallback response")
        progress = AsyncMock()

        result = await run_multi_course_turn(
            agent=agent,
            state=state,
            user_id=1,
            text="собери борщ",
            llm_provider="gigachat",
            trace=None,
            on_progress=progress,
            fallback_to_standard_turn=fallback,
        )

        assert result == "fallback response"
        fallback.assert_called_once()
