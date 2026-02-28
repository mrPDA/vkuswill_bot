"""Прямые unit-тесты pipeline-компонентов (ADR-006).

Тестируем ToolArgsPreprocessor, ToolInvoker, ToolResultPostprocessor, FreemiumCartHook
напрямую, без проксирования через ToolExecutor.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.tool_executor_pipeline import (
    ArgsPreprocessorProtocol,
    FreemiumCartHook,
    FreemiumHookProtocol,
    InvokerProtocol,
    ResultPostprocessorProtocol,
    ToolArgsPreprocessor,
    ToolInvoker,
    ToolResultPostprocessor,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def search_processor() -> SearchProcessor:
    return SearchProcessor()


@pytest.fixture
def cart_processor(search_processor: SearchProcessor) -> CartProcessor:
    return CartProcessor(search_processor.price_cache)


@pytest.fixture
def apply_preferences_to_query():
    """Подстановка предпочтений: query + pref если есть match."""

    def _apply(q: str, prefs: dict[str, str]) -> str:
        if not prefs or not q:
            return q
        pref = prefs.get(q.strip().lower())
        return f"{q} {pref}" if pref else q

    return _apply


@pytest.fixture
def find_unknown_xml_ids_empty():
    """Mock: неизвестных xml_id нет."""

    async def _find(_args: dict) -> list[int]:
        return []

    return _find


@pytest.fixture
def args_preprocessor(
    search_processor: SearchProcessor,
    cart_processor: CartProcessor,
    apply_preferences_to_query,
    find_unknown_xml_ids_empty,
) -> ToolArgsPreprocessor:
    return ToolArgsPreprocessor(
        search_processor=search_processor,
        cart_processor=cart_processor,
        apply_preferences_to_query=apply_preferences_to_query,
        find_unknown_xml_ids=find_unknown_xml_ids_empty,
    )


# ============================================================================
# Protocol conformance (ADR-006)
# ============================================================================


class TestProtocolConformance:
    """Проверка, что реализации соответствуют Protocol."""

    def test_tool_args_preprocessor_implements_protocol(
        self, args_preprocessor: ToolArgsPreprocessor
    ) -> None:
        assert isinstance(args_preprocessor, ArgsPreprocessorProtocol)

    def test_tool_invoker_implements_protocol(self) -> None:
        invoker = ToolInvoker(
            mcp_client=AsyncMock(),
            local_tool_names=frozenset(),
            prefs_store=None,
            nutrition_service=None,
            recipe_search_service=None,
            user_store=None,
            get_previous_cart=AsyncMock(return_value='{"ok": True}'),
        )
        assert isinstance(invoker, InvokerProtocol)

    def test_tool_result_postprocessor_implements_protocol(
        self, search_processor: SearchProcessor, cart_processor: CartProcessor
    ) -> None:
        postprocessor = ToolResultPostprocessor(
            search_processor=search_processor,
            cart_processor=cart_processor,
            user_store=None,
            cart_snapshot_store=None,
            parse_preferences=lambda s: {},
            is_cart_success=lambda s: "ok" in s and "error" not in s,
            add_unknown_ids_hint=lambda r, ids: r,
            add_quantity_adjustments=lambda a, r: r,
            save_cart_snapshot=AsyncMock(),
            handle_cart_created_freemium=AsyncMock(side_effect=lambda uid, a, r: r),
        )
        assert isinstance(postprocessor, ResultPostprocessorProtocol)

    def test_freemium_cart_hook_implements_protocol(self) -> None:
        hook = FreemiumCartHook(user_store=None)
        assert isinstance(hook, FreemiumHookProtocol)


# ============================================================================
# ToolArgsPreprocessor
# ============================================================================


class TestToolArgsPreprocessor:
    """Прямые тесты ToolArgsPreprocessor."""

    @pytest.mark.asyncio
    async def test_search_with_preferences(self, args_preprocessor: ToolArgsPreprocessor) -> None:
        prefs = {"молоко": "козье 3,2%"}
        args = {"q": "молоко"}
        result = await args_preprocessor.preprocess(
            tool_name="vkusvill_products_search",
            args=args,
            user_prefs=prefs,
        )
        assert result["q"] == "молоко козье 3,2%"
        assert "limit" in result

    @pytest.mark.asyncio
    async def test_search_passthrough_no_prefs(
        self, args_preprocessor: ToolArgsPreprocessor
    ) -> None:
        args = {"q": "творог"}
        result = await args_preprocessor.preprocess(
            tool_name="vkusvill_products_search",
            args=args,
            user_prefs={},
        )
        assert result["q"] == "творог"
        assert "limit" in result

    @pytest.mark.asyncio
    async def test_other_tool_passthrough(self, args_preprocessor: ToolArgsPreprocessor) -> None:
        args = {"xml_id": 123}
        result = await args_preprocessor.preprocess(
            tool_name="vkusvill_product_details",
            args=dict(args),
            user_prefs={},
        )
        assert result == {"xml_id": 123}

    @pytest.mark.asyncio
    async def test_cart_fix_quantities(
        self,
        search_processor: SearchProcessor,
        cart_processor: CartProcessor,
        apply_preferences_to_query,
        find_unknown_xml_ids_empty,
    ) -> None:
        search_processor.price_cache[100] = {
            "name": "Молоко",
            "price": 79,
            "unit": "шт",
        }
        preprocessor = ToolArgsPreprocessor(
            search_processor=search_processor,
            cart_processor=cart_processor,
            apply_preferences_to_query=apply_preferences_to_query,
            find_unknown_xml_ids=find_unknown_xml_ids_empty,
        )
        args = {"products": [{"xml_id": 100, "q": 0.5}]}
        result = await preprocessor.preprocess(
            tool_name="vkusvill_cart_link_create",
            args=args,
            user_prefs={},
        )
        assert result["products"][0]["q"] == 1
        assert "_requested_products" in result


# ============================================================================
# ToolInvoker
# ============================================================================


class TestToolInvoker:
    """Прямые тесты ToolInvoker."""

    @pytest.mark.asyncio
    async def test_mcp_tool_dispatched(self) -> None:
        mcp = AsyncMock()
        mcp.call_tool.return_value = json.dumps({"ok": True, "results": []})
        invoker = ToolInvoker(
            mcp_client=mcp,
            local_tool_names=frozenset(),
            prefs_store=None,
            nutrition_service=None,
            recipe_search_service=None,
            user_store=None,
            get_previous_cart=AsyncMock(return_value="{}"),
        )
        result = await invoker.execute(
            tool_name="vkusvill_products_search",
            args={"q": "молоко"},
            user_id=123,
        )
        mcp.call_tool.assert_called_once_with(
            "vkusvill_products_search",
            {"q": "молоко"},
        )
        assert "ok" in result

    @pytest.mark.asyncio
    async def test_local_tool_preferences_get(self) -> None:
        prefs_store = AsyncMock()
        prefs_store.get_formatted.return_value = json.dumps(
            {"ok": True, "preferences": []},
            ensure_ascii=False,
        )
        invoker = ToolInvoker(
            mcp_client=AsyncMock(),
            local_tool_names=frozenset({"user_preferences_get"}),
            prefs_store=prefs_store,
            nutrition_service=None,
            recipe_search_service=None,
            user_store=None,
            get_previous_cart=AsyncMock(return_value="{}"),
        )
        result = await invoker.execute(
            tool_name="user_preferences_get",
            args={},
            user_id=42,
        )
        prefs_store.get_formatted.assert_called_once_with(42)
        assert "ok" in result

    @pytest.mark.asyncio
    async def test_exception_returns_error_json(self) -> None:
        mcp = AsyncMock()
        mcp.call_tool.side_effect = ValueError("API unreachable")
        invoker = ToolInvoker(
            mcp_client=mcp,
            local_tool_names=frozenset(),
            prefs_store=None,
            nutrition_service=None,
            recipe_search_service=None,
            user_store=None,
            get_previous_cart=AsyncMock(return_value="{}"),
        )
        result = await invoker.execute(
            tool_name="vkusvill_products_search",
            args={"q": "x"},
            user_id=1,
        )
        parsed = json.loads(result)
        assert "error" in parsed
        assert "API unreachable" in parsed["error"] or "ValueError" in result


# ============================================================================
# ToolResultPostprocessor
# ============================================================================


class TestToolResultPostprocessor:
    """Прямые тесты ToolResultPostprocessor."""

    @pytest.fixture
    def postprocessor(
        self, search_processor: SearchProcessor, cart_processor: CartProcessor
    ) -> ToolResultPostprocessor:
        return ToolResultPostprocessor(
            search_processor=search_processor,
            cart_processor=cart_processor,
            user_store=None,
            cart_snapshot_store=None,
            parse_preferences=lambda s: {},
            is_cart_success=lambda s: "ok" in s and "error" not in s.lower(),
            add_unknown_ids_hint=lambda r, ids: r,
            add_quantity_adjustments=lambda a, r: r,
            save_cart_snapshot=AsyncMock(),
            handle_cart_created_freemium=AsyncMock(side_effect=lambda uid, a, r: r),
        )

    @pytest.mark.asyncio
    async def test_preferences_parsed_and_mutate_user_prefs(
        self,
        search_processor: SearchProcessor,
        cart_processor: CartProcessor,
    ) -> None:
        user_prefs: dict[str, str] = {}
        parsed_prefs = {"молоко": "козье", "творог": "обезжиренный"}

        def parse(s: str) -> dict[str, str]:
            return parsed_prefs if "preferences" in s else {}

        postprocessor = ToolResultPostprocessor(
            search_processor=search_processor,
            cart_processor=cart_processor,
            user_store=None,
            cart_snapshot_store=None,
            parse_preferences=parse,
            is_cart_success=lambda s: False,
            add_unknown_ids_hint=lambda r, ids: r,
            add_quantity_adjustments=lambda a, r: r,
            save_cart_snapshot=AsyncMock(),
            handle_cart_created_freemium=AsyncMock(side_effect=lambda u, a, r: r),
        )
        result = await postprocessor.postprocess(
            tool_name="user_preferences_get",
            args={},
            result='{"ok": true, "preferences": []}',
            user_prefs=user_prefs,
            search_log={},
            user_id=None,
        )
        assert user_prefs == parsed_prefs
        assert result == '{"ok": true, "preferences": []}'

    @pytest.mark.asyncio
    async def test_search_result_passthrough_for_other_tools(
        self, postprocessor: ToolResultPostprocessor
    ) -> None:
        result = await postprocessor.postprocess(
            tool_name="vkusvill_product_details",
            args={},
            result='{"ok": true}',
            user_prefs={},
            search_log={},
            user_id=None,
        )
        assert result == '{"ok": true}'


# ============================================================================
# FreemiumCartHook
# ============================================================================


class TestFreemiumCartHook:
    """Прямые тесты FreemiumCartHook."""

    @pytest.mark.asyncio
    async def test_passthrough_when_no_user_store(self) -> None:
        hook = FreemiumCartHook(user_store=None)
        result_in = '{"ok": true, "data": {}}'
        result_out = await hook.apply(
            user_id=1,
            args={"products": []},
            result=result_in,
        )
        assert result_out == result_in

    @pytest.mark.asyncio
    async def test_apply_with_user_store_adds_freemium_data(self) -> None:
        user_store = AsyncMock()
        user_store.increment_carts = AsyncMock(
            return_value={
                "carts_created": 2,
                "cart_limit": 5,
                "trial_active": False,
                "trial_days_left": 0,
                "trial_ends_at": None,
                "survey_completed": False,
            }
        )
        user_store.grant_referral_bonus_for_first_cart = AsyncMock(return_value={"granted": False})
        user_store.log_event = AsyncMock()
        hook = FreemiumCartHook(user_store=user_store)
        result_in = json.dumps(
            {"ok": True, "data": {"price_summary": {"total": 500}}},
            ensure_ascii=False,
        )
        result_out = await hook.apply(
            user_id=42,
            args={"products": [{"xml_id": 1, "q": 1}]},
            result=result_in,
        )
        parsed = json.loads(result_out)
        assert "freemium" in parsed.get("data", {})
        freemium = parsed["data"]["freemium"]
        assert freemium["cart_number"] == 2
        assert freemium["cart_limit"] == 5
        assert freemium["trial_active"] is False
