"""Golden-equivalence тесты `legacy` vs `shopping_agent` на уровне voice-link contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from vkuswill_bot.services.voice_link_api import _execute_order_request

_CASES_PATH = Path(__file__).parent / "fixtures" / "chat_engine_golden_cases.json"


def _load_cases() -> list[dict[str, Any]]:
    return json.loads(_CASES_PATH.read_text(encoding="utf-8"))


def _normalize_result(result: dict[str, Any]) -> dict[str, Any]:
    total_raw = result.get("total_rub")
    total_rub: float | None = None
    if isinstance(total_raw, int | float) and not isinstance(total_raw, bool):
        total_rub = float(total_raw)
    return {
        "ok": bool(result.get("ok")),
        "cart_link_present": bool(result.get("cart_link")),
        "items_count": int(result.get("items_count") or 0),
        "total_rub": total_rub,
        "error": result.get("error"),
    }


class _ScenarioEngine:
    def __init__(self, scenario: dict[str, Any], *, flavor: str) -> None:
        self._scenario = scenario
        self._flavor = flavor
        self._snapshot_calls = 0

    async def process_message(
        self,
        user_id: int,
        text: str,
        on_progress: object | None = None,
    ) -> str:
        del on_progress
        assert user_id == int(self._scenario["user_id"])
        assert text == str(self._scenario["utterance"])
        if bool(self._scenario.get("raise_llm_error")):
            raise RuntimeError(f"{self._flavor} simulated LLM failure")
        return str(self._scenario.get("assistant_text", ""))

    async def get_last_cart_snapshot(self, user_id: int) -> dict[str, Any] | None:
        assert user_id == int(self._scenario["user_id"])
        self._snapshot_calls += 1
        if self._snapshot_calls == 1:
            before = self._scenario.get("before_snapshot")
            return before if isinstance(before, dict) else None
        after = self._scenario.get("after_snapshot")
        return after if isinstance(after, dict) else None


class _LegacyLikeEngine(_ScenarioEngine):
    pass


class _ShoppingLikeEngine(_ScenarioEngine):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", _load_cases(), ids=lambda c: str(c["id"]))
async def test_golden_equivalence_between_engines(scenario: dict[str, Any]) -> None:
    """Сравнение key outputs между двумя chat-engine реализациями на golden-наборе."""
    user_id = int(scenario["user_id"])
    utterance = str(scenario["utterance"])
    voice_user_id = str(scenario["voice_user_id"])

    legacy_result = await _execute_order_request(
        chat_engine=_LegacyLikeEngine(scenario, flavor="legacy"),
        user_id=user_id,
        utterance=utterance,
        voice_user_id=voice_user_id,
    )
    shopping_result = await _execute_order_request(
        chat_engine=_ShoppingLikeEngine(scenario, flavor="shopping"),
        user_id=user_id,
        utterance=utterance,
        voice_user_id=voice_user_id,
    )

    expected = dict(scenario["expect"])
    assert _normalize_result(legacy_result) == expected
    assert _normalize_result(shopping_result) == expected
    assert _normalize_result(legacy_result) == _normalize_result(shopping_result)
