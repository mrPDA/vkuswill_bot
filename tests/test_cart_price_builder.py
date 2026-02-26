"""Unit-тесты для vkuswill_bot.agents.cart_price_builder."""

from __future__ import annotations

from typing import Any


from vkuswill_bot.agents.cart_price_builder import (
    aggregate_products_by_xml_id,
    ensure_cart_price_summary,
    format_quantity_text,
    normalize_product_row,
    round_kilogram_quantity,
)


# ── normalize_product_row ─────────────────────────────────────────


class TestNormalizeProductRow:
    def test_basic_product(self) -> None:
        row = {"xml_id": 123, "name": "Молоко", "unit": "шт", "price": 89.0}
        result = normalize_product_row(row)
        assert result == {"xml_id": 123, "name": "Молоко", "unit": "шт", "price": 89.0}

    def test_string_xml_id(self) -> None:
        row = {"xml_id": "456", "name": "Хлеб", "unit": "шт"}
        result = normalize_product_row(row)
        assert result is not None
        assert result["xml_id"] == 456

    def test_bool_xml_id_returns_none(self) -> None:
        assert normalize_product_row({"xml_id": True}) is None

    def test_missing_xml_id_returns_none(self) -> None:
        assert normalize_product_row({"name": "No ID"}) is None

    def test_defaults_name_and_unit(self) -> None:
        row = {"xml_id": 1}
        result = normalize_product_row(row)
        assert result is not None
        assert result["name"] == "Товар 1"
        assert result["unit"] == "шт"
        assert "price" not in result

    def test_dict_price_extracted(self) -> None:
        row = {"xml_id": 1, "name": "X", "unit": "шт", "price": {"current": 150}}
        result = normalize_product_row(row)
        assert result is not None
        assert result["price"] == 150.0

    def test_uses_id_field_fallback(self) -> None:
        row = {"id": 99, "name": "Fallback ID"}
        result = normalize_product_row(row)
        assert result is not None
        assert result["xml_id"] == 99


# ── aggregate_products_by_xml_id ──────────────────────────────────


class TestAggregateProductsByXmlId:
    def test_single_item(self) -> None:
        products = [{"xml_id": 1, "q": 2}]
        totals, order = aggregate_products_by_xml_id(products)
        assert totals == {1: 2.0}
        assert order == [1]

    def test_duplicate_xml_ids_summed(self) -> None:
        products = [{"xml_id": 1, "q": 2}, {"xml_id": 1, "q": 3}]
        totals, order = aggregate_products_by_xml_id(products)
        assert totals == {1: 5.0}
        assert order == [1]

    def test_preserves_order(self) -> None:
        products = [{"xml_id": 3, "q": 1}, {"xml_id": 1, "q": 1}, {"xml_id": 2, "q": 1}]
        _, order = aggregate_products_by_xml_id(products)
        assert order == [3, 1, 2]

    def test_skips_non_dict_items(self) -> None:
        products: list[Any] = [{"xml_id": 1, "q": 1}, "bad", None, 42]
        _totals, order = aggregate_products_by_xml_id(products)
        assert order == [1]

    def test_skips_bool_xml_id(self) -> None:
        products = [{"xml_id": True, "q": 1}]
        totals, order = aggregate_products_by_xml_id(products)
        assert totals == {}
        assert order == []

    def test_default_quantity_is_one(self) -> None:
        products = [{"xml_id": 5}]
        totals, _ = aggregate_products_by_xml_id(products)
        assert totals[5] == 1.0

    def test_negative_quantity_defaults_to_one(self) -> None:
        products = [{"xml_id": 5, "q": -3}]
        totals, _ = aggregate_products_by_xml_id(products)
        assert totals[5] == 1.0


# ── round_kilogram_quantity ───────────────────────────────────────


