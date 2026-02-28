"""Unit-тесты для recipe_pantry/recipe_parsing/recipe_matching/recipe_runtime."""

from __future__ import annotations

from vkuswill_bot.agents.pantry_tags import (
    PANTRY_TAG_PEPPER,
    PANTRY_TAG_SALT,
    PANTRY_TAG_SUGAR,
)
from vkuswill_bot.agents.recipe_matching import match_requested_ingredient
from vkuswill_bot.agents.recipe_pantry import (
    detect_pantry_tag_for_ingredient,
    extract_explicit_pantry_requests,
    has_explicit_egg_pack_request,
    looks_like_pepper_vegetable,
    normalize_text,
)
from vkuswill_bot.agents.recipe_parsing import parse_quantity_hint
from vkuswill_bot.agents.recipe_runtime import (
    filter_recipe_ingredients_list,
)


# ── normalize_text ────────────────────────────────────────────────


class TestNormalizeText:
    def test_lowercase_and_strip(self) -> None:
        assert normalize_text("  Привет Мир  ") == "привет мир"

    def test_replaces_yo(self) -> None:
        assert normalize_text("Ёлка") == "елка"

    def test_empty(self) -> None:
        assert normalize_text("") == ""


# ── looks_like_pepper_vegetable ───────────────────────────────────


class TestLooksLikePepperVegetable:
    def test_bolgarskiy(self) -> None:
        assert looks_like_pepper_vegetable("Перец болгарский красный") is True

    def test_chili(self) -> None:
        assert looks_like_pepper_vegetable("перец чили") is True

    def test_ground_pepper_is_not_vegetable(self) -> None:
        assert looks_like_pepper_vegetable("перец чёрный молотый") is False

    def test_no_pepper(self) -> None:
        assert looks_like_pepper_vegetable("морковь") is False


# ── detect_pantry_tag_for_ingredient ──────────────────────────────


class TestDetectPantryTag:
    def test_salt(self) -> None:
        assert detect_pantry_tag_for_ingredient({"name": "Соль"}) == PANTRY_TAG_SALT

    def test_sugar(self) -> None:
        assert detect_pantry_tag_for_ingredient({"name": "Сахар"}) == PANTRY_TAG_SUGAR

    def test_pepper_spice(self) -> None:
        assert detect_pantry_tag_for_ingredient({"name": "перец молотый"}) == PANTRY_TAG_PEPPER

    def test_pepper_vegetable_not_pantry(self) -> None:
        assert detect_pantry_tag_for_ingredient({"name": "перец болгарский"}) is None

    def test_regular_ingredient(self) -> None:
        assert detect_pantry_tag_for_ingredient({"name": "Молоко"}) is None

    def test_empty_name(self) -> None:
        assert detect_pantry_tag_for_ingredient({"name": ""}) is None


# ── extract_explicit_pantry_requests ──────────────────────────────


class TestExtractExplicitPantryRequests:
    def test_salt_in_text(self) -> None:
        result = extract_explicit_pantry_requests("добавь соль и сахар")
        assert PANTRY_TAG_SALT in result
        assert PANTRY_TAG_SUGAR in result

    def test_no_pantry(self) -> None:
        assert extract_explicit_pantry_requests("собери корзину для борща") == set()


# ── has_explicit_egg_pack_request ─────────────────────────────────


class TestHasExplicitEggPackRequest:
    def test_egg_pack_detected(self) -> None:
        assert has_explicit_egg_pack_request("2 упаковки яиц") is True
        assert has_explicit_egg_pack_request("десяток яиц") is True

    def test_plain_egg_not_pack(self) -> None:
        assert has_explicit_egg_pack_request("3 яйца") is False

    def test_no_eggs_at_all(self) -> None:
        assert has_explicit_egg_pack_request("молоко и хлеб") is False


# ── parse_quantity_hint ───────────────────────────────────────────


class TestParseQuantityHint:
    def test_simple_kg(self) -> None:
        result = parse_quantity_hint("2 кг")
        assert result is not None
        quantity, unit, _ = result
        assert quantity == 2.0
        assert unit == "кг"

    def test_grams(self) -> None:
        result = parse_quantity_hint("500 г")
        assert result is not None
        assert result[0] == 500.0
        assert result[1] == "г"

    def test_range_takes_max(self) -> None:
        result = parse_quantity_hint("2-3 шт")
        assert result is not None
        assert result[0] == 3.0

    def test_no_quantity(self) -> None:
        assert parse_quantity_hint("молоко") is None

    def test_empty_string(self) -> None:
        assert parse_quantity_hint("") is None


# ── match_requested_ingredient ────────────────────────────────────


class TestMatchRequestedIngredient:
    def test_matches_by_name(self) -> None:
        product = {"xml_id": 1, "name": "Молоко 3.2%"}
        ingredients = [{"name": "молоко", "quantity": 1, "unit": "л", "search_query": "молоко"}]
        result = match_requested_ingredient(
            product=product,
            xml_id=1,
            requested_ingredients=ingredients,
            search_query_by_xml_id=None,
        )
        assert result is not None
        assert result["name"] == "молоко"

    def test_no_match(self) -> None:
        product = {"xml_id": 1, "name": "Хлеб"}
        ingredients = [{"name": "молоко", "quantity": 1, "unit": "л", "search_query": "молоко"}]
        result = match_requested_ingredient(
            product=product,
            xml_id=1,
            requested_ingredients=ingredients,
            search_query_by_xml_id=None,
        )
        assert result is None

    def test_matches_by_search_query_xml_id(self) -> None:
        product = {"xml_id": 42, "name": "Сыр Маасдам"}
        ingredients = [{"name": "сыр", "quantity": 200, "unit": "г", "search_query": "сыр"}]
        result = match_requested_ingredient(
            product=product,
            xml_id=42,
            requested_ingredients=ingredients,
            search_query_by_xml_id={42: "сыр"},
        )
        assert result is not None


# ── filter_recipe_ingredients_list ────────────────────────────────


class TestFilterRecipeIngredientsList:
    def test_removes_salt_by_default(self) -> None:
        ingredients = [
            {"name": "Соль", "quantity": 1, "unit": "щепотка"},
            {"name": "Молоко", "quantity": 500, "unit": "мл"},
        ]
        filtered, removed = filter_recipe_ingredients_list(
            ingredients=ingredients,
            explicit_pantry_requests=set(),
        )
        assert len(filtered) == 1
        assert filtered[0]["name"] == "Молоко"
        assert "Соль" in removed

    def test_keeps_salt_if_explicitly_requested(self) -> None:
        ingredients = [
            {"name": "Соль морская", "quantity": 1, "unit": "шт"},
            {"name": "Молоко", "quantity": 500, "unit": "мл"},
        ]
        filtered, removed = filter_recipe_ingredients_list(
            ingredients=ingredients,
            explicit_pantry_requests={PANTRY_TAG_SALT},
        )
        assert len(filtered) == 2
        assert not removed

    def test_keeps_bell_pepper(self) -> None:
        ingredients = [
            {"name": "перец болгарский", "quantity": 1, "unit": "шт"},
        ]
        filtered, removed = filter_recipe_ingredients_list(
            ingredients=ingredients,
            explicit_pantry_requests=set(),
        )
        assert len(filtered) == 1
        assert not removed
