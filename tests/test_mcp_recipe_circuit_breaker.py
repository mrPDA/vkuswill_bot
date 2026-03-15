"""Tests for MCP recipe_ingredients circuit breaker."""

from __future__ import annotations

import time
from unittest.mock import patch

from vkuswill_bot.agents.meal_plan_ingredient_collection import _McpRecipeCircuitBreaker


class TestCircuitBreakerStates:
    """Verify CLOSED -> OPEN -> auto-reset lifecycle."""

    def test_starts_closed(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        assert cb.is_open is False

    def test_stays_closed_below_threshold(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False

    def test_opens_at_threshold(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open is True

    def test_opens_above_threshold(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        for _ in range(5):
            cb.record_failure()
        assert cb.is_open is True

    def test_success_resets_from_closed(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False

    def test_success_resets_counter_completely(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=3, cooldown_seconds=60.0)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.is_open is False
        cb.record_failure()
        assert cb.is_open is False


class TestCircuitBreakerCooldown:
    """Verify auto-reset after cooldown expiry."""

    def test_resets_after_cooldown(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=2, cooldown_seconds=10.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True

        future = time.monotonic() + 11.0
        with patch("vkuswill_bot.agents.meal_plan_ingredient_collection.time") as mock_time:
            mock_time.monotonic.return_value = future
            assert cb.is_open is False

    def test_stays_open_before_cooldown(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=2, cooldown_seconds=10.0)
        cb.record_failure()
        cb.record_failure()

        still_within = time.monotonic() + 5.0
        with patch("vkuswill_bot.agents.meal_plan_ingredient_collection.time") as mock_time:
            mock_time.monotonic.return_value = still_within
            assert cb.is_open is True

    def test_reopens_after_cooldown_reset_and_new_failures(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=2, cooldown_seconds=10.0)
        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True

        future = time.monotonic() + 11.0
        with patch("vkuswill_bot.agents.meal_plan_ingredient_collection.time") as mock_time:
            mock_time.monotonic.return_value = future
            assert cb.is_open is False

        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is True


class TestCircuitBreakerThresholdOne:
    """Edge case: threshold=1 opens immediately."""

    def test_single_failure_opens(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=1, cooldown_seconds=5.0)
        cb.record_failure()
        assert cb.is_open is True

    def test_single_success_resets(self) -> None:
        cb = _McpRecipeCircuitBreaker(threshold=1, cooldown_seconds=5.0)
        cb.record_failure()
        cb.record_success()
        assert cb.is_open is False
