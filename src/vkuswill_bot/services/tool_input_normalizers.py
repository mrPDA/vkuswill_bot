"""Shared нормализация входных аргументов инструментов."""

from __future__ import annotations

import re
from typing import Any

# Максимум товаров в результатах поиска (экономия токенов).
SEARCH_LIMIT = 5

_COLLOQUIAL_NUMERALS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bполтора\s+кило\b", re.I), "1.5 кг"),
    (re.compile(r"\bполкило\b", re.I), "0.5 кг"),
    (re.compile(r"\bполтора\s+литра\b", re.I), "1.5 л"),
    (re.compile(r"\bполлитра\b", re.I), "0.5 л"),
    (re.compile(r"\bчетверть\s+кило\b", re.I), "0.25 кг"),
    (re.compile(r"\bполтора\b", re.I), "1.5"),
    (re.compile(r"\bпар[уае]\b", re.I), "2"),
    (re.compile(r"\bтройк[уае]\b", re.I), "3 шт"),
    (re.compile(r"\bпяточ?к[уае]?\b", re.I), "5 шт"),
    (re.compile(r"\bдесято?к\b", re.I), "10 шт"),
    (re.compile(r"\bдюжин[уае]?\b", re.I), "12 шт"),
    (re.compile(r"\bчетверть\b", re.I), "0.25"),
]

_MULTILINGUAL_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bi\s+need\b", re.I), ""),
    (re.compile(r"\bplease\b", re.I), ""),
    (re.compile(r"\band\b", re.I), " и "),
    (re.compile(r"\bsour\s+cream\b", re.I), "сметана"),
    (re.compile(r"\bpotatoes\b", re.I), "картофель"),
    (re.compile(r"\bpotato\b", re.I), "картофель"),
    (re.compile(r"\btomatoes\b", re.I), "помидоры"),
    (re.compile(r"\btomato\b", re.I), "помидоры"),
    (re.compile(r"\bchicken\b", re.I), "курица"),
    (re.compile(r"\bbutter\b", re.I), "масло"),
    (re.compile(r"\bcheese\b", re.I), "сыр"),
    (re.compile(r"\beggs\b", re.I), "яйца"),
    (re.compile(r"\begg\b", re.I), "яйца"),
    (re.compile(r"\bbread\b", re.I), "хлеб"),
    (re.compile(r"\bmilk\b", re.I), "молоко"),
    (re.compile(r"\brice\b", re.I), "рис"),
    (re.compile(r"\bcream\b", re.I), "сливки"),
    (re.compile(r"\bkefir\b", re.I), "кефир"),
    (re.compile(r"\bmoloko\b", re.I), "молоко"),
    (re.compile(r"\bhleb\b", re.I), "хлеб"),
    (re.compile(r"\bmaslo\b", re.I), "масло"),
    (re.compile(r"\bsyr\b", re.I), "сыр"),
    (re.compile(r"\bsir\b", re.I), "сыр"),
    (re.compile(r"\byaic[ao]?\b", re.I), "яйца"),
    (re.compile(r"\byaits[ao]?\b", re.I), "яйца"),
    (re.compile(r"\blitra\b", re.I), "литра"),
    (re.compile(r"\blitrov\b", re.I), "литров"),
    (re.compile(r"\blitr\b", re.I), "литр"),
    (re.compile(r"\byogurt\b", re.I), "йогурт"),
    (re.compile(r"\boil\b", re.I), "растительное масло"),
    (re.compile(r"\bonion\b", re.I), "лук"),
    (re.compile(r"\bgarlic\b", re.I), "чеснок"),
    (re.compile(r"\bapple\b", re.I), "яблоко"),
    (re.compile(r"\bsugar\b", re.I), "сахар"),
    (re.compile(r"\bflour\b", re.I), "мука"),
    (re.compile(r"\bcarrot\b", re.I), "морковь"),
    (re.compile(r"\bcucumber\b", re.I), "огурец"),
    (re.compile(r"\bpepper\b", re.I), "перец"),
    (re.compile(r"\bbeef\b", re.I), "говядина"),
    (re.compile(r"\bpork\b", re.I), "свинина"),
    (re.compile(r"\bfish\b", re.I), "рыба"),
    (re.compile(r"\bsalmon\b", re.I), "лосось"),
    (re.compile(r"\boats\b", re.I), "овсянка"),
    (re.compile(r"\bpasta\b", re.I), "макароны"),
    (re.compile(r"\bcoffee\b", re.I), "кофе"),
    (re.compile(r"\btea\b", re.I), "чай"),
]


def normalize_colloquial_numerals(text: str) -> str:
    """Replace colloquial Russian numerals with digit equivalents."""
    result = text
    for pattern, replacement in _COLLOQUIAL_NUMERALS:
        result = pattern.sub(replacement, result)
    return result


def normalize_multilingual_grocery_text(text: str) -> str:
    """Map common English/translit grocery terms to Russian equivalents."""
    if not text or not re.search(r"[A-Za-z]", text):
        return text

    result = text
    for pattern, replacement in _MULTILINGUAL_REPLACEMENTS:
        result = pattern.sub(replacement, result)

    result = re.sub(r"\s+", " ", result)
    result = re.sub(r"\s+,", ",", result)
    result = re.sub(r",\s*,+", ", ", result)
    result = re.sub(r"\s+\.", ".", result)
    result = re.sub(r"\s+([!?])", r"\1", result)
    return result.strip(" ,") or text


# Паттерн для удаления количества/единиц из поискового запроса.
UNIT_PATTERN = re.compile(
    r"\b\d+[,.]?\d*\s*"
    r"(%|шт\w*|гр\w*|г\b|кг\w*|мл\w*|л\b|литр\w*|"
    r"бутыл\w*|банк\w*|пач\w*|уп\w*|порц\w*|"
    r"ст\.?\s*л\.?|ч\.?\s*л\.?|зубч\w*|пуч\w*|лист\w*)",
    re.IGNORECASE,
)

# Отдельные числа ("молоко 4", "мороженое 2").
STANDALONE_NUM_PATTERN = re.compile(r"\b\d+\b")


def clean_search_query(query: str) -> str:
    """Очистить поисковую строку от количеств и единиц измерения."""
    cleaned = UNIT_PATTERN.sub("", query)
    cleaned = STANDALONE_NUM_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or query


MAX_ITEM_QTY = 20


def fix_cart_args(arguments: dict[str, Any]) -> dict[str, Any]:
    """Исправить аргументы корзины: подставить q=1 и объединить дубли xml_id."""
    products = arguments.get("products")
    if not products or not isinstance(products, list):
        return arguments

    for item in products:
        if isinstance(item, dict) and "q" not in item:
            item["q"] = 1

    merged: dict[Any, float] = {}
    order: list[Any] = []
    for item in products:
        if not isinstance(item, dict):
            continue
        xml_id = item.get("xml_id")
        if xml_id is None:
            continue
        q_raw = item.get("q", 1)
        # Нормализуем значение q, если оно строковое
        if isinstance(q_raw, str):
            # Заменяем запятую на точку для правильного парсинга
            q_normalized = q_raw.replace(',', '.')
            try:
                q = float(q_normalized)
            except ValueError:
                # Если не удалось преобразовать, используем 1 как безопасное значение
                q = 1
        elif not isinstance(q_raw, (int, float)):
            # Для других типов данных используем 1
            q = 1
        else:
            q = float(q_raw)

        if xml_id in merged:
            merged[xml_id] += q
        else:
            merged[xml_id] = q
            order.append(xml_id)

    if merged:
        arguments["products"] = [{"xml_id": xid, "q": merged[xid]} for xid in order]

    return arguments
