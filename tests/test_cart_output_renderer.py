"""Unit-тесты для vkuswill_bot.agents.cart_output_renderer."""

from __future__ import annotations

from vkuswill_bot.agents.cart_output_renderer import (
    extract_cart_safety_note,
    extract_first_url,
    extract_llm_preamble,
    extract_llm_postamble,
    extract_llm_surrounding_text,
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


def test_extract_llm_preamble_returns_text_before_cart() -> None:
    text = (
        "А вот и шутка! Почему помидор покраснел? Потому что увидел салат!\n\n"
        "Собрала корзину по вашему запросу:\n"
        "1. Молоко x 1 = 100 руб"
    )
    preamble = extract_llm_preamble(text)
    assert "шутка" in preamble.lower()
    assert "корзин" not in preamble.lower()


def test_extract_llm_preamble_returns_empty_when_no_preamble() -> None:
    text = "1. Молоко x 1 = 100 руб\nИтого: 100 руб"
    assert extract_llm_preamble(text) == ""


def test_extract_llm_preamble_returns_empty_for_short_text() -> None:
    text = "Ок\n1. Молоко x 1 = 100 руб"
    assert extract_llm_preamble(text) == ""


def test_extract_llm_postamble_returns_joke_after_cart() -> None:
    text = (
        "Собрала корзину по вашему запросу:\n"
        "1. Молоко Parmalat x 3 = 573 руб\n\n"
        "Итого: 573 руб\n\n"
        '<a href="https://cart.vkusvill.ru/123">Открыть корзину</a>\n\n'
        "А теперь шутка:\n"
        "Почему безлактозное молоко не участвует в спорах?\n"
        "Потому что оно всегда комфортно! 😄"
    )
    postamble = extract_llm_postamble(text)
    assert "шутка" in postamble.lower()
    assert "комфортно" in postamble.lower()


def test_extract_llm_postamble_returns_empty_when_no_text_after_cart() -> None:
    text = (
        "1. Молоко x 1 = 100 руб\n"
        "Итого: 100 руб\n"
        '<a href="https://cart.vkusvill.ru/123">Открыть корзину</a>'
    )
    assert extract_llm_postamble(text) == ""


def test_extract_llm_surrounding_text_both_parts() -> None:
    text = (
        "Привет! Вот что я нашла.\n\n"
        "Собрала корзину:\n"
        "1. Молоко x 1 = 100 руб\n"
        "Итого: 100 руб\n"
        '<a href="https://cart.vkusvill.ru/1">Открыть корзину</a>\n\n'
        "Приятных покупок! Обращайтесь ещё."
    )
    preamble, postamble = extract_llm_surrounding_text(text)
    assert "привет" in preamble.lower()
    assert "приятных" in postamble.lower()


def test_looks_like_missing_cart_prices_accepts_priced_rows_and_total() -> None:
    summary = {
        "items": ["1. Молоко x 1 = 100 руб"],
        "total_text": "Итого: 100 руб",
    }
    text = "1. Молоко x 1 = 100 руб\n\nИтого: 100 руб"
    assert looks_like_missing_cart_prices(text, summary=summary) is False
