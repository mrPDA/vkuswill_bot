"""Unit-тесты для vkuswill_bot.agents.tool_preprocessor."""

from __future__ import annotations

from typing import Any

import json

import pytest

from vkuswill_bot.agents.tool_preprocessor import (
    apply_preferences_to_query,
    collect_requested_products_snapshot,
    normalize_recipe_search_args,
    preprocess_tool_args,
    restore_previous_quantities_for_additive_update,
)
from vkuswill_bot.agents.tool_preprocessor_search import inject_preference_mismatch_hint


# ── apply_preferences_to_query ────────────────────────────────────


class TestApplyPreferencesToQuery:
    def test_appends_preference(self) -> None:
        result = apply_preferences_to_query("молоко", {"молоко": "безлактозное"})
        assert result == "молоко безлактозное"

    def test_replaces_when_query_in_preference(self) -> None:
        result = apply_preferences_to_query("молоко", {"молоко": "молоко безлактозное"})
        assert result == "молоко безлактозное"

    def test_no_matching_preference(self) -> None:
        result = apply_preferences_to_query("хлеб", {"молоко": "безлактозное"})
        assert result == "хлеб"

    def test_empty_prefs(self) -> None:
        assert apply_preferences_to_query("хлеб", {}) == "хлеб"

    def test_empty_query(self) -> None:
        assert apply_preferences_to_query("", {"молоко": "безлактозное"}) == ""


# ── preprocess_tool_args: cart ────────────────────────────────────


class TestPreprocessToolArgsCart:
    def test_egg_quantity_capped_to_packs(self) -> None:
        """25 яиц → ceil(25/10) = 3 упаковки."""
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 1, "q": 25}]},
            product_index={1: {"xml_id": 1, "name": "Яйцо куриное С1", "unit": "шт"}},
        )
        products = result["products"]
        assert products[0]["q"] == 3

    def test_single_egg_stays_one(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 1, "q": 1}]},
            product_index={1: {"xml_id": 1, "name": "Яйца С2", "unit": "шт"}},
        )
        assert result["products"][0]["q"] == 1

    def test_discrete_unit_ceiled(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 2, "q": 2.5}]},
            product_index={2: {"xml_id": 2, "name": "Сметана", "unit": "шт"}},
        )
        assert result["products"][0]["q"] == 3

    def test_kg_product_rounded(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 3, "q": 0.35}]},
            product_index={3: {"xml_id": 3, "name": "Яблоки", "unit": "кг"}},
        )
        assert result["products"][0]["q"] == 0.4

    def test_explicit_egg_pack_skips_normalization(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 1, "q": 25}]},
            product_index={1: {"xml_id": 1, "name": "Яйца", "unit": "шт"}},
            explicit_egg_pack_request=True,
        )
        assert result["products"][0]["q"] == 25


# ── preprocess_tool_args: search ──────────────────────────────────


class TestPreprocessToolArgsSearch:
    def test_strips_page_param(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_products_search",
            {"q": "молоко", "page": 2},
        )
        assert "page" not in result
        assert result["q"] == "молоко"

    def test_applies_preferences(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_products_search",
            {"q": "молоко"},
            user_preferences={"молоко": "безлактозное"},
        )
        assert "безлактозное" in result["q"]

    def test_strips_quantity_from_search_query(self) -> None:
        """BUG-7: 'яйца 30 штук' should become 'яйца'."""
        result = preprocess_tool_args(
            "vkusvill_products_search",
            {"q": "яйца 30 штук"},
        )
        assert "30" not in result["q"]
        assert "штук" not in result["q"]
        assert "яйца" in result["q"]

    def test_strips_quantity_kg_from_search_query(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_products_search",
            {"q": "картофель 2.5 кг"},
        )
        assert "2.5" not in result["q"]
        assert "кг" not in result["q"]
        assert "картофель" in result["q"]

    def test_unknown_tool_passthrough(self) -> None:
        args = {"foo": "bar"}
        assert preprocess_tool_args("unknown_tool", args) is args


# ── collect_requested_products_snapshot ────────────────────────────


class TestCollectRequestedProductsSnapshot:
    def test_basic_snapshot(self) -> None:
        result = collect_requested_products_snapshot(
            {"products": [{"xml_id": 1, "q": 2}]},
        )
        assert len(result) == 1
        assert result[0]["xml_id"] == 1

    def test_no_products_returns_empty(self) -> None:
        assert collect_requested_products_snapshot({}) == []

    def test_egg_quantity_normalized(self) -> None:
        result = collect_requested_products_snapshot(
            {"products": [{"xml_id": 1, "q": 15}]},
            product_index={1: {"xml_id": 1, "name": "Яйцо С1", "unit": "шт"}},
        )
        assert result[0]["q"] == 2  # ceil(15/10)

    def test_kg_quantity_rounded(self) -> None:
        result = collect_requested_products_snapshot(
            {"products": [{"xml_id": 2, "q": 0.35}]},
            product_index={2: {"xml_id": 2, "name": "Яблоки", "unit": "кг"}},
        )
        assert result[0]["q"] == 0.4


