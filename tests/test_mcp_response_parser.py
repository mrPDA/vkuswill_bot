"""Тесты для vkuswill_bot.agents.mcp_response_parser."""

from __future__ import annotations

import json

from vkuswill_bot.agents.mcp_response_parser import (
    extract_cart_data,
    extract_recipe_products_from_history,
    extract_search_items,
    has_recipe_search_candidates,
    parse_json_payload,
)


# ── parse_json_payload ────────────────────────────────────────────────


class TestParseJsonPayload:
    def test_dict_passthrough(self):
        d = {"a": 1}
        assert parse_json_payload(d) is d

    def test_list_passthrough(self):
        lst = [1, 2]
        assert parse_json_payload(lst) is lst

    def test_none_passthrough(self):
        assert parse_json_payload(None) is None

    def test_int_passthrough(self):
        assert parse_json_payload(42) == 42

    def test_plain_json_string(self):
        assert parse_json_payload('{"key": "value"}') == {"key": "value"}

    def test_json_array_string(self):
        assert parse_json_payload("[1, 2, 3]") == [1, 2, 3]

    def test_fenced_json(self):
        text = '```json\n{"x": 1}\n```'
        assert parse_json_payload(text) == {"x": 1}

    def test_fenced_no_lang(self):
        text = '```\n{"x": 2}\n```'
        assert parse_json_payload(text) == {"x": 2}

    def test_invalid_json_returns_empty_dict(self):
        assert parse_json_payload("not json at all") == {}

    def test_whitespace_around_json(self):
        assert parse_json_payload('  {"a": 1}  ') == {"a": 1}

    def test_empty_string_returns_empty_dict(self):
        assert parse_json_payload("") == {}


# ── extract_search_items ──────────────────────────────────────────────


class TestExtractSearchItems:
    def test_non_dict_returns_empty(self):
        assert extract_search_items("string") == []
        assert extract_search_items(None) == []
        assert extract_search_items([1, 2]) == []

    def test_data_products(self):
        payload = {"data": {"products": [{"xml_id": 1}, {"xml_id": 2}]}}
        assert extract_search_items(payload) == [{"xml_id": 1}, {"xml_id": 2}]

    def test_data_products_filters_non_dict(self):
        payload = {"data": {"products": [{"xml_id": 1}, "skip", 42]}}
        assert extract_search_items(payload) == [{"xml_id": 1}]

    def test_data_items(self):
        payload = {"data": {"items": [{"xml_id": 10}]}}
        assert extract_search_items(payload) == [{"xml_id": 10}]

    def test_data_xml_id_int(self):
        payload = {"data": {"xml_id": 100, "name": "Молоко"}}
        assert extract_search_items(payload) == [{"xml_id": 100, "name": "Молоко"}]

    def test_data_xml_id_str(self):
        payload = {"data": {"xml_id": "200", "price": 99}}
        assert extract_search_items(payload) == [{"xml_id": "200", "price": 99}]

    def test_item_key(self):
        payload = {"item": {"xml_id": 5}}
        assert extract_search_items(payload) == [{"xml_id": 5}]

    def test_items_key(self):
        payload = {"items": [{"xml_id": 1}, {"xml_id": 2}]}
        assert extract_search_items(payload) == [{"xml_id": 1}, {"xml_id": 2}]

    def test_products_key(self):
        payload = {"products": [{"xml_id": 3}]}
        assert extract_search_items(payload) == [{"xml_id": 3}]

    def test_found_key(self):
        payload = {
            "found": [
                {"xml_id": 10, "name": "Сыр", "price": 200, "unit": "кг"},
                {"xml_id": 20, "name": "Хлеб"},
            ]
        }
        result = extract_search_items(payload)
        assert len(result) == 2
        assert result[0] == {"xml_id": 10, "name": "Сыр", "price": 200, "unit": "кг"}
        assert result[1] == {"xml_id": 20, "name": "Хлеб", "price": None, "unit": "шт"}

    def test_found_skips_rows_without_xml_id(self):
        payload = {"found": [{"name": "Без id"}, {"xml_id": 1, "name": "OK"}]}
        result = extract_search_items(payload)
        assert len(result) == 1
        assert result[0]["xml_id"] == 1

    def test_found_skips_non_dict(self):
        payload = {"found": ["skip", {"xml_id": 7, "name": "OK"}]}
        result = extract_search_items(payload)
        assert len(result) == 1

    def test_results_best_match(self):
        payload = {
            "results": [
                {"best_match": {"xml_id": 50, "name": "Яблоко"}},
                {"best_match": {"xml_id": 60, "name": "Груша"}},
            ]
        }
        result = extract_search_items(payload)
        assert len(result) == 2
        assert result[0]["xml_id"] == 50

    def test_results_skips_no_best_match(self):
        payload = {"results": [{"other": "data"}, {"best_match": {"xml_id": 1}}]}
        result = extract_search_items(payload)
        assert len(result) == 1

    def test_empty_dict(self):
        assert extract_search_items({}) == []

    def test_data_products_takes_priority_over_items(self):
        payload = {"data": {"products": [{"xml_id": 1}]}, "items": [{"xml_id": 2}]}
        assert extract_search_items(payload) == [{"xml_id": 1}]