class TestRoundKilogramQuantity:
    def test_rounds_up_to_100g(self) -> None:
        assert round_kilogram_quantity(0.25) == 0.3
        assert round_kilogram_quantity(0.31) == 0.4

    def test_exact_100g_stays(self) -> None:
        assert round_kilogram_quantity(0.5) == 0.5
        assert round_kilogram_quantity(1.0) == 1.0

    def test_minimum_is_100g(self) -> None:
        assert round_kilogram_quantity(0.01) == 0.1
        assert round_kilogram_quantity(0.0) == 0.1

    def test_negative_defaults_to_minimum(self) -> None:
        assert round_kilogram_quantity(-1.0) == 0.1


# ── format_quantity_text ──────────────────────────────────────────


class TestFormatQuantityText:
    def test_discrete_units_ceiled(self) -> None:
        assert format_quantity_text(2.3, unit="шт") == "3"
        assert format_quantity_text(1.0, unit="уп") == "1"

    def test_kg_rounded(self) -> None:
        assert format_quantity_text(0.25, unit="кг") == "0.3"
        assert format_quantity_text(1.0, unit="кг") == "1.0"

    def test_integer_quantity_no_unit(self) -> None:
        assert format_quantity_text(5.0) == "5"

    def test_fractional_quantity_no_unit(self) -> None:
        assert format_quantity_text(1.5) == "1.5"

    def test_minimum_discrete_is_one(self) -> None:
        assert format_quantity_text(0.1, unit="шт") == "1"


# ── ensure_cart_price_summary ─────────────────────────────────────


class TestEnsureCartPriceSummary:
    def test_builds_summary_for_priced_products(self) -> None:
        cart_data: dict[str, Any] = {
            "products": [{"xml_id": 1, "q": 2}],
        }
        product_index = {1: {"xml_id": 1, "name": "Молоко", "unit": "шт", "price": 80.0}}
        ensure_cart_price_summary(cart_data=cart_data, product_index=product_index)
        summary = cart_data["price_summary"]
        assert summary["total"] == 160.0
        assert summary["count"] == 1
        assert "Итого:" in summary["total_text"]

    def test_skips_if_summary_already_complete(self) -> None:
        cart_data: dict[str, Any] = {
            "products": [{"xml_id": 1, "q": 1}],
            "price_summary": {
                "items": ["- Молоко x 1 = 80 руб"],
                "total_text": "Итого: 80.00 руб",
            },
        }
        ensure_cart_price_summary(cart_data=cart_data, product_index={})
        # Should not overwrite existing complete summary.
        assert cart_data["price_summary"]["total_text"] == "Итого: 80.00 руб"

    def test_no_products_does_nothing(self) -> None:
        cart_data: dict[str, Any] = {}
        ensure_cart_price_summary(cart_data=cart_data, product_index={})
        assert "price_summary" not in cart_data

    def test_unpriced_products_show_placeholder(self) -> None:
        cart_data: dict[str, Any] = {
            "products": [{"xml_id": 1, "q": 1}],
        }
        # No price in product_index.
        product_index = {1: {"xml_id": 1, "name": "Молоко", "unit": "шт"}}
        ensure_cart_price_summary(cart_data=cart_data, product_index=product_index)
        summary = cart_data["price_summary"]
        assert "цена уточняется" in summary["items"][0]
        assert "будет рассчитано" in summary["total_text"]

    def test_dual_pricing_when_quantities_differ(self) -> None:
        cart_data: dict[str, Any] = {
            "products": [{"xml_id": 1, "q": 2}],
            "requested_products": [{"xml_id": 1, "q": 1}],
        }
        product_index = {1: {"xml_id": 1, "name": "Сметана", "unit": "шт", "price": 100.0}}
        ensure_cart_price_summary(cart_data=cart_data, product_index=product_index)
        summary = cart_data["price_summary"]
        assert summary["dual_pricing"] is True
        assert summary["purchase_total"] == 200.0
        assert summary["recipe_total"] == 100.0
        assert summary["overbuy_total"] == 100.0
