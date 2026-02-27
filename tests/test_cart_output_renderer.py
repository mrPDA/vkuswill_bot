"""Unit-тесты для vkuswill_bot.agents.cart_output_renderer."""

from __future__ import annotations

from vkuswill_bot.agents.cart_output_renderer import (
    extract_cart_safety_note,
    extract_first_url,
    looks_like_missing_cart_prices,
)


def test_extract_first_url_strips_trailing_punctuation() -> None:
    text = "Откройте ссылку: https://shop.example/cart/12345)."
    assert extract_first_url(text) == "https://shop.example/cart/12345"


def test_extract_cart_safety_note_returns_allergy_sentence() -> None:
    text = (
        "Собрала корзину.\n"
        "Учитывайте возможную аллергию и индивидуальную непереносимость компонентов.\n"
        "https://shop.example/cart/12345"
    )
    note = extract_cart_safety_note(text)
    assert "аллерг" in note.lower()


def test_looks_like_missing_cart_prices_detects_missing_rows() -> None:
    summary = {
        "items": ["1. Молоко x 1 = 100 руб"],
        "total_text": "Итого: 100 руб",
    }
    assert looks_like_missing_cart_prices("Собрала корзину.", summary=summary) is True


def test_looks_like_missing_cart_prices_accepts_priced_rows_and_total() -> None:
    summary = {
        "items": ["1. Молоко x 1 = 100 руб"],
        "total_text": "Итого: 100 руб",
    }
    text = "1. Молоко x 1 = 100 руб\n\nИтого: 100 руб"
    assert looks_like_missing_cart_prices(text, summary=summary) is False
