"""Helpers for preparing HTML replies for Telegram delivery."""

from __future__ import annotations

from dataclasses import dataclass
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# Максимальная длина одного сообщения в Telegram
MAX_TELEGRAM_MESSAGE_LENGTH = 4096

# Теги, которые поддерживает Telegram Bot API в ParseMode.HTML
_ALLOWED_TAGS = frozenset(
    {
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "code",
        "pre",
        "a",
        "blockquote",
        "tg-spoiler",
        "tg-emoji",
    }
)

# Regex: находит все HTML-теги  <tag ...>, </tag>, <tag/>
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^>]*)?)(/?\s*)>")

# Regex: валидирует атрибут href с http/https URL (для <a>)
_SAFE_HREF_RE = re.compile(r'^\s+href\s*=\s*"https?://[^"]*"\s*$')

# Regex: извлекает URL из ссылки «Открыть корзину» в ответе LLM
_CART_LINK_RE = re.compile(
    r'<a\s+href="(https?://[^"]+)"[^>]*>[^<]*(?:корзин|[Cc]art)[^<]*</a>',
    re.IGNORECASE,
)


@dataclass(slots=True)
class TelegramDeliveryPreview:
    """Final Telegram-visible representation of a reply."""

    sanitized_text: str
    clean_text: str
    chunks: list[str]
    cart_keyboard: InlineKeyboardMarkup | None
    total_chars: int
    total_lines: int

    @property
    def has_cart_button(self) -> bool:
        return self.cart_keyboard is not None


def _sanitize_telegram_html(text: str) -> str:
    """Оставить только безопасные Telegram HTML-теги."""

    def _check_tag(match: re.Match[str]) -> str:
        full = match.group(0)
        closing = match.group(1)
        tag = match.group(2).lower()
        attrs = match.group(3)

        if tag not in _ALLOWED_TAGS:
            return full.replace("<", "&lt;").replace(">", "&gt;")

        if closing:
            return full

        if tag == "a" and attrs.strip():
            if not _SAFE_HREF_RE.match(attrs):
                return full.replace("<", "&lt;").replace(">", "&gt;")
            return full

        if attrs.strip():
            return f"<{tag}>"

        return full

    return _TAG_RE.sub(_check_tag, text)


def _extract_cart_link(text: str) -> tuple[str, InlineKeyboardMarkup | None]:
    """Извлечь ссылку корзины в inline-кнопку."""

    match = _CART_LINK_RE.search(text)
    if not match:
        return text, None
    cart_url = match.group(1)

    cleaned = _CART_LINK_RE.sub("", text)
    cleaned = re.sub(r"[\U0001f6d2\U0001f6d2]\s*\n*", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\U0001f6d2 Открыть корзину",
                    url=cart_url,
                ),
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f44d Подобрано хорошо",
                    callback_data="cart_fb_pos",
                ),
                InlineKeyboardButton(
                    text="\U0001f44e Не то",
                    callback_data="cart_fb_neg",
                ),
            ],
        ],
    )
    return cleaned, keyboard


def _split_message(text: str, max_length: int) -> list[str]:
    """Разбить длинное сообщение на части для Telegram."""
    if len(text) <= max_length:
        return [text]

    chunks: list[str] = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        split_pos = text.rfind("\n\n", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind("\n", 0, max_length)
        if split_pos == -1:
            split_pos = text.rfind(" ", 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip()

    return chunks


def build_telegram_delivery_preview(response_text: str) -> TelegramDeliveryPreview:
    """Apply the same transformations as Telegram runtime delivery."""
    sanitized_text = _sanitize_telegram_html(response_text)
    clean_text, cart_keyboard = _extract_cart_link(sanitized_text)
    chunks = _split_message(clean_text, MAX_TELEGRAM_MESSAGE_LENGTH)
    return TelegramDeliveryPreview(
        sanitized_text=sanitized_text,
        clean_text=clean_text,
        chunks=chunks,
        cart_keyboard=cart_keyboard,
        total_chars=len(clean_text),
        total_lines=len(clean_text.splitlines()) if clean_text else 0,
    )
