"""Multi-course recipe executor: decompose multi-dish requests into sub-queries.

When a user asks for 4 dishes (breakfast + lunch + dinner + dessert), the LLM-based
tool loop can't reliably process all of them within step limits.  This executor
bypasses the LLM tool loop entirely: it parses dish names from the user text,
calls recipe_ingredients for each dish in parallel, searches products, creates
one cart, and renders a deterministic response.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from vkuswill_bot.agents.cart_output_renderer import render_stable_cart_output
from vkuswill_bot.agents.cart_price_builder import normalize_product_row
from vkuswill_bot.agents.meal_plan_cart_ops import _create_single_cart
from vkuswill_bot.agents.meal_plan_runtime_ops import (
    aggregate_ingredients,
    extract_ingredients,
    merge_products,
)
from vkuswill_bot.agents.meal_plan_recipe_search_ops import search_products
from vkuswill_bot.agents.meal_plan_runtime_policy import (
    call_with_timeout_retry,
    deadline_after,
)

logger = logging.getLogger(__name__)

ProgressReporter = Callable[[str], Awaitable[None]]
FallbackFn = Callable[[str], Awaitable[str]]

_TURN_DEADLINE_SECONDS = 55.0
_INGREDIENT_TIMEOUT_SECONDS = 15.0
_CART_TIMEOUT_SECONDS = 15.0
_INGREDIENT_CONCURRENCY = 4

_SERVINGS_WORDS: dict[str, int] = {
    "двоих": 2, "двух": 2,
    "троих": 3, "трёх": 3, "трех": 3,
    "четверых": 4, "четырёх": 4, "четырех": 4,
}

_MEAL_TYPE_RE = re.compile(
    r"(?:(?:для|на)\s+)?"
    r"(завтрак\w*|обед\w*|ужин\w*|десерт\w*|полдник\w*|перекус\w*)",
    re.IGNORECASE,
)

_GLOBAL_SERVINGS_RE = re.compile(
    r"(?:для|на)\s+(\d+|двоих|двух|троих|трёх|трех|четверых|четырёх|четырех)"
    r"\s*(?:чел\w*|порц\w*|персон\w*)?",
    re.IGNORECASE,
)


def _extract_servings_from_segment(segment: str) -> tuple[int, str]:
    """Extract and remove servings info from a segment, return (servings, cleaned)."""
    # "на 4 порции" / "4 порции"
    m = re.search(r"(?:на\s+)?(\d+)\s*порц\w*", segment, re.I)
    if m:
        return int(m.group(1)), segment[: m.start()] + segment[m.end() :]

    # "для двоих" / "на двоих" / "для троих" etc.
    for word, n in _SERVINGS_WORDS.items():
        m = re.search(rf"(?:для|на)\s+{word}", segment, re.I)
        if m:
            return n, segment[: m.start()] + segment[m.end() :]

    # "для 3 человек" / "на 4 персон"
    m = re.search(r"(?:для|на)\s+(\d+)\s*(?:чел\w*|персон\w*)", segment, re.I)
    if m:
        return int(m.group(1)), segment[: m.start()] + segment[m.end() :]

    return 0, segment


# ---------------------------------------------------------------------------
# Dish extraction
# ---------------------------------------------------------------------------

def extract_dishes_from_text(text: str) -> list[dict[str, Any]]:
    """Extract individual dish names and servings from a multi-course request.

    Returns a list of dicts: [{"name": str, "servings": int}, ...].
    Returns [] if fewer than 2 distinct courses are detected.
    """
    low = text.lower()
    markers = list(_MEAL_TYPE_RE.finditer(low))
    if len(markers) < 2:
        return []

    global_servings = 2
    gs = _GLOBAL_SERVINGS_RE.search(low)
    if gs:
        raw = gs.group(1)
        global_servings = _SERVINGS_WORDS.get(raw, int(raw) if raw.isdigit() else 2)

    dishes: list[dict[str, Any]] = []
    for i, m in enumerate(markers):
        seg_start = m.end()
        seg_end = markers[i + 1].start() if i + 1 < len(markers) else len(text)
        segment = text[seg_start:seg_end]

        per_dish, segment = _extract_servings_from_segment(segment)

        segment = re.sub(r"^[\s\-—–:;,]+", "", segment)
        segment = re.sub(r"[\s;,]+$", "", segment)
        # Remove trailing standalone connectors — require whitespace before
        # to avoid stripping word-final letters (e.g. "и" in "фруктами").
        segment = re.sub(
            r"\s+(?:плюс|и|всё|все|ещё|еще)\s*$", "", segment, flags=re.I,
        )

        dish_name = segment.strip().strip(".,;:—–- ").strip()
        if dish_name and len(dish_name) >= 2:
            dishes.append({
                "name": dish_name,
                "servings": per_dish or global_servings,
            })

    return dishes


# ---------------------------------------------------------------------------
# Ingredient collection
# ---------------------------------------------------------------------------

async def _collect_all_ingredients(
    *,
    agent: Any,
    state: Any,
    user_id: int,
    llm_provider: str,
    dishes: list[dict[str, Any]],
    deadline_at: float,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Call recipe_ingredients for each dish concurrently."""
    semaphore = asyncio.Semaphore(_INGREDIENT_CONCURRENCY)

    async def _load(dish: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        async with semaphore:
            result = await call_with_timeout_retry(
                operation=lambda: agent._call_mcp_tool(
                    name="recipe_ingredients",
                    arguments={"dish": dish["name"], "servings": dish["servings"]},
                    llm_provider=llm_provider,
                    call_cache=state.mcp_call_cache,
                    user_id=user_id,
                ),
                timeout_seconds=_INGREDIENT_TIMEOUT_SECONDS,
                hard_deadline_at=deadline_at,
                retries=0,
            )
            return dish["name"], extract_ingredients(result)

    tasks = [_load(d) for d in dishes]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    flat: list[dict[str, Any]] = []
    by_dish: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        if isinstance(r, Exception):
            logger.warning("Multi-course ingredient load failed: %s", r)
            continue
        name, ingredients = r
        if ingredients:
            by_dish[name] = ingredients
            flat.extend(ingredients)

    return flat, by_dish


# ---------------------------------------------------------------------------
# Product index builder
# ---------------------------------------------------------------------------

def _build_product_index(products: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Build a product_index from search results for ensure_cart_price_summary."""
    index: dict[int, dict[str, Any]] = {}
    for p in products:
        normalized = normalize_product_row(p)
        if normalized is not None:
            index[normalized["xml_id"]] = normalized
    return index


# ---------------------------------------------------------------------------
# Response rendering
# ---------------------------------------------------------------------------

def _render_multi_course_response(
    *,
    dishes: list[dict[str, Any]],
    by_dish: dict[str, list[dict[str, Any]]],
    cart_data: dict[str, Any],
    not_found: list[str],
) -> str:
    """Render a deterministic multi-course response with dish summary and cart."""
    parts: list[str] = []

    dish_lines = []
    for d in dishes:
        name = d["name"]
        count = len(by_dish.get(name, []))
        status = "✅" if count else "⚠️"
        dish_lines.append(f"{status} {name} ({d['servings']} порц.) — {count} ингр.")

    parts.append(
        f"🍽 <b>Собрала корзину для {len(dishes)} блюд:</b>\n" + "\n".join(dish_lines)
    )

    cart_output = render_stable_cart_output(cart_data, include_intro=False)
    parts.append(cart_output)

    if not_found:
        snippet = ", ".join(not_found[:10])
        suffix = f" (и ещё {len(not_found) - 10})" if len(not_found) > 10 else ""
        parts.append(f"⚠️ Не найдено в каталоге: {snippet}{suffix}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------

async def run_multi_course_turn(
    *,
    agent: Any,
    state: Any,
    user_id: int,
    text: str,
    llm_provider: str,
    trace: Any | None,
    on_progress: ProgressReporter,
    fallback_to_standard_turn: FallbackFn,
    diagnostics: dict[str, Any] | None = None,
) -> str:
    """Execute a multi-course recipe request via deterministic code-level orchestration.

    Phases:
      1. Parse dish names from user text
      2. Collect ingredients for all dishes (parallel recipe_ingredients calls)
      3. Aggregate & search products (recipe_search / products_search)
      4. Create single cart via MCP
      5. Render deterministic response
    """
    started_at = time.monotonic()
    dishes = extract_dishes_from_text(text)
    if len(dishes) < 2:
        logger.info("Multi-course extraction found < 2 dishes, falling back")
        return await fallback_to_standard_turn("не удалось определить блюда")

    turn_deadline_at = deadline_after(_TURN_DEADLINE_SECONDS)

    # --- Phase 1: Collect ingredients ---
    await on_progress(f"🍽 Подбираю рецепты для {len(dishes)} блюд...")

    ingr_span = None
    if trace is not None:
        with contextlib.suppress(Exception):
            ingr_span = trace.span(
                name="multi-course.collect-ingredients",
                input={"dishes": [d["name"] for d in dishes]},
            )

    flat_ingredients, by_dish = await _collect_all_ingredients(
        agent=agent,
        state=state,
        user_id=user_id,
        llm_provider=llm_provider,
        dishes=dishes,
        deadline_at=turn_deadline_at,
    )
    if ingr_span is not None:
        with contextlib.suppress(Exception):
            ingr_span.end(output={
                "total_ingredients": len(flat_ingredients),
                "dishes_with_ingredients": len(by_dish),
            })

    if not flat_ingredients:
        logger.warning("Multi-course: no ingredients collected")
        return await fallback_to_standard_turn("не удалось собрать ингредиенты")

    # --- Phase 2: Search products ---
    await on_progress("🔍 Ищу продукты для всех блюд...")
    aggregated = aggregate_ingredients(flat_ingredients)

    search_span = None
    if trace is not None:
        with contextlib.suppress(Exception):
            search_span = trace.span(
                name="multi-course.search-products",
                input={"ingredients_count": len(aggregated)},
            )

    products, not_found, _used_fallback, _search_stats = await search_products(
        agent=agent,
        state=state,
        user_id=user_id,
        llm_provider=llm_provider,
        aggregated_ingredients=aggregated,
        phase2_deadline_at=turn_deadline_at,
    )
    if search_span is not None:
        with contextlib.suppress(Exception):
            search_span.end(output={
                "products_found": len(products),
                "not_found_count": len(not_found),
            })

    if not products:
        logger.warning("Multi-course: no products found")
        return await fallback_to_standard_turn("не удалось найти продукты в каталоге")

    # --- Phase 3: Create cart ---
    await on_progress("🛒 Формирую корзину...")

    product_index = _build_product_index(products)
    merged = merge_products(products)

    cart_data, cart_stats = await _create_single_cart(
        agent=agent,
        state=state,
        user_id=user_id,
        llm_provider=llm_provider,
        products=merged,
        phase2_deadline_at=turn_deadline_at,
        timeout_seconds=_CART_TIMEOUT_SECONDS,
    )

    if cart_data is None or not cart_stats.cart_created:
        logger.warning("Multi-course: cart creation failed")
        return await fallback_to_standard_turn("не удалось создать корзину")

    cart_data.setdefault("products", merged)
    agent._ensure_cart_price_summary(
        cart_data=cart_data,
        product_index=product_index,
    )

    state.cart_data_this_turn = cart_data
    with contextlib.suppress(Exception):
        import json

        agent._capture_cart_snapshot(
            user_id=user_id,
            tool_name="vkusvill_cart_link_create",
            args={"products": merged},
            result=json.dumps({"ok": True, "data": cart_data}, ensure_ascii=False),
        )

    # --- Phase 4: Render ---
    response = _render_multi_course_response(
        dishes=dishes,
        by_dish=by_dish,
        cart_data=cart_data,
        not_found=not_found,
    )

    agent._history[user_id] = agent._trim_history([
        *state.history,
        {"role": "assistant", "content": response},
    ])

    elapsed_ms = (time.monotonic() - started_at) * 1000
    if isinstance(diagnostics, dict):
        diagnostics["multi_course"] = {
            "dishes_requested": len(dishes),
            "dishes_with_ingredients": len(by_dish),
            "ingredients_total": len(flat_ingredients),
            "ingredients_aggregated": len(aggregated),
            "products_found": len(merged),
            "not_found_count": len(not_found),
            "cart_created": cart_stats.cart_created,
            "elapsed_ms": round(elapsed_ms),
        }

    if trace is not None:
        with contextlib.suppress(Exception):
            trace.update(
                output=response,
                metadata={
                    "execution_path": "multi_course_executor",
                    "dishes_count": len(dishes),
                    "dishes_with_ingredients": len(by_dish),
                    "products_found": len(merged),
                    "not_found_count": len(not_found),
                    "elapsed_ms": round(elapsed_ms),
                },
            )

    logger.info(
        "Multi-course executor: %d dishes, %d ingredients, %d products, %dms",
        len(dishes),
        len(flat_ingredients),
        len(merged),
        round(elapsed_ms),
    )
    return response