# ── extract_cart_data ─────────────────────────────────────────────────


class TestExtractCartData:
    def test_wrong_tool_name(self):
        result = extract_cart_data(
            tool_name="vkusvill_products_search",
            tool_result='{"ok": true, "data": {"link": "https://example.com"}}',
        )
        assert result is None

    def test_valid_cart_response(self):
        payload = {"ok": True, "data": {"link": "https://vkusvill.ru/cart/123", "total": 500}}
        result = extract_cart_data(
            tool_name="vkusvill_cart_link_create",
            tool_result=json.dumps(payload),
        )
        assert result is not None
        assert result["link"] == "https://vkusvill.ru/cart/123"
        assert result["total"] == 500

    def test_not_ok(self):
        payload = {"ok": False, "data": {"link": "https://vkusvill.ru/cart/123"}}
        assert (
            extract_cart_data(
                tool_name="vkusvill_cart_link_create",
                tool_result=json.dumps(payload),
            )
            is None
        )

    def test_missing_data(self):
        assert (
            extract_cart_data(
                tool_name="vkusvill_cart_link_create",
                tool_result=json.dumps({"ok": True}),
            )
            is None
        )

    def test_data_not_dict(self):
        assert (
            extract_cart_data(
                tool_name="vkusvill_cart_link_create",
                tool_result=json.dumps({"ok": True, "data": "string"}),
            )
            is None
        )

    def test_empty_link(self):
        payload = {"ok": True, "data": {"link": ""}}
        assert (
            extract_cart_data(
                tool_name="vkusvill_cart_link_create",
                tool_result=json.dumps(payload),
            )
            is None
        )

    def test_whitespace_link(self):
        payload = {"ok": True, "data": {"link": "   "}}
        assert (
            extract_cart_data(
                tool_name="vkusvill_cart_link_create",
                tool_result=json.dumps(payload),
            )
            is None
        )

    def test_missing_link(self):
        payload = {"ok": True, "data": {"total": 100}}
        assert (
            extract_cart_data(
                tool_name="vkusvill_cart_link_create",
                tool_result=json.dumps(payload),
            )
            is None
        )

    def test_invalid_json(self):
        assert (
            extract_cart_data(
                tool_name="vkusvill_cart_link_create",
                tool_result="not json",
            )
            is None
        )


# ── extract_recipe_products_from_history ──────────────────────────────


