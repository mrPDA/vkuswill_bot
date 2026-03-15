"""Tests for shared Telegram delivery preview helpers."""

from __future__ import annotations

from vkuswill_bot.bot.telegram_delivery import (
    MAX_TELEGRAM_MESSAGE_LENGTH,
    build_telegram_delivery_preview,
)


def test_build_delivery_preview_sanitizes_extracts_button_and_splits() -> None:
    response = (
        "<script>alert(1)</script>\n\n"
        "Корзина готова!\n"
        '<a href="https://vkusvill.ru/?share_basket=123">Открыть корзину</a>\n\n'
        + ("Картофель " * 600)
    )

    preview = build_telegram_delivery_preview(response)

    assert "&lt;script&gt;" in preview.sanitized_text
    assert "share_basket=123" not in preview.clean_text
    assert preview.has_cart_button is True
    assert len(preview.chunks) >= 2
    assert all(len(chunk) <= MAX_TELEGRAM_MESSAGE_LENGTH for chunk in preview.chunks)


def test_build_delivery_preview_keeps_plain_text_without_keyboard() -> None:
    preview = build_telegram_delivery_preview("Просто текст без корзины")

    assert preview.clean_text == "Просто текст без корзины"
    assert preview.chunks == ["Просто текст без корзины"]
    assert preview.has_cart_button is False
    assert preview.total_lines == 1
