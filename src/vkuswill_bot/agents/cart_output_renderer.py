"""Рендеринг и валидация финального вывода корзины."""

from __future__ import annotations

import re
from typing import Any

from vkuswill_bot.agents.tool_result_compactor import (
    _safe_float,
    normalize_compact_text,
)

_URL_RE = re.compile(r"https?://[^\s\"<>]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_PRICED_ROW_RE = re.compile(r"(?im)^\s*\d+\.\s+.+?(?:x|×).+?=\s*[\d\s.,]+(?:₽|руб(?:\.|ля|лей)?)")
_CART_START_RE = re.compile(
    r"(?im)"
    r"(?:"
    r"^\s*\d+\.\s+.+?(?:x|×|—|\d+\s*(?:шт|г|кг|мл|л))"
    r"|(?:собрал[аи]?\s+корзин)"
    r"|(?:вот\s+(?:ваша|твоя)\s+корзин)"
    r"|(?:корзин\w*\s+готов[аы]?)"
    r"|(?:итого\s*:?\s*[\d.,]+)"
    r")",
)
_CART_END_RE = re.compile(
    r"(?im)"
    r"(?:"
    r"открыть\s+корзин"
    r"|наличие\s+и\s+точное\s+количество"
    r"|цены\s+и\s+состав\s+уточняйте"
    r"|уточняйте\s+на\s+сайте"
    r"|https?://[^\s\"<>]*(?:cart|basket|share_basket)[^\s\"<>]*"
    r"|итого\s*:?\s*[\d.,]+\s*(?:₽|руб)"
    r"|</a>"
    r")",
)


def render_stable_cart_output(
    cart_data: dict[str, Any],
    *,
    safety_note: str = "",
) -> str:
    """Отрендерить стабильный детерминированный вывод корзины (HTML для Telegram)."""
    link = str(cart_data.get("link", "")).strip()
    summary = cart_data.get("price_summary")
    summary_dict = summary if isinstance(summary, dict) else {}
    items = summary_dict.get("items")

    lines: list[str] = []
    if isinstance(items, list):
        for index, row in enumerate(items[:20], start=1):
            if not isinstance(row, str):
                continue
            normalized = normalize_compact_text(row)
            if not normalized:
                continue
            if normalized.startswith("-"):
                normalized = normalized.lstrip("-").strip()
            lines.append(f"{index}. {normalized}")

    total_text = ""
    total_text_raw = summary_dict.get("total_text")
    if isinstance(total_text_raw, str) and total_text_raw.strip():
        total_text = normalize_compact_text(total_text_raw)
    else:
        total_raw = summary_dict.get("total")
        total = _safe_float(total_raw, default=-1.0)
        if total >= 0:
            total_text = f"Итого: {total:.2f} руб"
    purchase_total_text = normalize_compact_text(summary_dict.get("purchase_total_text"))
    recipe_total_text = normalize_compact_text(summary_dict.get("recipe_total_text"))
    overbuy_total_text = normalize_compact_text(summary_dict.get("overbuy_total_text"))
    dual_pricing = bool(summary_dict.get("dual_pricing"))
    purchase_total = _safe_float(summary_dict.get("purchase_total"), default=-1.0)
    recipe_total = _safe_float(summary_dict.get("recipe_total"), default=-1.0)
    has_meaningful_dual = dual_pricing or (
        purchase_total >= 0 and recipe_total >= 0 and abs(purchase_total - recipe_total) >= 0.01
    )

    chunks = ["Собрала корзину по вашему запросу."]
    if lines:
        chunks.append("\n".join(lines))
    if total_text:
        chunks.append(f"<b>{total_text}</b>")
    if has_meaningful_dual:
        if recipe_total_text:
            chunks.append(f"<b>{recipe_total_text}</b>")
        if purchase_total_text:
            chunks.append(f"<b>{purchase_total_text}</b>")
        if overbuy_total_text:
            chunks.append(overbuy_total_text)
    cleaned_safety_note = normalize_compact_text(safety_note)
    if cleaned_safety_note:
        chunks.append(cleaned_safety_note)
    if link:
        chunks.append(f'<a href="{link}">Открыть корзину</a>')
    chunks.append(
        "<i>Наличие и точное количество товаров будет проверено при открытии ссылки на "
        "корзину. Цены и состав уточняйте на сайте.</i>"
    )
    return "\n\n".join(chunks)


def extract_llm_preamble(text: str) -> str:
    """Извлечь вступительный текст LLM до начала вывода корзины."""
    return extract_llm_surrounding_text(text)[0]


def extract_llm_postamble(text: str) -> str:
    """Извлечь текст LLM после вывода корзины (шутка, комментарий и т.п.)."""
    return extract_llm_surrounding_text(text)[1]

def _clean_fragment(raw: str) -> str:
    cleaned = normalize_compact_text(raw.strip())
    return cleaned if len(cleaned) >= 4 else ""


def extract_llm_surrounding_text(text: str) -> tuple[str, str]:
    """Извлечь текст LLM до и после вывода корзины.

    Возвращает (preamble, postamble). Пустая строка, если фрагмент
    отсутствует или короче 4 символов.
    """
    if not text or not text.strip():
        return "", ""
    preamble = ""
    start_match = _CART_START_RE.search(text)
    if start_match is not None:
        preamble = _clean_fragment(text[: start_match.start()])
    postamble = ""
    last_end = max((m.end() for m in _CART_END_RE.finditer(text)), default=-1)
    if last_end >= 0:
        postamble = _clean_fragment(text[last_end:])
    return preamble, postamble


def extract_cart_safety_note(text: str) -> str:
    """Извлечь предупреждение об аллергенах/составе из текста LLM."""
    normalized_text = normalize_compact_text(text)
    if not normalized_text:
        return ""

    candidates = _SENTENCE_SPLIT_RE.split(normalized_text)
    if not candidates:
        candidates = [normalized_text]

    keywords = (
        "аллерг",
        "состав",
        "неперенос",
        "индивидуал",
        "чувствител",
        "противопоказ",
    )
    for candidate in candidates:
        line = normalize_compact_text(candidate).strip(" -\t")
        if not line:
            continue
        line_low = line.lower()
        if "http://" in line_low or "https://" in line_low:
            continue
        if "открыть корзину" in line_low:
            continue
        if any(token in line_low for token in keywords):
            return line
    return ""


def stabilize_cart_output(*, final_text: str, cart_data: dict[str, Any]) -> str:
    """Проверить и стабилизировать финальный вывод корзины."""
    items_count = 0
    summary_dict: dict[str, Any] = {}
    summary = cart_data.get("price_summary")
    if isinstance(summary, dict):
        summary_dict = summary
        count_raw = summary.get("count")
        if isinstance(count_raw, int) and count_raw > 0:
            items_count = count_raw
        elif isinstance(count_raw, float) and count_raw.is_integer() and count_raw > 0:
            items_count = int(count_raw)
        else:
            items = summary.get("items")
            if isinstance(items, list):
                items_count = len(items)

    if looks_like_manual_cart_reply(final_text):
        return render_stable_cart_output(cart_data)
    if looks_like_wrong_cart_summary(final_text, items_count=items_count):
        return render_stable_cart_output(cart_data)
    if looks_like_wrong_cart_link(final_text, cart_data=cart_data):
        return render_stable_cart_output(cart_data)
    if looks_like_missing_cart_prices(final_text, summary=summary_dict):
        return render_stable_cart_output(cart_data)
    return final_text


def extract_first_url(text: str) -> str:
    """Извлечь первый URL из текста."""
    match = _URL_RE.search(text)
    if not match:
        return ""
    return match.group(0).strip().rstrip("\\).,;:!?]")


def looks_like_manual_cart_reply(text: str) -> bool:
    """Определить, что LLM предложил собрать корзину вручную."""
    normalized = text.lower()
    markers = (
        "вручн",
        "самостоятель",
        "соберите корзину сами",
        "оформите заказ сами",
        "собрать корзину самому",
        "добавьте товары сами",
        "перейдите на сайт",
        "в приложении соберите",
    )
    return any(marker in normalized for marker in markers)


def looks_like_cart_ready_reply(text: str) -> bool:
    """Определить, что ответ содержит собранную корзину."""
    normalized = text.lower()
    if "открыть корзину" in normalized:
        return True
    if "итого" in normalized and any(
        word in normalized for word in ("корзин", "собрал", "собрала")
    ):
        return True
    if "корзин" in normalized and any(
        word in normalized for word in ("собрал", "собрала", "собрано", "готова", "готово")
    ):
        return True
    return "share_basket" in normalized


def looks_like_wrong_cart_summary(text: str, *, items_count: int) -> bool:
    """Определить, что LLM выдал неверную сводку по корзине."""
    normalized = text.lower()
    if items_count > 0 and (
        "0 товар" in normalized
        or "0 пози" in normalized
        or "ноль товар" in normalized
        or "ноль пози" in normalized
    ):
        return True
    return "не удалось создать корзину" in normalized or "не могу создать корзину" in normalized


def looks_like_wrong_cart_link(text: str, *, cart_data: dict[str, Any]) -> bool:
    """Определить, что LLM подменил ссылку на корзину."""
    expected_link = str(cart_data.get("link", "")).strip()
    if not expected_link:
        return False
    actual_link = extract_first_url(text)
    if not actual_link:
        return True
    return actual_link != expected_link


def looks_like_missing_cart_prices(text: str, *, summary: dict[str, Any]) -> bool:
    """Определить, что финальный ответ не содержит цен по позициям корзины."""
    items = summary.get("items")
    if not isinstance(items, list) or not any(isinstance(row, str) for row in items):
        return False

    normalized = text.strip()
    if not normalized:
        return True

    has_priced_rows = bool(_PRICED_ROW_RE.search(normalized))

    total_text_raw = summary.get("total_text")
    has_total_text = isinstance(total_text_raw, str) and bool(total_text_raw.strip())
    total_text = total_text_raw.strip().lower() if has_total_text else ""
    has_total_in_text = total_text in normalized.lower() if total_text else True

    return not (has_priced_rows and has_total_in_text)
