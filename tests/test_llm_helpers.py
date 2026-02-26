"""Unit-тесты для vkuswill_bot.agents.llm_helpers."""

from __future__ import annotations

from types import SimpleNamespace


from vkuswill_bot.agents.llm_helpers import (
    assistant_msg,
    estimate_usage_details,
    extract_message,
    extract_text,
    extract_tool_calls,
    parse_tool_args,
)


# ── extract_text ──────────────────────────────────────────────────


class TestExtractText:
    def test_string_content(self) -> None:
        assert extract_text({"content": "hello"}) == "hello"

    def test_list_content_with_text_blocks(self) -> None:
        msg = {
            "content": [
                {"type": "text", "text": "line1"},
                {"type": "image", "url": "..."},
                {"type": "text", "text": "line2"},
            ]
        }
        assert extract_text(msg) == "line1\nline2"

    def test_empty_content(self) -> None:
        assert extract_text({"content": None}) == ""
        assert extract_text({}) == ""

    def test_object_with_content_attr(self) -> None:
        msg = SimpleNamespace(content="from attr")
        assert extract_text(msg) == "from attr"

    def test_integer_content_returns_empty(self) -> None:
        assert extract_text({"content": 42}) == ""


# ── extract_tool_calls ────────────────────────────────────────────


class TestExtractToolCalls:
    def test_dict_tool_calls(self) -> None:
        msg = {
            "tool_calls": [
                {
                    "id": "tc1",
                    "function": {"name": "search", "arguments": '{"q": "milk"}'},
                }
            ]
        }
        result = extract_tool_calls(msg)
        assert len(result) == 1
        assert result[0]["id"] == "tc1"
        assert result[0]["name"] == "search"
        assert result[0]["arguments"] == '{"q": "milk"}'

    def test_object_tool_calls(self) -> None:
        fn = SimpleNamespace(name="cart", arguments='{"products": []}')
        call = SimpleNamespace(id="tc2", function=fn)
        msg = SimpleNamespace(tool_calls=[call])
        result = extract_tool_calls(msg)
        assert result[0]["name"] == "cart"

    def test_no_tool_calls(self) -> None:
        assert extract_tool_calls({"content": "text"}) == []
        assert extract_tool_calls(SimpleNamespace(tool_calls=None)) == []

    def test_empty_list(self) -> None:
        assert extract_tool_calls({"tool_calls": []}) == []


# ── assistant_msg ─────────────────────────────────────────────────


class TestAssistantMsg:
    def test_text_only(self) -> None:
        result = assistant_msg({"content": "Привет"})
        assert result == {"role": "assistant", "content": "Привет"}
        assert "tool_calls" not in result

    def test_with_tool_calls(self) -> None:
        msg = {
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        }
        result = assistant_msg(msg)
        assert result["role"] == "assistant"
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["type"] == "function"
        assert result["tool_calls"][0]["function"]["name"] == "search"


# ── parse_tool_args ───────────────────────────────────────────────


class TestParseToolArgs:
    def test_dict_passthrough(self) -> None:
        args = {"q": "молоко"}
        assert parse_tool_args(args) is args

    def test_json_string(self) -> None:
        assert parse_tool_args('{"q": "хлеб"}') == {"q": "хлеб"}

    def test_invalid_json(self) -> None:
        assert parse_tool_args("not json") == {}

    def test_non_dict_json(self) -> None:
        assert parse_tool_args("[1, 2]") == {}

    def test_none(self) -> None:
        assert parse_tool_args(None) == {}

    def test_integer(self) -> None:
        assert parse_tool_args(42) == {}


# ── extract_message ───────────────────────────────────────────────


class TestExtractMessage:
    def test_object_with_choices(self) -> None:
        message_obj = SimpleNamespace(content="hello")
        choice = SimpleNamespace(message=message_obj)
        response = SimpleNamespace(choices=[choice])
        assert extract_message(response) is message_obj

    def test_dict_response(self) -> None:
        response = {"choices": [{"message": {"content": "ok"}}]}
        assert extract_message(response) == {"content": "ok"}

    def test_empty_choices(self) -> None:
        assert extract_message(SimpleNamespace(choices=[])) == {}

    def test_no_choices(self) -> None:
        assert extract_message({"data": "irrelevant"}) == {}
        assert extract_message(42) == {}


# ── estimate_usage_details ────────────────────────────────────────


class TestEstimateUsageDetails:
    def test_basic_estimation(self) -> None:
        messages = [{"role": "user", "content": "Привет"}]
        message = {"role": "assistant", "content": "Здравствуйте!"}
        result = estimate_usage_details(messages=messages, message=message)
        assert result["input"] > 0
        assert result["output"] > 0
        assert result["total"] == result["input"] + result["output"]

    def test_empty_messages(self) -> None:
        result = estimate_usage_details(messages=[], message={})
        # Empty messages → 0 input chars, but {} serializes to 2 chars → 1 output token
        assert result["input"] == 0
        assert result["output"] >= 1
        assert result["total"] == result["input"] + result["output"]
