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
    dishes = [
        {
            "name": "Овсяная каша",
            "day": 1,
            "meal_type": "breakfast",
            "servings_total": 4,
            "audience_groups": ["adults", "child_2y"],
            "cuisine_tags": ["russian"],
        },
        {
            "name": "Овощной суп",
            "day": 2,
            "meal_type": "lunch",
            "servings_total": 4,
            "audience_groups": ["adults", "child_2y"],
            "cuisine_tags": ["russian"],
        },
        {
            "name": "Индейка с рисом",
            "day": 3,
            "meal_type": "dinner",
            "servings_total": 4,
            "audience_groups": ["adults"],
            "cuisine_tags": ["russian"],
        },
        {
            "name": "Творог с фруктами",
            "day": 4,
            "meal_type": "breakfast",
            "servings_total": 4,
            "audience_groups": ["adults", "child_2y"],
            "cuisine_tags": ["russian"],
        },
        {
            "name": "Куриные тефтели",
            "day": 5,
            "meal_type": "lunch",
            "servings_total": 4,
            "audience_groups": ["adults", "child_2y"],
            "cuisine_tags": ["russian"],
        },
        {
            "name": "Запеканка",
            "day": 6,
            "meal_type": "dinner",
            "servings_total": 4,
            "audience_groups": ["adults", "child_2y"],
            "cuisine_tags": ["russian"],
        },
        {
            "name": "Гречка с овощами",
            "day": 7,
            "meal_type": "lunch",
            "servings_total": 4,
            "audience_groups": ["adults"],
            "cuisine_tags": ["russian"],
        },
    ]
    llm_payload = {"schema_version": 1, "dishes": dishes}
    llm_client = _FakeLLMClient(
        [_FakeResponse(_FakeMessage(content=json.dumps(llm_payload, ensure_ascii=False)))]
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
    assert "Параметры запроса:" in result
    assert "Группы: adults (3), child_2y (1)" in result
    assert "Жесткие ограничения:" in result
    assert "орехи" in result
    assert "План по дням:" in result
    assert "День 1:" in result
    assert "Перекус 1:" in result
    assert "Адаптации по группам:" in result
    assert "child_2y" in result
    assert "Корзина:" in result
    assert "https://shop.example/cart/e2e-meal" in result
    assert "Проверка ограничений:" in result
