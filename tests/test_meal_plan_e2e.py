"""E2E-like test for complex meal-plan request/response contract."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from vkuswill_bot.agents.shopping_agent import ShoppingAgent


class _FakeDialogManager:
    def __init__(self) -> None:
        self._locks: dict[int, Any] = {}

    def get_lock(self, user_id: int) -> Any:
        import asyncio

        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]


class _FakeMessage:
    def __init__(self, content: str | None = None) -> None:
        self.content = content
        self.tool_calls = None


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = scripted

    async def create(self, **kwargs: Any) -> Any:
        if not self._scripted:
            raise RuntimeError("No scripted LLM response")
        return self._scripted.pop(0)


class _FakeLLMClient:
    def __init__(self, scripted: list[Any]) -> None:
        self.completions = _FakeCompletions(scripted)
        self.chat = SimpleNamespace(completions=self.completions)

    async def close(self) -> None:
        return None


def _meal_plan_parse_response(
    *,
    days: int,
    people_total: int,
    child_count: int | None = None,
    child_age_years: int | None = None,
    allergens_excluded: list[str] | None = None,
) -> _FakeResponse:
    return _FakeResponse(
        _FakeMessage(
            content=json.dumps(
                {
                    "days": days,
                    "people_total": people_total,
                    "requested_meal_types": None,
                    "child_count": child_count,
                    "child_age_years": child_age_years,
                    "diet": None,
                    "cuisines": [],
                    "allergens_excluded": allergens_excluded or [],
                    "confidence": 0.99,
                    "reason": "family weekly meal plan",
                },
                ensure_ascii=False,
            )
        )
    )


class _MealPlanMCPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": "recipe_ingredients", "description": "Ingredients", "parameters": {}},
            {"name": "recipe_search", "description": "Recipe search", "parameters": {}},
            {"name": "vkusvill_products_search", "description": "Products", "parameters": {}},
            {"name": "vkusvill_cart_link_create", "description": "Cart", "parameters": {}},
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        if name == "recipe_ingredients":
            dish = str(arguments.get("dish", "")).lower().replace(" ", "")
            return json.dumps(
                {
                    "ok": True,
                    "ingredients": [
                        {
                            "name": f"инг-{dish}",
                            "search_query": f"инг-{dish}",
                            "quantity": 1,
                            "unit": "шт",
                        },
                        {"name": "рис", "search_query": "рис", "quantity": 1, "unit": "кг"},
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_products_search":
            query = str(arguments.get("q", "")).strip() or "товар"
            return json.dumps(
                {
                    "ok": True,
                    "items": [
                        {
                            "xml_id": 410 + len(self.calls),
                            "name": f"Товар для {query}",
                            "price": 100,
                            "unit": "шт",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if name == "vkusvill_cart_link_create":
            return json.dumps(
                {"ok": True, "data": {"link": "https://shop.example/cart/e2e-meal"}},
                ensure_ascii=False,
            )
        return json.dumps({"ok": True}, ensure_ascii=False)


class _AllowAllRolloutController:
    async def resolve_rollout_percent(self, *, configured_percent: int) -> int:
        return configured_percent


@pytest.mark.asyncio
async def test_meal_plan_e2e_contract_for_family_with_child_and_allergy() -> None:
    _meal_names = [
        ("Овсяная каша", "breakfast"),
        ("Овощной суп", "lunch"),
        ("Рис с овощами", "dinner"),
        ("Творог с фруктами", "breakfast"),
        ("Куриные тефтели", "lunch"),
        ("Запеканка", "dinner"),
        ("Гречка с овощами", "breakfast"),
        ("Рагу из индейки", "lunch"),
        ("Салат с курицей", "dinner"),
        ("Блины", "breakfast"),
        ("Щи", "lunch"),
        ("Индейка с рисом", "dinner"),
        ("Каша пшённая", "breakfast"),
        ("Борщ", "lunch"),
        ("Котлеты рыбные", "dinner"),
        ("Омлет", "breakfast"),
        ("Суп-пюре тыквенный", "lunch"),
        ("Пельмени", "dinner"),
        ("Сырники", "breakfast"),
        ("Куриный суп", "lunch"),
        ("Плов", "dinner"),
    ]
    dishes = [
        {
            "name": name,
            "day": idx // 3 + 1,
            "meal_type": mt,
            "servings_total": 4,
            "audience_groups": ["adults", "child_2y"],
            "cuisine_tags": ["russian"],
        }
        for idx, (name, mt) in enumerate(_meal_names)
    ]
    llm_payload = {"schema_version": 1, "dishes": dishes}
    llm_client = _FakeLLMClient(
        [
            _meal_plan_parse_response(
                days=7,
                people_total=4,
                child_count=1,
                child_age_years=2,
                allergens_excluded=["орехи"],
            ),
            _FakeResponse(_FakeMessage(content=json.dumps(llm_payload, ensure_ascii=False))),
        ]
    )

    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=_MealPlanMCPClient(),  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        meal_plan_intent_routing_enabled=True,
        meal_plan_executor_enabled=True,
    )
    agent._meal_plan_rollout_controller = _AllowAllRolloutController()

    result = await agent.process_message(
        user_id=444,
        text=("меню на неделю для 4 человек, один ребенок 2 года с аллергией на орехи"),
    )

    assert "🍽 План питания" in result
    assert "орехи" in result
    assert "День 1" in result
    assert "child_2y" in result
    assert "Корзина ВкусВилл" in result
    assert "https://shop.example/cart/e2e-meal" in result