# ── restore_previous_quantities_for_additive_update ───────────────


class TestRestorePreviousQuantities:
    def test_restores_quantity_on_additive_intent(self) -> None:
        result = restore_previous_quantities_for_additive_update(
            tool_name="vkusvill_cart_link_create",
            tool_args={"products": [{"xml_id": 1, "q": 1}]},
            user_text="добавь ещё молоко",
            previous_products=[{"xml_id": 1, "q": 3}],
        )
        assert result["products"][0]["q"] == 3

    def test_no_restore_without_additive_intent(self) -> None:
        result = restore_previous_quantities_for_additive_update(
            tool_name="vkusvill_cart_link_create",
            tool_args={"products": [{"xml_id": 1, "q": 1}]},
            user_text="собери корзину",
            previous_products=[{"xml_id": 1, "q": 3}],
        )
        assert result["products"][0]["q"] == 1

    def test_non_cart_tool_passthrough(self) -> None:
        args = {"q": "молоко"}
        result = restore_previous_quantities_for_additive_update(
            tool_name="vkusvill_products_search",
            tool_args=args,
            user_text="добавь",
            previous_products=[],
        )
        assert result is args

    def test_skips_explicit_overrides(self) -> None:
        result = restore_previous_quantities_for_additive_update(
            tool_name="vkusvill_cart_link_create",
            tool_args={"products": [{"xml_id": 1, "q": 1}]},
            user_text="добавь ещё",
            previous_products=[{"xml_id": 1, "q": 5}],
            requested_quantity_overrides={1: 2.0},
        )
        # xml_id=1 is in overrides → should not be restored.
        assert result["products"][0]["q"] == 1

    def test_restores_on_qty_explosion(self) -> None:
        """REGR-01: LLM sets q=14 for chicken when user only asked to add rice."""
        result = restore_previous_quantities_for_additive_update(
            tool_name="vkusvill_cart_link_create",
            tool_args={
                "products": [
                    {"xml_id": 1, "q": 4},
                    {"xml_id": 2, "q": 14},
                    {"xml_id": 3, "q": 1},
                ]
            },
            user_text="ещё добавь рис и гречку",
            previous_products=[
                {"xml_id": 1, "q": 1},
                {"xml_id": 2, "q": 1},
            ],
        )
        prods = result["products"]
        assert prods[0]["q"] == 1
        assert prods[1]["q"] == 1
        assert prods[2]["q"] == 1

    def test_keeps_intentional_small_increase(self) -> None:
        """User says 'ещё добавь 3 пачки молока' — LLM correctly sets q=3."""
        result = restore_previous_quantities_for_additive_update(
            tool_name="vkusvill_cart_link_create",
            tool_args={"products": [{"xml_id": 1, "q": 3}]},
            user_text="ещё добавь 3 пачки молока",
            previous_products=[{"xml_id": 1, "q": 1}],
        )
        assert result["products"][0]["q"] == 3


class TestHallucinatedXmlIdFilter:
    """Filter out xml_ids not found in product_index (LLM hallucinations)."""

    def test_drops_unknown_xml_ids(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {
                "products": [
                    {"xml_id": 111, "q": 1},
                    {"xml_id": 12345, "q": 1},
                    {"xml_id": 222, "q": 2},
                    {"xml_id": 99999, "q": 1},
                ]
            },
            product_index={
                111: {"xml_id": 111, "name": "Молоко", "unit": "шт"},
                222: {"xml_id": 222, "name": "Хлеб", "unit": "шт"},
            },
        )
        products = result["products"]
        xml_ids = [p["xml_id"] for p in products]
        assert xml_ids == [111, 222]

    def test_keeps_all_when_all_valid(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 1, "q": 2}, {"xml_id": 2, "q": 3}]},
            product_index={
                1: {"xml_id": 1, "name": "Рис", "unit": "шт"},
                2: {"xml_id": 2, "name": "Гречка", "unit": "шт"},
            },
        )
        assert len(result["products"]) == 2

    def test_no_filter_when_product_index_empty(self) -> None:
        """Without product_index, no filtering is applied."""
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 12345, "q": 1}]},
        )
        assert len(result["products"]) == 1


class TestMaxQtyCap:
    """NEG02: max qty safeguard caps absurd quantities after unit normalization."""

    def test_caps_absurd_qty_at_20(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 1, "q": 30}, {"xml_id": 2, "q": 15}]},
            product_index={
                1: {"xml_id": 1, "name": "Орехи", "unit": "шт"},
                2: {"xml_id": 2, "name": "Мёд", "unit": "шт"},
            },
        )
        assert result["products"][0]["q"] == 20
        assert result["products"][1]["q"] == 15

    def test_normal_qty_unchanged(self) -> None:
        result = preprocess_tool_args(
            "vkusvill_cart_link_create",
            {"products": [{"xml_id": 1, "q": 6}, {"xml_id": 2, "q": 12}]},
            product_index={
                1: {"xml_id": 1, "name": "Рис", "unit": "шт"},
                2: {"xml_id": 2, "name": "Молоко", "unit": "шт"},
            },
        )
        assert result["products"][0]["q"] == 6
        assert result["products"][1]["q"] == 12


