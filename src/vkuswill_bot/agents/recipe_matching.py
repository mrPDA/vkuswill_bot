"""Matching and quantity override helpers for recipe-driven cart generation."""

from __future__ import annotations

import contextlib
from typing import Any

from vkuswill_bot.agents.recipe_quantity_calculator import RecipeQuantityCalculator
from vkuswill_bot.agents.tool_result_compactor import tokenize_query_terms


def match_requested_ingredient(
    *,
    product: dict[str, Any],
    xml_id: int,
    requested_ingredients: list[dict[str, Any]],
    search_query_by_xml_id: dict[int, str] | None,
) -> dict[str, Any] | None:
    """Find the best requested ingredient match for a selected product."""
    if not requested_ingredients:
        return None

    def _score_pair(left: str, right: str) -> float:
        left_tokens = tokenize_query_terms(left)
        right_tokens = tokenize_query_terms(right)
        if not left_tokens or not right_tokens:
            return 0.0
        left_set = set(left_tokens)
        right_set = set(right_tokens)
        inter = len(left_set & right_set)
        if inter <= 0:
            return 0.0
        return inter / max(len(left_set), len(right_set))

    best_score = 0.0
    best_row: dict[str, Any] | None = None
    product_name = str(product.get("name", "")).strip()
    query_hint = ""
    if search_query_by_xml_id is not None:
        query_hint = str(search_query_by_xml_id.get(xml_id, "")).strip()

    for row in requested_ingredients:
        if not isinstance(row, dict):
            continue
        row_name = str(row.get("name", "")).strip()
        row_query = str(row.get("search_query", "")).strip()
        score = max(
            _score_pair(row_query, query_hint),
            _score_pair(row_name, query_hint),
            _score_pair(row_query, product_name),
            _score_pair(row_name, product_name),
        )
        if score > best_score:
            best_score = score
            best_row = row

    if best_row is None or best_score < 0.2:
        return None
    return best_row


def apply_requested_quantity_overrides(
    snapshot: list[dict[str, Any]],
    overrides: dict[int, float],
) -> list[dict[str, Any]]:
    """Apply requested quantity overrides to a cart snapshot."""
    if not snapshot or not overrides:
        return snapshot
    updated: list[dict[str, Any]] = []
    for row in snapshot:
        if not isinstance(row, dict):
            continue
        current = dict(row)
        xml_id_raw = current.get("xml_id")
        if isinstance(xml_id_raw, bool):
            updated.append(current)
            continue
        with contextlib.suppress(TypeError, ValueError):
            xml_id = int(xml_id_raw)
            if xml_id in overrides:
                current["q"] = overrides[xml_id]
        updated.append(current)
    return updated


def calculate_requested_and_purchase_quantity(
    *,
    ingredient: dict[str, Any],
    product: dict[str, Any],
) -> tuple[float, float]:
    """Calculate requested and purchase quantities for matched ingredient/product pair."""
    return RecipeQuantityCalculator.calculate_requested_and_purchase_q(
        ingredient=ingredient,
        item=product,
    )
