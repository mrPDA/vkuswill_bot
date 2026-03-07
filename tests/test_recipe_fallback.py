"""Тесты для vkuswill_bot.agents.recipe_fallback."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from vkuswill_bot.agents.recipe_fallback import (
    extract_recipe_ingredients_with_llm,
    fallback_recipe_ingredients,
    fallback_recipe_search,
)


class _FakeAdapter:
    def __init__(
        self,
        *,
        response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.response = response or {"choices": [{"message": {"content": "[]"}}]}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.mark.asyncio
async def test_extract_recipe_ingredients_with_llm_returns_empty_without_adapter() -> None:
    rows = await extract_recipe_ingredients_with_llm(
        dish="борщ",
        servings=2,
        adapter=None,
        model="test-model",
        timeout_seconds=0.1,
    )
    assert rows == []


@pytest.mark.asyncio
async def test_extract_recipe_ingredients_with_llm_normalizes_rows() -> None:
    adapter = _FakeAdapter(
        response={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "ingredients": [
                                    {"name": "Свёкла", "quantity": "0.5", "unit": "кг"},
                                    {
                                        "name": "Соль",
                                        "quantity": 0,
                                        "unit": "ч.л.",
                                        "optional": True,
                                    },
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
    )

    rows = await extract_recipe_ingredients_with_llm(
        dish="борщ",
        servings=2,
        adapter=adapter,
        model="test-model",
        timeout_seconds=0.1,
    )

    assert adapter.calls
    assert adapter.calls[0]["tool_choice"] == "none"
    assert adapter.calls[0]["max_tokens"] == 900
    assert adapter.calls[0]["temperature"] == 0.1
    assert rows[0]["name"] == "Свёкла"
    assert rows[0]["quantity"] == 0.5
    assert rows[0]["search_query"] == "Свёкла"
    assert rows[1]["name"] == "Соль"
    assert rows[1]["quantity"] == 1.0
    assert rows[1]["optional"] is True


@pytest.mark.asyncio
async def test_fallback_recipe_ingredients_validates_missing_dish() -> None:
    raw = await fallback_recipe_ingredients(
        {},
        adapter=None,
        model="test-model",
        timeout_seconds=0.1,
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "Не указано название блюда" in payload["error"]


@pytest.mark.asyncio
async def test_fallback_recipe_ingredients_uses_borscht_builtin_when_llm_fails() -> None:
    adapter = _FakeAdapter(error=RuntimeError("llm unavailable"))
    raw = await fallback_recipe_ingredients(
        {"dish": "Борщ", "servings": 4},
        adapter=adapter,
        model="test-model",
        timeout_seconds=0.1,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["source"] == "shopping_agent_fallback"
    assert payload["servings"] == 4
    assert isinstance(payload["ingredients"], list) and payload["ingredients"]


@pytest.mark.asyncio
async def test_fallback_recipe_ingredients_returns_error_for_non_borscht_without_llm_data() -> None:
    adapter = _FakeAdapter(response={"choices": [{"message": {"content": "not-json"}}]})
    raw = await fallback_recipe_ingredients(
        {"dish": "окрошка", "servings": 2},
        adapter=adapter,
        model="test-model",
        timeout_seconds=0.1,
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == "Не удалось получить рецепт"


@pytest.mark.asyncio
async def test_extract_recipe_ingredients_with_llm_retries_once_on_parse_failure() -> None:
    adapter = _FakeAdapter(
        response={
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "ingredients": [
                                    {
                                        "name": "Киноа",
                                        "quantity": 1,
                                        "unit": "уп",
                                        "search_query": "киноа",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
    )
    adapter.response = {"choices": [{"message": {"content": "not-json"}}]}
    second_response = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "ingredients": [
                                {
                                    "name": "Киноа",
                                    "quantity": 1,
                                    "unit": "уп",
                                    "search_query": "киноа",
                                }
                            ]
                        },
                        ensure_ascii=False,
                    )
                }
            }
        ]
    }

    async def _create_completion(**kwargs: Any) -> dict[str, Any]:
        adapter.calls.append(kwargs)
        return adapter.response if len(adapter.calls) == 1 else second_response

    adapter.create_completion = _create_completion  # type: ignore[method-assign]

    rows = await extract_recipe_ingredients_with_llm(
        dish="Киноа с овощами",
        servings=2,
        adapter=adapter,
        model="test-model",
        timeout_seconds=0.5,
    )

    assert len(adapter.calls) == 2
    assert rows == [
        {
            "name": "Киноа",
            "quantity": 1.0,
            "unit": "уп",
            "search_query": "киноа",
        }
    ]


@pytest.mark.asyncio
async def test_fallback_recipe_search_returns_error_for_empty_ingredients() -> None:
    async def _search_fn(_query: str) -> str:
        return json.dumps({"ok": True, "data": {"items": []}}, ensure_ascii=False)

    raw = await fallback_recipe_search({"ingredients": []}, search_fn=_search_fn)
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["error"] == "Пустой список ingredients"


@pytest.mark.asyncio
async def test_fallback_recipe_search_builds_found_and_not_found_sections() -> None:
    async def _search_fn(query: str) -> str:
        if query == "свекла":
            return json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {"xml_id": 111, "name": "Свекла", "price": 54, "unit": "кг"},
                            {"xml_id": 222, "name": "Свекла мытая", "price": 65, "unit": "кг"},
                        ]
                    },
                },
                ensure_ascii=False,
            )
        return json.dumps({"ok": True, "data": {"items": []}}, ensure_ascii=False)

    raw = await fallback_recipe_search(
        {
            "ingredients": [
                {"name": "свёкла", "quantity": 0.5, "unit": "кг", "search_query": "свекла"},
                "морковь 2 шт",
            ]
        },
        search_fn=_search_fn,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert payload["source"] == "shopping_agent_fallback"
    assert payload["data"]["found"][0]["item"]["xml_id"] == 111
    assert payload["data"]["found"][0]["search_query"] == "свекла"
    assert "морковь" in payload["not_found"]
    assert payload["results"][1]["best_match"] is None


@pytest.mark.asyncio
async def test_fallback_recipe_search_respects_max_concurrent_and_keeps_order() -> None:
    active = 0
    peak = 0

    async def _search_fn(query: str) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {"xml_id": hash(query) % 10000, "name": query, "price": 100, "unit": "шт"},
                    ]
                },
            },
            ensure_ascii=False,
        )

    queries = ["лук", "морковь", "картофель", "свекла", "капуста"]
    raw = await fallback_recipe_search(
        {
            "ingredients": [
                {"name": query, "quantity": 1, "unit": "шт", "search_query": query}
                for query in queries
            ]
        },
        search_fn=_search_fn,
        max_concurrent=2,
    )
    payload = json.loads(raw)

    assert payload["ok"] is True
    assert peak <= 2
    assert [row["search_query"] for row in payload["results"]] == queries