# ── normalize_recipe_search_args ──────────────────────────────────


class TestNormalizeRecipeSearchArgs:
    def test_no_ingredients_passthrough(self) -> None:
        args = {"query": "борщ"}
        assert normalize_recipe_search_args(args) is args

    def test_fills_search_query_from_name(self) -> None:
        args = {
            "ingredients": [{"name": "Свекла"}],
        }
        result = normalize_recipe_search_args(args)
        assert result["ingredients"][0].get("search_query")

    def test_non_dict_rows_preserved(self) -> None:
        args: dict[str, Any] = {"ingredients": ["raw_string", {"name": "Морковь"}]}
        result = normalize_recipe_search_args(args)
        assert result["ingredients"][0] == "raw_string"


# ── inject_preference_mismatch_hint ──────────────────────────────


class TestInjectPreferenceMismatchHint:
    def test_adds_warning_when_top_result_differs_from_preference(self) -> None:
        raw = json.dumps(
            {
                "ok": True,
                "data": {
                    "meta": {"q": "безлактозное молоко 1,5 ВкусВилл", "total": 100},
                    "items": [
                        {"xml_id": 54730, "name": "Молоко Parmalat Comfort Безлактозное 1,8% 1 л"},
                    ],
                },
            },
            ensure_ascii=False,
        )
        result = inject_preference_mismatch_hint(
            raw,
            user_preferences={"молоко": "безлактозное молоко 1,5 ВкусВилл"},
        )
        parsed = json.loads(result)
        warning = parsed["data"].get("relevance_warning", "")
        assert "любимый продукт" in warning.lower() or "не найден точно" in warning

    def test_no_warning_when_result_matches_preference(self) -> None:
        raw = json.dumps(
            {
                "ok": True,
                "data": {
                    "meta": {"q": "безлактозное молоко 1,5 ВкусВилл", "total": 10},
                    "items": [
                        {"xml_id": 999, "name": "Молоко безлактозное 1,5% ВкусВилл 1 л"},
                    ],
                },
            },
            ensure_ascii=False,
        )
        result = inject_preference_mismatch_hint(
            raw,
            user_preferences={"молоко": "безлактозное молоко 1,5 ВкусВилл"},
        )
        parsed = json.loads(result)
        assert "relevance_warning" not in parsed.get("data", {})

    def test_no_warning_without_preferences(self) -> None:
        raw = json.dumps({"ok": True, "data": {"meta": {"q": "молоко"}, "items": []}})
        result = inject_preference_mismatch_hint(raw, user_preferences=None)
        assert result == raw

    def test_no_warning_when_query_unrelated_to_preference(self) -> None:
        raw = json.dumps(
            {
                "ok": True,
                "data": {
                    "meta": {"q": "хлеб бородинский", "total": 5},
                    "items": [{"xml_id": 1, "name": "Хлеб Бородинский"}],
                },
            },
            ensure_ascii=False,
        )
        result = inject_preference_mismatch_hint(
            raw,
            user_preferences={"молоко": "безлактозное молоко 1,5 ВкусВилл"},
        )
        parsed = json.loads(result)
        assert "relevance_warning" not in parsed.get("data", {})


class TestNormalizeColloquialNumerals:
    """BUG-15: разговорные числительные → цифры."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("полтора кило картошки", "1.5 кг картошки"),
            ("полкило моркови", "0.5 кг моркови"),
            ("пару литров молока", "2 литров молока"),
            ("тройку яблок", "3 шт яблок"),
            ("пяток яиц", "5 шт яиц"),
            ("десяток яиц", "10 шт яиц"),
            ("дюжину булочек", "12 шт булочек"),
            ("четверть кило масла", "0.25 кг масла"),
            ("полтора литра молока", "1.5 л молока"),
            ("без числительных", "без числительных"),
        ],
    )
    def test_normalize(self, text: str, expected: str) -> None:
        from vkuswill_bot.services.tool_input_normalizers import normalize_colloquial_numerals

        assert normalize_colloquial_numerals(text) == expected


class TestNormalizeMultilingualGroceryText:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "I need milk, bread, eggs and cheese please",
                "молоко, хлеб, яйца и сыр",
            ),
            ("moloko 2 litra, hleb, maslo", "молоко 2 литра, хлеб, масло"),
            ("сыр и хлеб", "сыр и хлеб"),
            (
                "yogurt oil onion garlic apple sugar flour carrot cucumber pepper beef pork fish salmon oats pasta coffee tea",
                "йогурт растительное масло лук чеснок яблоко сахар мука морковь огурец перец говядина свинина рыба лосось овсянка макароны кофе чай"
            ),
        ],
    )
    def test_normalize(self, text: str, expected: str) -> None:
        from vkuswill_bot.services.tool_input_normalizers import (
            normalize_multilingual_grocery_text,
        )

        assert normalize_multilingual_grocery_text(text) == expected
