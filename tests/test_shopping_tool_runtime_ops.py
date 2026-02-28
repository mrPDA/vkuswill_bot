"""Unit tests for shopping tool runtime/recovery modules."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from vkuswill_bot.agents.mcp_helpers import tool_progress_text
from vkuswill_bot.agents.recovery_hints import FORCE_BATCH_SEARCH_HINT, FORCE_RECIPE_TO_CART_HINT
from vkuswill_bot.agents.shopping_tool_recovery import apply_post_step_recovery_hints
from vkuswill_bot.agents.shopping_tool_runtime_ops import execute_tool_calls


class _FakeSpan:
    def __init__(self) -> None:
        self.output: str | None = None

    def end(self, *, output: str) -> None:
        self.output = output


class _FakeTrace:
    def __init__(self) -> None:
        self.spans: list[tuple[str, dict[str, Any], _FakeSpan]] = []

    def span(self, *, name: str, input: dict[str, Any]) -> _FakeSpan:
        span = _FakeSpan()
        self.spans.append((name, input, span))
        return span


class _FakeAgent:
    def __init__(self, *, tool_results: dict[str, str]) -> None:
        self.tool_results = tool_results
        self.mcp_calls: list[tuple[str, dict[str, Any]]] = []
        self.captured_calls: list[tuple[str, dict[str, Any], str]] = []
        self.normalize_calls = 0

    async def _call_mcp_tool(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        llm_provider: str,
        call_cache: dict[str, str] | None = None,
        user_id: int | None = None,
    ) -> str:
        self.mcp_calls.append((name, arguments))
        return self.tool_results[name]

    def _capture_cart_snapshot(
        self,
        *,
        user_id: int,
        tool_name: str,
        args: dict[str, Any],
        result: str,
    ) -> None:
        self.captured_calls.append((tool_name, args, result))

    def _prepare_tool_result_for_history(self, tool_name: str, tool_result: str) -> str:
        return tool_result

    def _normalize_history(self, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.normalize_calls += 1
        return history


def _build_state(**overrides: Any) -> SimpleNamespace:
    base = {
        "history": [],
        "cart_intent": True,
        "cart_data_this_turn": None,
        "single_search_steps_streak": 0,
        "user_preferences": {},
        "product_index_this_turn": {},
        "explicit_egg_pack_request": False,
        "requested_ingredients": [],
        "search_query_by_xml_id_this_turn": {},
        "previous_cart_products": [],
        "mcp_call_cache": {},
        "tools_called_this_turn": False,
        "recipe_flow_started_this_turn": False,
        "explicit_pantry_requests": set(),
        "recipe_to_cart_recovery_used": False,
        "search_batch_recovery_used": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_execute_tool_calls_increments_single_search_streak_and_tracks_query() -> None:
    search_result = json.dumps(
        {
            "ok": True,
            "data": {"items": [{"xml_id": 101, "name": "Молоко", "price": {"current": 100}}]},
        },
        ensure_ascii=False,
    )
    state = _build_state(single_search_steps_streak=2)
    agent = _FakeAgent(tool_results={"vkusvill_products_search": search_result})
    trace = _FakeTrace()
    progress_events: list[str] = []

    async def _on_progress(text: str) -> None:
        progress_events.append(text)

    await execute_tool_calls(
        agent=agent,
        state=state,
        message={"content": "", "tool_calls": []},
        tool_calls=[
            {"id": "tc-1", "name": "vkusvill_products_search", "arguments": '{"q":"молоко"}'}
        ],
        user_id=1,
        text="собери корзину",
        llm_provider="qwen_openai",
        trace=trace,
        on_progress=_on_progress,
    )

    assert state.single_search_steps_streak == 3
    assert state.tools_called_this_turn is True
    assert state.search_query_by_xml_id_this_turn[101] == "молоко"
    assert progress_events == [tool_progress_text("vkusvill_products_search")]
    assert agent.mcp_calls[0] == ("vkusvill_products_search", {"q": "молоко"})
    assert trace.spans and trace.spans[0][0] == "tool:vkusvill_products_search"
    assert trace.spans[0][2].output is not None


@pytest.mark.asyncio
async def test_execute_tool_calls_adds_requested_products_to_cart_data(monkeypatch: Any) -> None:
    cart_result = json.dumps(
        {"ok": True, "data": {"link": "https://shop.example/cart/abc"}},
        ensure_ascii=False,
    )
    state = _build_state()
    agent = _FakeAgent(tool_results={"vkusvill_cart_link_create": cart_result})
    progress_events: list[str] = []

    async def _on_progress(text: str) -> None:
        progress_events.append(text)

    monkeypatch.setattr(
        "vkuswill_bot.agents.shopping_tool_runtime_ops.collect_requested_products_snapshot",
        lambda *args, **kwargs: [{"xml_id": 777, "q": 2}],
    )
    monkeypatch.setattr(
        "vkuswill_bot.agents.shopping_tool_runtime_ops.apply_requested_quantity_overrides",
        lambda snapshot, overrides: snapshot,
    )

    await execute_tool_calls(
        agent=agent,
        state=state,
        message={"content": "", "tool_calls": []},
        tool_calls=[
            {
                "id": "tc-2",
                "name": "vkusvill_cart_link_create",
                "arguments": '{"products":[{"xml_id":777,"q":1}]}',
            }
        ],
        user_id=2,
        text="собери корзину",
        llm_provider="qwen_openai",
        trace=None,
        on_progress=_on_progress,
    )

    assert state.cart_data_this_turn is not None
    assert state.cart_data_this_turn["link"] == "https://shop.example/cart/abc"
    assert state.cart_data_this_turn["products"] == [{"xml_id": 777, "q": 1}]
    assert state.cart_data_this_turn["requested_products"] == [{"xml_id": 777, "q": 2}]
    assert progress_events == [tool_progress_text("vkusvill_cart_link_create")]
    assert agent.captured_calls and agent.captured_calls[0][0] == "vkusvill_cart_link_create"


@pytest.mark.asyncio
async def test_execute_tool_calls_marks_recipe_flow_and_sanitizes_ingredients() -> None:
    recipe_result = json.dumps(
        {
            "ok": True,
            "ingredients": [
                {"name": "свекла", "search_query": "свекла", "quantity": 1, "unit": "шт"},
                {"name": "соль", "search_query": "соль", "quantity": 1, "unit": "ч.л."},
            ],
        },
        ensure_ascii=False,
    )
    state = _build_state(explicit_pantry_requests=set())
    agent = _FakeAgent(tool_results={"recipe_ingredients": recipe_result})

    async def _on_progress(_text: str) -> None:
        return None

    await execute_tool_calls(
        agent=agent,
        state=state,
        message={"content": "", "tool_calls": []},
        tool_calls=[{"id": "tc-3", "name": "recipe_ingredients", "arguments": '{"dish":"борщ"}'}],
        user_id=3,
        text="ингредиенты для борща",
        llm_provider="qwen_openai",
        trace=None,
        on_progress=_on_progress,
    )

    assert state.recipe_flow_started_this_turn is True
    tool_messages = [msg for msg in state.history if msg.get("role") == "tool"]
    assert tool_messages
    payload = json.loads(tool_messages[-1]["content"])
    names = [str(row.get("name", "")).lower() for row in payload.get("ingredients", [])]
    assert "соль" not in names
    assert "свекла" in names

    assert len(state.requested_ingredients) >= 1
    req_names = [str(r.get("name", "")).lower() for r in state.requested_ingredients]
    assert "свекла" in req_names


@pytest.mark.asyncio
async def test_recipe_ingredients_enriches_requested_ingredients_garlic_quantity() -> None:
    """Garlic '2 зубчика' should map to q=1 (not q=2) for a 100g pack."""
    recipe_result = json.dumps(
        {
            "ok": True,
            "ingredients": [
                {"name": "чеснок", "quantity": 2, "unit": "зубчик", "search_query": "чеснок"},
                {"name": "говядина", "quantity": 0.5, "unit": "кг", "search_query": "говядина"},
            ],
        },
        ensure_ascii=False,
    )
    garlic_product = {
        "xml_id": 555,
        "name": "Чеснок Фермерский,100 г",
        "price": {"current": 150},
        "unit": "шт",
    }
    cart_result = json.dumps(
        {"ok": True, "data": {"link": "https://shop.example/cart/xyz"}},
        ensure_ascii=False,
    )
    state = _build_state(
        product_index_this_turn={555: garlic_product},
        search_query_by_xml_id_this_turn={555: "чеснок"},
    )
    agent = _FakeAgent(
        tool_results={
            "recipe_ingredients": recipe_result,
            "vkusvill_cart_link_create": cart_result,
        },
    )

    async def _on_progress(_text: str) -> None:
        return None

    await execute_tool_calls(
        agent=agent,
        state=state,
        message={"content": "", "tool_calls": []},
        tool_calls=[
            {"id": "tc-r", "name": "recipe_ingredients", "arguments": '{"dish":"лагман"}'},
        ],
        user_id=4,
        text="приготовь лагман",
        llm_provider="qwen_openai",
        trace=None,
        on_progress=_on_progress,
    )

    assert len(state.requested_ingredients) == 2
    garlic_row = next(r for r in state.requested_ingredients if "чеснок" in r.get("name", ""))
    assert garlic_row["quantity"] == 2
    assert garlic_row["unit"] == "зубчик"

    await execute_tool_calls(
        agent=agent,
        state=state,
        message={"content": "", "tool_calls": []},
        tool_calls=[
            {
                "id": "tc-c",
                "name": "vkusvill_cart_link_create",
                "arguments": json.dumps({"products": [{"xml_id": 555, "q": 2}]}),
            },
        ],
        user_id=4,
        text="приготовь лагман",
        llm_provider="qwen_openai",
        trace=None,
        on_progress=_on_progress,
    )

    cart_call_args = agent.mcp_calls[-1][1]
    garlic_in_cart = next(p for p in cart_call_args["products"] if p.get("xml_id") == 555)
    assert garlic_in_cart["q"] == 1, (
        f"2 cloves of garlic should map to q=1 for 100g pack, got q={garlic_in_cart['q']}"
    )


def test_apply_post_step_recovery_hints_appends_expected_system_hints(monkeypatch: Any) -> None:
    state = _build_state(
        history=[{"role": "user", "content": "собери корзину"}],
        recipe_flow_started_this_turn=True,
        single_search_steps_streak=3,
    )
    agent = _FakeAgent(tool_results={})

    monkeypatch.setattr(
        "vkuswill_bot.agents.shopping_tool_recovery.should_force_recipe_to_cart_hint",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        "vkuswill_bot.agents.shopping_tool_recovery.should_force_batch_search_hint",
        lambda **_kwargs: True,
    )

    apply_post_step_recovery_hints(agent=agent, state=state, step=2, max_tool_calls=5)

    assert state.recipe_to_cart_recovery_used is True
    assert state.search_batch_recovery_used is True
    system_messages = [msg for msg in state.history if msg.get("role") == "system"]
    assert system_messages[-2]["content"] == FORCE_RECIPE_TO_CART_HINT
    assert system_messages[-1]["content"] == FORCE_BATCH_SEARCH_HINT
    assert agent.normalize_calls == 2


def test_apply_post_step_recovery_hints_keeps_history_when_no_conditions(monkeypatch: Any) -> None:
    initial_history = [{"role": "user", "content": "x"}]
    state = _build_state(history=list(initial_history))
    agent = _FakeAgent(tool_results={})

    monkeypatch.setattr(
        "vkuswill_bot.agents.shopping_tool_recovery.should_force_recipe_to_cart_hint",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        "vkuswill_bot.agents.shopping_tool_recovery.should_force_batch_search_hint",
        lambda **_kwargs: False,
    )

    apply_post_step_recovery_hints(agent=agent, state=state, step=2, max_tool_calls=5)

    assert state.history == initial_history
    assert state.recipe_to_cart_recovery_used is False
    assert state.search_batch_recovery_used is False
    assert agent.normalize_calls == 0
