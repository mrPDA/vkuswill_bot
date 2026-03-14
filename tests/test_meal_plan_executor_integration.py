"""Integration tests for ShoppingAgent + dedicated meal-plan executor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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


class _FakeFunction:
    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, tool_call_id: str, name: str, arguments: str) -> None:
        self.id = tool_call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(
        self,
        content: str | None = None,
        tool_calls: list[_FakeToolCall] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeCompletions:
    def __init__(self, scripted: list[Any]) -> None:
        self._scripted = scripted
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._scripted:
            raise RuntimeError("No scripted LLM response")
        item = self._scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


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
                            "name": f"ингредиент-{dish}",
                            "search_query": f"ингредиент-{dish}",
                            "quantity": 1,
                            "unit": "шт",
                        },
                        {
                            "name": "помидоры",
                            "search_query": "помидоры",
                            "quantity": 1,
                            "unit": "шт",
                        },
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
                            "xml_id": 300 + len(self.calls),
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
                {"ok": True, "data": {"link": "https://shop.example/cart/integration-meal"}},
                ensure_ascii=False,
            )
        return json.dumps({"ok": True}, ensure_ascii=False)


class _BrokenPreferencesStore:
    async def get_formatted(self, user_id: int) -> str:
        raise RuntimeError(f"sqlite readonly for {user_id}")


class _AllowAllRolloutController:
    async def resolve_rollout_percent(self, *, configured_percent: int) -> int:
        return configured_percent


class _BrokenRolloutController:
    async def resolve_rollout_percent(self, *, configured_percent: int) -> int:
        raise RuntimeError("centralized KPI reader unavailable")


@pytest.mark.asyncio
async def test_shopping_agent_routes_meal_plan_to_dedicated_executor() -> None:
    meal_types = ["breakfast", "lunch", "dinner", "breakfast", "lunch", "dinner", "lunch"]
    plan_payload = {
        "schema_version": 1,
        "dishes": [
            {
                "name": f"Овощное блюдо {idx}",
                "day": idx,
                "meal_type": meal_types[idx - 1],
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": ["italian"],
            }
            for idx in range(1, 8)
        ],
    }
    llm_client = _FakeLLMClient(
        [_FakeResponse(_FakeMessage(content=json.dumps(plan_payload, ensure_ascii=False)))]
    )
    mcp = _MealPlanMCPClient()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        meal_plan_intent_routing_enabled=True,
        meal_plan_executor_enabled=True,
    )
    agent._meal_plan_rollout_controller = _AllowAllRolloutController()

    result = await agent.process_message(user_id=333, text="собери меню на неделю для 2 человек")

    assert "🍽 План питания" in result
    assert "Овощное блюдо 1" in result
    assert "https://shop.example/cart/integration-meal" in result
    assert "Корзина ВкусВилл" in result

    call_names = [name for name, _args in mcp.calls]
    assert call_names.count("recipe_ingredients") == 7
    assert call_names.count("recipe_search") == 0
    assert call_names.count("vkusvill_cart_link_create") == 1
    assert call_names.count("vkusvill_products_search") >= 7
    assert len(llm_client.completions.calls) == 1


@pytest.mark.asyncio
async def test_shopping_agent_disables_executor_when_rollout_controller_unavailable() -> None:
    llm_client = _FakeLLMClient([_FakeResponse(_FakeMessage(content="ok"))])
    mcp = _MealPlanMCPClient()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        meal_plan_intent_routing_enabled=True,
        meal_plan_executor_enabled=True,
    )

    result = await agent.process_message(user_id=335, text="собери меню на неделю для 2 человек")

    assert "🍽 План питания" in result
    assert mcp.calls == []
    assert len(llm_client.completions.calls) == 1


@pytest.mark.asyncio
async def test_shopping_agent_disables_executor_when_rollout_controller_fails() -> None:
    llm_client = _FakeLLMClient([_FakeResponse(_FakeMessage(content="ok"))])
    mcp = _MealPlanMCPClient()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        meal_plan_intent_routing_enabled=True,
        meal_plan_executor_enabled=True,
    )
    agent._meal_plan_rollout_controller = _BrokenRolloutController()

    result = await agent.process_message(user_id=337, text="собери меню на неделю для 2 человек")

    assert "🍽 План питания" in result
    assert mcp.calls == []
    assert len(llm_client.completions.calls) == 1


@pytest.mark.asyncio
async def test_shopping_agent_allows_explicit_unvalidated_rollout_override() -> None:
    meal_types = ["breakfast", "lunch", "dinner", "breakfast", "lunch", "dinner", "lunch"]
    plan_payload = {
        "schema_version": 1,
        "dishes": [
            {
                "name": f"Овощное блюдо {idx}",
                "day": idx,
                "meal_type": meal_types[idx - 1],
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": ["italian"],
            }
            for idx in range(1, 8)
        ],
    }
    llm_client = _FakeLLMClient(
        [_FakeResponse(_FakeMessage(content=json.dumps(plan_payload, ensure_ascii=False)))]
    )
    mcp = _MealPlanMCPClient()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        meal_plan_intent_routing_enabled=True,
        meal_plan_executor_enabled=True,
        meal_plan_allow_unvalidated_rollout=True,
        meal_plan_unvalidated_rollout_reason="integration override",
        meal_plan_unvalidated_rollout_actor="test-suite",
        meal_plan_unvalidated_rollout_expires_at=(
            datetime.now(UTC) + timedelta(minutes=30)
        ).isoformat(),
        deployment_environment="staging",
    )

    result = await agent.process_message(user_id=336, text="собери меню на неделю для 2 человек")

    assert "🍽 План питания" in result
    call_names = [name for name, _args in mcp.calls]
    assert call_names.count("recipe_ingredients") == 7
    assert call_names.count("recipe_search") == 0
    assert call_names.count("vkusvill_products_search") >= 7
    assert call_names.count("vkusvill_cart_link_create") == 1


@pytest.mark.asyncio
async def test_shopping_agent_shadow_mode_keeps_standard_path() -> None:
    llm_client = _FakeLLMClient([_FakeResponse(_FakeMessage(content="ok"))])
    mcp = _MealPlanMCPClient()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        meal_plan_intent_routing_enabled=True,
        meal_plan_executor_enabled=True,
        meal_plan_shadow_mode_enabled=True,
    )

    result = await agent.process_message(user_id=334, text="собери меню на неделю для 2 человек")

    assert "🍽 План питания" in result
    assert mcp.calls == []
    assert len(llm_client.completions.calls) == 1


@pytest.mark.asyncio
async def test_shopping_agent_disables_meal_plan_routing_when_feature_flag_off() -> None:
    llm_client = _FakeLLMClient([_FakeResponse(_FakeMessage(content="ok"))])
    mcp = _MealPlanMCPClient()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        meal_plan_intent_routing_enabled=False,
        meal_plan_executor_enabled=True,
    )
    agent._meal_plan_rollout_controller = _AllowAllRolloutController()

    result = await agent.process_message(
        user_id=339,
        text="план питания на неделю для 2 человек",
    )

    assert result == "ok"
    assert "🍽 План питания" not in result
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_meal_plan_uses_explicit_constraints_when_preferences_unavailable() -> None:
    meal_types = ["breakfast", "lunch", "dinner", "breakfast", "lunch", "dinner", "lunch"]
    plan_payload = {
        "schema_version": 1,
        "dishes": [
            {
                "name": f"Овощное блюдо {idx}",
                "day": idx,
                "meal_type": meal_types[idx - 1],
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": ["russian"],
            }
            for idx in range(1, 8)
        ],
    }
    llm_client = _FakeLLMClient(
        [_FakeResponse(_FakeMessage(content=json.dumps(plan_payload, ensure_ascii=False)))]
    )
    mcp = _MealPlanMCPClient()
    agent = ShoppingAgent(
        llm_base_url="https://llm.api.cloud.yandex.net/v1",
        llm_api_key="test-key",
        llm_model="gpt://folder/model/latest",
        llm_max_concurrent=2,
        mcp_client=mcp,  # type: ignore[arg-type]
        dialog_manager=_FakeDialogManager(),  # type: ignore[arg-type]
        llm_client=llm_client,
        prompt_profiles_enabled=True,
        preferences_store=_BrokenPreferencesStore(),  # type: ignore[arg-type]
        meal_plan_intent_routing_enabled=True,
        meal_plan_executor_enabled=True,
    )
    agent._meal_plan_rollout_controller = _AllowAllRolloutController()

    result = await agent.process_message(
        user_id=338,
        text="собери меню на неделю для 2 человек с аллергией на орехи",
    )

    assert "🍽 План питания" in result
    assert "орехи" in result
    assert "Корзина ВкусВилл" in result
