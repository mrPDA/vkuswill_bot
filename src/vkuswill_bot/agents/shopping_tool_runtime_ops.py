"""Runtime operations for tool-step execution."""

from __future__ import annotations

import copy
import json
from typing import Any

from vkuswill_bot.agents.llm_helpers import assistant_msg, parse_tool_args
from vkuswill_bot.agents.product_index_manager import (
    update_product_index_from_tool_result,
    update_search_query_by_xml_id,
)
from vkuswill_bot.agents.recipe_matching import (
    apply_requested_quantity_overrides,
    calculate_requested_and_purchase_quantity,
    match_requested_ingredient,
)
from vkuswill_bot.agents.recipe_runtime import (
    sanitize_recipe_ingredients_tool_result,
)
from vkuswill_bot.agents.tool_preprocessor import (
    collect_requested_products_snapshot,
    preprocess_tool_args,
    restore_previous_quantities_for_additive_update,
)


def _tool_progress_text(tool_name: str) -> str:
    mapping = {
        "vkusvill_products_search": "\U0001f50d Ищу товары...",
        "vkusvill_cart_link_create": "\U0001f6d2 Формирую корзину...",
        "recipe_ingredients": "\U0001f373 Подбираю рецепт...",
        "recipe_search": "\U0001f50d Ищу продукты по рецепту...",
    }
    return mapping.get(tool_name, "\u2699\ufe0f Обрабатываю запрос...")


def _extract_cart_data_from_result(*, tool_name: str, tool_result: str) -> dict[str, Any] | None:
    if tool_name != "vkusvill_cart_link_create":
        return None
    try:
        payload = json.loads(tool_result)
    except Exception:
        return None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    link = data.get("link")
    if not isinstance(link, str) or not link.strip():
        return None
    return data


async def execute_tool_calls(
    *,
    agent: Any,
    state: Any,
    message: Any,
    tool_calls: list[dict[str, Any]],
    user_id: int,
    text: str,
    llm_provider: str,
    trace: Any | None,
    on_progress: Any,
) -> None:
    state.history.append(assistant_msg(message))
    step_tool_names = [
        str(tool_call.get("name", "")).strip()
        for tool_call in tool_calls
        if str(tool_call.get("name", "")).strip()
    ]
    if (
        state.cart_intent
        and state.cart_data_this_turn is None
        and len(step_tool_names) == 1
        and step_tool_names[0] == "vkusvill_products_search"
    ):
        state.single_search_steps_streak += 1
    else:
        state.single_search_steps_streak = 0

    for tool_call in tool_calls:
        tool_name = str(tool_call.get("name", "")).strip()
        tool_call_id = str(tool_call.get("id", "")).strip()
        raw_tool_args = parse_tool_args(tool_call.get("arguments"))
        raw_tool_args_snapshot = copy.deepcopy(raw_tool_args)
        requested_quantity_overrides: dict[int, float] = {}
        tool_args = preprocess_tool_args(
            tool_name,
            raw_tool_args,
            user_preferences=state.user_preferences,
            product_index=state.product_index_this_turn,
            explicit_egg_pack_request=state.explicit_egg_pack_request,
            requested_ingredients=state.requested_ingredients,
            search_query_by_xml_id=state.search_query_by_xml_id_this_turn,
            requested_quantity_overrides=requested_quantity_overrides,
        )
        apply_requested_ingredient_overrides(
            tool_name=tool_name,
            tool_args=tool_args,
            product_index=state.product_index_this_turn,
            explicit_egg_pack_request=state.explicit_egg_pack_request,
            requested_ingredients=state.requested_ingredients,
            search_query_by_xml_id=state.search_query_by_xml_id_this_turn,
            requested_quantity_overrides=requested_quantity_overrides,
        )
        tool_args = restore_previous_quantities_for_additive_update(
            tool_name=tool_name,
            tool_args=tool_args,
            user_text=text,
            previous_products=state.previous_cart_products,
            requested_quantity_overrides=requested_quantity_overrides,
        )
        requested_products_snapshot = (
            apply_requested_quantity_overrides(
                collect_requested_products_snapshot(
                    raw_tool_args_snapshot,
                    product_index=state.product_index_this_turn,
                    explicit_egg_pack_request=state.explicit_egg_pack_request,
                ),
                requested_quantity_overrides,
            )
            if tool_name == "vkusvill_cart_link_create"
            else []
        )

        await on_progress(_tool_progress_text(tool_name))
        tool_span = trace.span(name=f"tool:{tool_name}", input=tool_args) if trace else None

        tool_result = await agent._call_mcp_tool(
            name=tool_name,
            arguments=tool_args,
            llm_provider=llm_provider,
            call_cache=state.mcp_call_cache,
        )
        if tool_name == "recipe_ingredients":
            tool_result = sanitize_recipe_ingredients_tool_result(
                tool_result=tool_result,
                explicit_pantry_requests=state.explicit_pantry_requests,
            )
        update_product_index_from_tool_result(
            product_index=state.product_index_this_turn,
            tool_name=tool_name,
            tool_result=tool_result,
        )
        if tool_name == "vkusvill_products_search":
            update_search_query_by_xml_id(
                search_query_by_xml_id=state.search_query_by_xml_id_this_turn,
                tool_args=tool_args,
                tool_result=tool_result,
            )
        state.tools_called_this_turn = True
        if tool_name in {"recipe_ingredients", "recipe_search"}:
            state.recipe_flow_started_this_turn = True

        cart_data = _extract_cart_data_from_result(tool_name=tool_name, tool_result=tool_result)
        if cart_data is not None:
            products = tool_args.get("products")
            if isinstance(products, list) and "products" not in cart_data:
                cart_data["products"] = products
            if requested_products_snapshot and "requested_products" not in cart_data:
                cart_data["requested_products"] = requested_products_snapshot
            state.cart_data_this_turn = cart_data

        agent._capture_cart_snapshot(
            user_id=user_id,
            tool_name=tool_name,
            args=tool_args,
            result=tool_result,
        )

        if tool_span is not None:
            tool_span.end(output=tool_result[:5000])

        state.history.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": agent._prepare_tool_result_for_history(tool_name, tool_result),
            }
        )

    state.history = agent._normalize_history(state.history)


def apply_requested_ingredient_overrides(
    *,
    tool_name: str,
    tool_args: dict[str, Any],
    product_index: dict[int, dict[str, Any]],
    explicit_egg_pack_request: bool,
    requested_ingredients: list[dict[str, Any]],
    search_query_by_xml_id: dict[int, str],
    requested_quantity_overrides: dict[int, float],
) -> None:
    if tool_name != "vkusvill_cart_link_create":
        return
    if explicit_egg_pack_request:
        return
    if not requested_ingredients:
        return
    products = tool_args.get("products")
    if not isinstance(products, list):
        return

    for row in products:
        if not isinstance(row, dict):
            continue
        xml_id_raw = row.get("xml_id")
        if isinstance(xml_id_raw, bool):
            continue
        try:
            xml_id = int(xml_id_raw)
        except (TypeError, ValueError):
            continue
        product = product_index.get(xml_id)
        if not isinstance(product, dict):
            continue
        matched_ingredient = match_requested_ingredient(
            product=product,
            xml_id=xml_id,
            requested_ingredients=requested_ingredients,
            search_query_by_xml_id=search_query_by_xml_id,
        )
        if matched_ingredient is None:
            continue
        requested_q, purchase_q = calculate_requested_and_purchase_quantity(
            ingredient=matched_ingredient,
            product=product,
        )
        if requested_q > 0:
            requested_quantity_overrides[xml_id] = requested_q
        row["q"] = purchase_q
