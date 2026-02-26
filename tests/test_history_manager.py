"""Unit-тесты для vkuswill_bot.agents.history_manager."""

from __future__ import annotations

import json


from vkuswill_bot.agents.history_manager import (
    history_char_count,
    sanitize_tool_history,
    trim_history,
)


# ── trim_history ──────────────────────────────────────────────────


class TestTrimHistory:
    def test_no_trim_when_under_limit(self) -> None:
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        result = trim_history(history, max_history=10)
        assert result is history

    def test_trims_keeping_system_first(self) -> None:
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
        ]
        result = trim_history(history, max_history=3)
        assert len(result) == 3
        assert result[0]["role"] == "system"
        assert result[-1]["content"] == "resp2"


# ── sanitize_tool_history ─────────────────────────────────────────


class TestSanitizeToolHistory:
    def test_removes_orphan_tool_messages(self) -> None:
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "tool", "name": "search", "content": "result"},
        ]
        result = sanitize_tool_history(history)
        # Tool without preceding assistant+tool_calls is dropped.
        assert len(result) == 2
        assert result[0]["role"] == "system"
        assert result[1]["role"] == "user"

    def test_keeps_tool_after_assistant_with_tool_calls(self) -> None:
        history = [
            {"role": "system", "content": "sys"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "tc1", "function": {"name": "search"}}],
            },
            {"role": "tool", "name": "search", "content": "result"},
        ]
        result = sanitize_tool_history(history)
        assert len(result) == 3

    def test_short_history_unchanged(self) -> None:
        history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        assert sanitize_tool_history(history) is history


# ── history_char_count ────────────────────────────────────────────


class TestHistoryCharCount:
    def test_counts_serialized_chars(self) -> None:
        history = [{"role": "user", "content": "Привет"}]
        count = history_char_count(history)
        expected = len(json.dumps(history[0], ensure_ascii=False))
        assert count == expected

    def test_empty_history(self) -> None:
        assert history_char_count([]) == 0

    def test_multiple_messages(self) -> None:
        history = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
        ]
        count = history_char_count(history)
        expected = sum(len(json.dumps(m, ensure_ascii=False)) for m in history)
        assert count == expected
