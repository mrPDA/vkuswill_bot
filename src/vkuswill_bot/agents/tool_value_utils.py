"""Shared value/text utilities for compact MCP payload processing."""

from __future__ import annotations

import contextlib
import html
import re
from typing import Any


def normalize_compact_text(value: Any) -> str:
    """Normalize text by unescaping HTML, stripping tags and squashing spaces."""
    text = str(value or "")
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokenize_query_terms(query: str) -> list[str]:
    """Tokenize query text for relevance scoring."""
    normalized = normalize_compact_text(query).lower().replace("ё", "е")
    tokens = re.findall(r"[a-zа-я0-9]+", normalized, flags=re.IGNORECASE)
    return [token for token in tokens if len(token) >= 2][:6]


def score_search_candidate(
    *,
    query_terms: list[str],
    product_name: str,
    rating: float | None,
) -> tuple[float, float]:
    """Score relevance and confidence for product candidate."""
    normalized_name = normalize_compact_text(product_name).lower().replace("ё", "е")
    if not query_terms:
        rating_bonus = (rating or 0.0) / 10 if rating is not None else 0.0
        return rating_bonus, 0.5

    matched = sum(1 for token in query_terms if token in normalized_name)
    coverage = matched / max(1, len(query_terms))
    prefix_bonus = 0.2 if normalized_name.startswith(query_terms[0]) else 0.0
    rating_bonus = (rating or 0.0) / 10 if rating is not None else 0.0
    score = coverage * 2.5 + prefix_bonus + rating_bonus
    confidence = min(0.99, max(0.0, 0.3 + coverage * 0.7))
    return score, round(confidence, 2)


def extract_price_value(raw_price: Any) -> float | None:
    """Extract numeric price from scalar or nested dict payload."""
    if isinstance(raw_price, dict):
        for key in ("current", "value", "amount", "price"):
            if key in raw_price:
                price = _safe_float(raw_price.get(key), default=-1.0)
                if price >= 0:
                    return price
        return None
    price = _safe_float(raw_price, default=-1.0)
    return price if price >= 0 else None


def _safe_float(value: Any, *, default: float) -> float:
    """Safe float conversion with localized decimal separator support."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            return float(value.replace(",", "."))
    return default
