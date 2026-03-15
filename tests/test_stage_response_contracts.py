"""Stage-only response contract checks for user-visible replies."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import aiohttp
import pytest

from vkuswill_bot.bot.telegram_delivery import build_telegram_delivery_preview

from stage_response_contract_cases import SCENARIOS, StageScenario

pytestmark = pytest.mark.stage


@dataclass(slots=True)
class _StageConfig:
    base_url: str
    debug_api_key: str
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str
    verify_stage_ssl: bool

    @classmethod
    def from_env(cls) -> _StageConfig:
        if os.getenv("RUN_STAGE_RESPONSE_CONTRACTS") != "1":
            pytest.skip(
                "Stage response-contract tests are disabled. "
                "Set RUN_STAGE_RESPONSE_CONTRACTS=1 to enable them."
            )

        debug_api_key = os.getenv("DEBUG_API_KEY_STG", "").strip()
        if not debug_api_key:
            pytest.skip("Set DEBUG_API_KEY_STG to call the stage debug API.")

        langfuse_host = os.getenv("LANGFUSE_HOST", "").strip().rstrip("/")
        langfuse_public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
        langfuse_secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
        if not (langfuse_host and langfuse_public_key and langfuse_secret_key):
            pytest.skip(
                "Set LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY "
                "to verify that traces are really from stage."
            )

        return cls(
            base_url=os.getenv("STAGE_BASE_URL", "https://89.169.138.16").strip().rstrip("/"),
            debug_api_key=debug_api_key,
            langfuse_host=langfuse_host,
            langfuse_public_key=langfuse_public_key,
            langfuse_secret_key=langfuse_secret_key,
            verify_stage_ssl=os.getenv("STAGE_VERIFY_SSL", "").strip() == "1",
        )

    @property
    def auth_header(self) -> str:
        raw = f"{self.langfuse_public_key}:{self.langfuse_secret_key}".encode()
        return f"Basic {base64.b64encode(raw).decode()}"


def _scenario_params() -> list[object]:
    params: list[object] = []
    for scenario in SCENARIOS:
        marks: list[object] = []
        if scenario.known_issue:
            marks.append(pytest.mark.xfail(reason=scenario.known_issue, strict=False))
        params.append(pytest.param(scenario, id=scenario.case_id, marks=marks))
    return params


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


def _extract_trace_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("data"), dict):
        return payload["data"]
    if isinstance(payload.get("result"), dict):
        return payload["result"]
    return payload


def _is_stage_trace(payload: dict[str, Any]) -> bool:
    trace = _extract_trace_payload(payload)
    tags = trace.get("tags")
    if isinstance(tags, list) and "env:stage" in tags:
        return True

    metadata = trace.get("metadata")
    if not isinstance(metadata, dict):
        return False

    rollout = metadata.get("meal_plan_rollout_bypass")
    if not isinstance(rollout, dict):
        return False
    return str(rollout.get("environment", "")).strip().lower() == "staging"


async def _call_stage_debug_api(
    session: aiohttp.ClientSession,
    config: _StageConfig,
    *,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "X-Debug-Api-Key": config.debug_api_key,
    }
    async with session.post(
        f"{config.base_url}{path}",
        json=payload,
        headers=headers,
        ssl=config.verify_stage_ssl,
    ) as response:
        body = await response.json()
    assert response.status == 200, body
    return body


async def _fetch_langfuse_trace(
    session: aiohttp.ClientSession,
    config: _StageConfig,
    trace_id: str,
) -> dict[str, Any]:
    headers = {"Authorization": config.auth_header}
    async with session.get(
        f"{config.langfuse_host}/api/public/traces/{trace_id}",
        headers=headers,
    ) as response:
        body = await response.json()
    assert response.status == 200, body
    return body


def _assert_response_contract(result: dict[str, Any], scenario: StageScenario) -> None:
    response_text = str(result.get("response") or "")
    assert response_text.strip(), "Stage debug API returned an empty response."

    preview = build_telegram_delivery_preview(response_text)
    contract = scenario.contract
    diagnostics = result.get("diagnostics")
    snapshot = result.get("cart_snapshot")
    items_count = int(result.get("items_count") or 0)
    response_text_lower = preview.clean_text.lower()
    product_names = _flatten_product_names(snapshot)

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


@pytest.fixture(scope="module")
def stage_config() -> _StageConfig:
    return _StageConfig.from_env()


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", _scenario_params())
async def test_stage_response_contracts(
    stage_config: _StageConfig, scenario: StageScenario
) -> None:
    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await _call_stage_debug_api(
            session,
            stage_config,
            path="/debug/reset-history",
            payload={"user_id": scenario.user_id},
        )

        result: dict[str, Any] | None = None
        for index, turn in enumerate(scenario.turns):
            result = await _call_stage_debug_api(
                session,
                stage_config,
                path="/debug/run-shopping",
                payload={
                    "user_id": scenario.user_id,
                    "text": turn,
                    "reset_history": index == 0,
                },
            )

        assert result is not None
        _assert_response_contract(result, scenario)

        trace_id = str(result.get("trace_id") or "").strip()
        assert trace_id, result
        trace_payload = await _fetch_langfuse_trace(session, stage_config, trace_id)
        assert _is_stage_trace(trace_payload), trace_payload