class TestExtractRecipeProductsFromHistory:
    def _make_tool_msg(self, content: dict) -> dict:
        return {"role": "tool", "name": "recipe_search", "content": json.dumps(content)}

    def test_empty_history(self):
        products, not_found = extract_recipe_products_from_history([])
        assert products == []
        assert not_found == 0

    def test_no_recipe_search(self):
        history = [
            {"role": "user", "content": "привет"},
            {"role": "assistant", "content": "здравствуйте"},
        ]
        products, _not_found = extract_recipe_products_from_history(history)
        assert products == []

    def test_found_key(self):
        msg = self._make_tool_msg(
            {
                "found": [
                    {"xml_id": 1, "suggested_q": 2},
                    {"xml_id": 2, "suggested_q": 1},
                ],
                "not_found": ["соль"],
            }
        )
        products, not_found = extract_recipe_products_from_history([msg])
        assert len(products) == 2
        assert not_found == 1
        by_id = {p["xml_id"]: p["q"] for p in products}
        assert by_id[1] == 2.0
        assert by_id[2] == 1.0

    def test_results_best_match_fallback(self):
        msg = self._make_tool_msg(
            {
                "results": [
                    {"best_match": {"xml_id": 10, "suggested_q": 3}},
                    {"best_match": {"xml_id": 20, "suggested_q": 1}},
                ],
            }
        )
        products, _not_found = extract_recipe_products_from_history([msg])
        assert len(products) == 2
        by_id = {p["xml_id"]: p["q"] for p in products}
        assert by_id[10] == 3.0
        assert by_id[20] == 1.0

    def test_duplicate_xml_ids_summed(self):
        msg = self._make_tool_msg(
            {
                "found": [
                    {"xml_id": 5, "suggested_q": 1},
                    {"xml_id": 5, "suggested_q": 2},
                ],
            }
        )
        products, _ = extract_recipe_products_from_history([msg])
        assert len(products) == 1
        assert products[0]["q"] == 3.0

    def test_negative_quantity_defaults_to_one(self):
        msg = self._make_tool_msg(
            {
                "found": [{"xml_id": 1, "suggested_q": -5}],
            }
        )
        products, _ = extract_recipe_products_from_history([msg])
        assert products[0]["q"] == 1.0

    def test_zero_quantity_defaults_to_one(self):
        msg = self._make_tool_msg(
            {
                "found": [{"xml_id": 1, "suggested_q": 0}],
            }
        )
        products, _ = extract_recipe_products_from_history([msg])
        assert products[0]["q"] == 1.0

    def test_missing_suggested_q_defaults_to_one(self):
        msg = self._make_tool_msg(
            {
                "found": [{"xml_id": 1}],
            }
        )
        products, _ = extract_recipe_products_from_history([msg])
        assert products[0]["q"] == 1.0

    def test_bool_xml_id_skipped(self):
        msg = self._make_tool_msg(
            {
                "found": [{"xml_id": True, "suggested_q": 1}],
            }
        )
        products, _ = extract_recipe_products_from_history([msg])
        assert products == []

    def test_non_dict_rows_skipped(self):
        msg = self._make_tool_msg(
            {
                "found": ["not a dict", {"xml_id": 1}],
            }
        )
        products, _ = extract_recipe_products_from_history([msg])
        assert len(products) == 1

    def test_uses_last_recipe_search(self):
        old_msg = self._make_tool_msg({"found": [{"xml_id": 100}]})
        new_msg = self._make_tool_msg({"found": [{"xml_id": 200}]})
        history = [old_msg, {"role": "assistant", "content": "ok"}, new_msg]
        products, _ = extract_recipe_products_from_history(history)
        assert products[0]["xml_id"] == 200

    def test_string_xml_id_parsed(self):
        msg = self._make_tool_msg({"found": [{"xml_id": "42", "suggested_q": 1}]})
        products, _ = extract_recipe_products_from_history([msg])
        assert products[0]["xml_id"] == 42

    def test_non_parseable_xml_id_skipped(self):
        msg = self._make_tool_msg({"found": [{"xml_id": "abc"}]})
        products, _ = extract_recipe_products_from_history([msg])
        assert products == []

    def test_string_quantity_parsed(self):
        msg = self._make_tool_msg({"found": [{"xml_id": 1, "suggested_q": "2.5"}]})
        products, _ = extract_recipe_products_from_history([msg])
        assert products[0]["q"] == 2.5


# ── has_recipe_search_candidates ──────────────────────────────────────


class TestHasRecipeSearchCandidates:
    def test_true_when_products_exist(self):
        msg = {
            "role": "tool",
            "name": "recipe_search",
            "content": json.dumps(
                {
                    "found": [{"xml_id": 1}],
                }
            ),
        }
        assert has_recipe_search_candidates([msg]) is True

    def test_false_when_empty(self):
        assert has_recipe_search_candidates([]) is False

    def test_false_when_no_products(self):
        msg = {
            "role": "tool",
            "name": "recipe_search",
            "content": json.dumps(
                {
                    "found": [],
                }
            ),
        }
        assert has_recipe_search_candidates([msg]) is False
