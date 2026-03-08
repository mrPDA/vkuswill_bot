"""Langfuse tracing helpers for meal-plan executor phases."""

from __future__ import annotations

from typing import Any


def start_span(*, trace: Any | None, name: str, input: dict[str, Any]) -> Any | None:
    if trace is None:
        return None
    return trace.span(name=name, input=input)


def finish_parse_span(
    *,
    span: Any | None,
    request: Any | None = None,
    error: Exception | None = None,
) -> None:
    if span is None:
        return
    if error is not None:
        span.end(
            output=str(error),
            level="ERROR",
            status_message="meal_plan_parse_failed",
        )
        return
    span.end(
        output={
            "days": request.days,
            "people_total": request.people_total,
            "groups": [group.to_prompt_dict() for group in request.groups],
        }
    )


def finish_ingredient_span(
    *,
    span: Any | None,
    flat_ingredients: list[dict[str, Any]],
    ingredients_by_dish: dict[str, list[dict[str, Any]]],
    stats: Any,
) -> None:
    if span is None:
        return
    span.end(
        output={
            "flat_ingredients": len(flat_ingredients),
            "by_dish": len(ingredients_by_dish),
            "stats": stats.as_dict(),
        },
        level="DEFAULT" if flat_ingredients else "WARNING",
        status_message=None if flat_ingredients else "meal_plan_ingredients_empty",
    )


def finish_phase2_safety_span(*, span: Any | None, outcome: Any) -> None:
    if span is None:
        return
    span.end(
        output={
            "proceed": outcome.proceed,
            "dishes_count": len(outcome.dishes_payload),
            "flat_ingredients": len(outcome.flat_ingredients),
            "fallback_reason": outcome.fallback_reason,
        },
        level="DEFAULT" if outcome.proceed else "WARNING",
        status_message=None if outcome.proceed else "meal_plan_phase2_safety_fallback",
    )


def finish_search_span(
    *,
    span: Any | None,
    products: list[dict[str, Any]],
    not_found: list[str],
    used_chunk_fallback: bool,
    stats: Any,
) -> None:
    if span is None:
        return
    span.end(
        output={
            "products_count": len(products),
            "not_found_count": len(not_found),
            "used_chunk_fallback": used_chunk_fallback,
            "stats": stats.as_dict(),
        },
        level="DEFAULT" if products else "WARNING",
        status_message=None if products else "meal_plan_search_empty_products",
    )


def finish_cart_span(*, span: Any | None, stats: Any) -> None:
    if span is None:
        return
    span.end(
        output={
            "cart_created": stats.cart_created,
            "has_link": stats.has_link,
            "returned_products_count": stats.returned_products_count,
            "failed_before_response": stats.failed_before_response,
        },
        level="DEFAULT" if stats.cart_created else "WARNING",
        status_message=None if stats.cart_created else "meal_plan_cart_not_created",
    )
