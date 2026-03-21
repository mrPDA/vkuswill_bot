#!/usr/bin/env python3
"""Run TC-* response-contract scenarios against a live local ShoppingAgent runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from vkuswill_bot.agents.intent_classifier import _CLASSIFY_PROMPT_STUB
from vkuswill_bot.bot.telegram_delivery import build_telegram_delivery_preview
from vkuswill_bot.config import config
from vkuswill_bot.services.chat_engine_factory import create_chat_engine
from vkuswill_bot.services.langfuse_tracing import LangfuseService
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.preferences_store import PreferencesStore
from vkuswill_bot.services.prompt_registry import init_registry
from vkuswill_bot.services.prompts import (
    _FALLBACK_MODES,
    _FALLBACK_PROFILE_CORE,
    _FALLBACK_PROFILES,
    _FALLBACK_RECIPE_PROMPT,
    _FALLBACK_SYSTEM_PROMPT,
)
from vkuswill_bot.testing.response_contract_cases import SCENARIOS, StageScenario

if TYPE_CHECKING:
    import asyncpg

    from vkuswill_bot.services.user_store import UserStore


class _DialogManager:
    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}

    def get_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]


@dataclass(slots=True)
class ScenarioRunResult:
    case_id: str
    status: str
    outcome: str
    duration_seconds: float
    error: str | None = None
    response_excerpt: str = ""
    items_count: int = 0
    prompt_profile: str | None = None
    cart_link_present: bool = False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run TC-* response-contract scenarios against the current local ShoppingAgent "
            "runtime (same env/prompt registry as the bot container)."
        )
    )
    parser.add_argument(
        "--status",
        choices=("stable", "known_issue", "all"),
        default="stable",
        help="Which scenario subset to run.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        help="Specific TC-* case id to run. Can be repeated.",
    )
    parser.add_argument(
        "--max-scenarios",
        type=int,
        default=0,
        help="Optional hard limit after filtering (0 = no limit).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Per-turn timeout for agent.process_message.",
    )
    parser.add_argument(
        "--report-json",
        default="",
        help="Optional path to save a JSON report.",
    )
    parser.add_argument(
        "--with-user-store",
        action="store_true",
        help="Connect PostgreSQL UserStore when DATABASE_URL is available.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print one line per scenario while running.",
    )
    return parser.parse_args()


def _select_scenarios(args: argparse.Namespace) -> list[StageScenario]:
    selected = list(SCENARIOS)
    if args.status != "all":
        selected = [scenario for scenario in selected if scenario.status == args.status]
    if args.case:
        requested = {case.strip() for case in args.case if case.strip()}
        selected = [scenario for scenario in selected if scenario.case_id in requested]
    if args.max_scenarios > 0:
        selected = selected[: args.max_scenarios]
    return selected


def _snapshot_items_count(snapshot: dict[str, Any] | None) -> int:
    if not isinstance(snapshot, dict):
        return 0
    direct = snapshot.get("items_count")
    if isinstance(direct, int) and direct >= 0:
        return direct
    if isinstance(direct, float) and direct.is_integer() and direct >= 0:
        return int(direct)
    price_summary = snapshot.get("price_summary")
    if isinstance(price_summary, dict):
        count = price_summary.get("count")
        if isinstance(count, int) and count >= 0:
            return count
    products = snapshot.get("products")
    return len(products) if isinstance(products, list) else 0


def _flatten_product_names(snapshot: dict[str, Any] | None) -> list[str]:
    if not isinstance(snapshot, dict):
        return []

    names: list[str] = []

    def _collect(products: object) -> None:
        if not isinstance(products, list):
            return
        for product in products:
            if isinstance(product, dict) and isinstance(product.get("name"), str):
                names.append(product["name"].lower())

    _collect(snapshot.get("products"))
    groups = snapshot.get("groups")
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict):
                _collect(group.get("products"))
    return names


def _extract_product_names_from_response(response_text: str) -> list[str]:
    names: list[str] = []
    for line in response_text.splitlines():
        stripped = line.strip()
        if ". " not in stripped or " x " not in stripped:
            continue
        prefix, _, suffix = stripped.partition(". ")
        if not prefix.isdigit():
            continue
        name, _, _qty = suffix.partition(" x ")
        cleaned = name.strip().lower()
        if cleaned:
            names.append(cleaned)
    return names


def _assert_response_contract(result: dict[str, Any], scenario: StageScenario) -> None:
    response_text = str(result.get("response") or "")
    assert response_text.strip(), "Empty response"

    preview = build_telegram_delivery_preview(response_text)
    contract = scenario.contract
    diagnostics = result.get("diagnostics")
    snapshot = result.get("cart_snapshot")
    items_count = int(result.get("items_count") or 0)
    response_text_lower = preview.clean_text.lower()
    product_names = _flatten_product_names(snapshot) or _extract_product_names_from_response(
        preview.clean_text
    )

    if contract.expected_profile is not None:
        assert isinstance(diagnostics, dict), result
        assert diagnostics.get("prompt_profile") == contract.expected_profile

    if contract.requires_cart_button is not None:
        assert preview.has_cart_button is contract.requires_cart_button

    if contract.max_chunks is not None:
        assert len(preview.chunks) <= contract.max_chunks

    if contract.max_chars_total is not None:
        assert preview.total_chars <= contract.max_chars_total

    if contract.max_lines_total is not None:
        assert preview.total_lines <= contract.max_lines_total

    if contract.min_items_count is not None:
        assert items_count >= contract.min_items_count

    if contract.max_items_count is not None:
        assert items_count <= contract.max_items_count

    for value in contract.must_contain:
        assert value.lower() in response_text_lower

    if contract.must_contain_any:
        assert any(value.lower() in response_text_lower for value in contract.must_contain_any)

    for value in contract.must_not_contain:
        assert value.lower() not in response_text_lower

    for expected in contract.required_products:
        assert any(expected.lower() in name for name in product_names), product_names

    for forbidden in contract.forbidden_products:
        assert all(forbidden.lower() not in name for name in product_names), product_names


async def _build_runtime(
    *, with_user_store: bool
) -> tuple[Any, VkusvillMCPClient, PreferencesStore, asyncpg.Pool | None]:
    import asyncpg

    from vkuswill_bot.services.user_store import UserStore

    pg_pool: asyncpg.Pool | None = None
    user_store: UserStore | None = None
    if with_user_store and config.database_url.strip():
        pg_pool = await asyncpg.create_pool(
            dsn=config.database_url,
            min_size=config.db_pool_min,
            max_size=config.db_pool_max,
        )
        user_store = UserStore(pg_pool, schema_ready=True)

    mcp_client = VkusvillMCPClient(
        config.mcp_server_url,
        api_key=config.mcp_server_api_key,
    )
    prefs_store = PreferencesStore(config.database_path)
    langfuse_service = LangfuseService(
        enabled=config.langfuse_enabled,
        public_key=config.langfuse_public_key,
        secret_key=config.langfuse_secret_key,
        host=config.langfuse_host,
        anonymize_messages=config.langfuse_anonymize_messages,
        environment=config.prompt_label,
    )

    env_overrides: dict[str, str] = {}
    if config.system_prompt:
        env_overrides["system-prompt"] = config.system_prompt
    if config.recipe_extraction_prompt:
        env_overrides["recipe-extraction"] = config.recipe_extraction_prompt

    prompt_registry = init_registry(
        langfuse_client=langfuse_service.client,
        cache_ttl_seconds=config.prompt_cache_ttl_seconds,
        label=config.prompt_label,
        env_overrides=env_overrides or None,
    )
    prompt_registry.register_fallbacks(
        {
            "system-prompt": _FALLBACK_SYSTEM_PROMPT,
            "recipe-extraction": _FALLBACK_RECIPE_PROMPT,
            "profile-core": _FALLBACK_PROFILE_CORE,
            **{f"profile-{name}": value for name, value in _FALLBACK_PROFILES.items()},
            **{f"mode-{name}": value for name, value in _FALLBACK_MODES.items()},
            "classify-intent": _CLASSIFY_PROMPT_STUB,
        }
    )

    engine = create_chat_engine(
        cfg=config,
        mcp_client=mcp_client,
        preferences_store=prefs_store,
        recipe_store=None,
        dialog_manager=_DialogManager(),
        tool_executor=None,
        langfuse_service=langfuse_service,
        user_store=user_store,
    )
    return engine, mcp_client, prefs_store, pg_pool


async def _run_scenario(
    *,
    engine: Any,
    scenario: StageScenario,
    timeout_seconds: float,
) -> ScenarioRunResult:
    started_at = time.perf_counter()
    response_text = ""
    cart_snapshot: dict[str, Any] | None = None
    diagnostics: dict[str, Any] | None = None

    try:
        await engine.reset_conversation(scenario.user_id)
        for turn in scenario.turns:
            response_text = await asyncio.wait_for(
                engine.process_message(user_id=scenario.user_id, text=turn),
                timeout=timeout_seconds,
            )

        diagnostics = await engine.get_last_turn_diagnostics(scenario.user_id)
        cart_snapshot = await engine.get_last_cart_snapshot(scenario.user_id)
        result = {
            "response": response_text,
            "diagnostics": diagnostics,
            "cart_snapshot": cart_snapshot,
            "items_count": _snapshot_items_count(cart_snapshot),
        }
        _assert_response_contract(result, scenario)
    except Exception as exc:
        outcome = "xfail" if scenario.known_issue else "fail"
        return ScenarioRunResult(
            case_id=scenario.case_id,
            status=scenario.status,
            outcome=outcome,
            duration_seconds=round(time.perf_counter() - started_at, 2),
            error=f"{type(exc).__name__}: {exc}",
            response_excerpt=response_text[:300],
            items_count=_snapshot_items_count(cart_snapshot),
            prompt_profile=(
                str(diagnostics.get("prompt_profile"))
                if isinstance(diagnostics, dict) and diagnostics.get("prompt_profile") is not None
                else None
            ),
            cart_link_present=bool(
                isinstance(cart_snapshot, dict) and str(cart_snapshot.get("link", "")).strip()
            ),
        )

    outcome = "xpass" if scenario.known_issue else "pass"
    return ScenarioRunResult(
        case_id=scenario.case_id,
        status=scenario.status,
        outcome=outcome,
        duration_seconds=round(time.perf_counter() - started_at, 2),
        response_excerpt=response_text[:300],
        items_count=_snapshot_items_count(cart_snapshot),
        prompt_profile=(
            str(diagnostics.get("prompt_profile"))
            if isinstance(diagnostics, dict) and diagnostics.get("prompt_profile") is not None
            else None
        ),
        cart_link_present=bool(
            isinstance(cart_snapshot, dict) and str(cart_snapshot.get("link", "")).strip()
        ),
    )


async def _amain(args: argparse.Namespace) -> int:
    scenarios = _select_scenarios(args)
    if not scenarios:
        print("No scenarios selected.", file=sys.stderr)
        return 2

    engine, mcp_client, prefs_store, pg_pool = await _build_runtime(
        with_user_store=args.with_user_store
    )
    try:
        results: list[ScenarioRunResult] = []
        for scenario in scenarios:
            result = await _run_scenario(
                engine=engine,
                scenario=scenario,
                timeout_seconds=args.timeout_seconds,
            )
            results.append(result)
            if args.verbose:
                suffix = f" error={result.error}" if result.error else ""
                print(
                    f"{result.case_id}: {result.outcome} "
                    f"({result.duration_seconds:.2f}s, profile={result.prompt_profile}, "
                    f"items={result.items_count}){suffix}"
                )

        summary = {
            "selected": len(results),
            "pass": sum(result.outcome == "pass" for result in results),
            "fail": sum(result.outcome == "fail" for result in results),
            "xfail": sum(result.outcome == "xfail" for result in results),
            "xpass": sum(result.outcome == "xpass" for result in results),
            "results": [asdict(result) for result in results],
        }

        if args.report_json:
            with open(args.report_json, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, ensure_ascii=False, indent=2)

        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if summary["fail"] else 0
    finally:
        await engine.close()
        await prefs_store.close()
        await mcp_client.close()
        if pg_pool is not None:
            await pg_pool.close()


def main() -> int:
    args = _parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
