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


def normalize_colloquial_numerals(text: str) -> str:
    """Replace colloquial Russian numerals with digit equivalents."""
    result = text
    for pattern, replacement in _COLLOQUIAL_NUMERALS:
        result = pattern.sub(replacement, result)
    return result


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
        q = item.get("q", 1)
        if xml_id in merged:
            merged[xml_id] += q
        else:
            merged[xml_id] = q
            order.append(xml_id)

    if merged:
        arguments["products"] = [{"xml_id": xid, "q": merged[xid]} for xid in order]

    return arguments
