"""Вспомогательные функции для обработки рецептов и ингредиентов."""

from __future__ import annotations

import contextlib
import json
import logging
import math
import re
from typing import Any

from vkuswill_bot.agents.intent_markers import (
    PANTRY_TAG_PEPPER,
    PANTRY_TAG_SALT,
    PANTRY_TAG_SUGAR,
)
from vkuswill_bot.agents.recipe_quantity_calculator import RecipeQuantityCalculator
from vkuswill_bot.agents.tool_result_compactor import _safe_float, tokenize_query_terms
from vkuswill_bot.agents.mcp_response_parser import parse_json_payload
from vkuswill_bot.services.prompts import detect_prompt_profile
from vkuswill_bot.services.search_processor import SearchProcessor

logger = logging.getLogger(__name__)

_QUANTITY_UNIT_PATTERN = (
    r"кг|kg|г|гр|л|l|мл|ml|шт|штук|шт\.|"
    r"зубчик(?:а|ов)?|головк(?:а|и|ок)|лист(?:а|ов)?|"
    r"ст\.?\s*л\.?|ч\.?\s*л\.?|"
    r"ст\.?\s*ложк[аи]?|ч\.?\s*ложк[аи]?|столов(?:ая|ые)\s+ложк[аи]|"
    r"чайн(?:ая|ые)\s+ложк[аи]"
)
_RANGE_QUANTITY_RE = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*[-–—]\s*(\d+(?:[.,]\d+)?)\s*({_QUANTITY_UNIT_PATTERN})",
    flags=re.IGNORECASE,
)
_SINGLE_QUANTITY_RE = re.compile(
    rf"(\d+(?:[.,]\d+)?)\s*({_QUANTITY_UNIT_PATTERN})",
    flags=re.IGNORECASE,
)
_CLEAN_INGREDIENT_PREFIX_RE = (
    re.compile(r"^\s*собери(?:\s+мне)?\s+корзин[ауые]?\s+", flags=re.IGNORECASE),
    re.compile(r"^\s*добав(?:ь|ьте)(?:\s+в\s+корзин[ауые]?)?\s+", flags=re.IGNORECASE),
    re.compile(r"^\s*закаж(?:и|ите)\s+", flags=re.IGNORECASE),
)
_LEADING_AND_RE = re.compile(r"^\s*и\s+", flags=re.IGNORECASE)
_INLINE_INGREDIENT_RE = re.compile(
    rf"(?:^|[\n,;])\s*(?P<name>[^\n,;]+?)\s*[-–—:]\s*"
    rf"(?P<qty>\d+(?:[.,]\d+)?(?:\s*[-–—]\s*\d+(?:[.,]\d+)?)?\s*(?:{_QUANTITY_UNIT_PATTERN}))"
    r"(?=$|[\n,;])",
    flags=re.IGNORECASE,
)


# ------------------------------------------------------------------
# Базовые text-утилиты
# ------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """Привести текст к нижнему регистру, убрать пробелы и ё→е."""
    return text.strip().lower().replace("ё", "е")


# ------------------------------------------------------------------
# Определение типа перца и pantry-тегов
# ------------------------------------------------------------------


def looks_like_pepper_vegetable(text: str) -> bool:
    """Определить, что речь об овощном перце (болгарский, чили и т.д.)."""
    normalized = normalize_text(text)
    vegetable_markers = (
        "болгар",
        "сладк",
        "чили",
        "халапень",
        "пепперони",
        "перец овощ",
        "фаршированн",
    )
    return any(marker in normalized for marker in vegetable_markers)


def is_explicit_seasoning_pepper_request(text: str) -> bool:
    """Определить, что пользователь явно запрашивает перец-приправу."""
    normalized = normalize_text(text)
    if "перец" not in normalized:
        return False
    spice_markers = (
        "черн",
        "красн",
        "бел",
        "молот",
        "горош",
        "душист",
        "приправ",
    )
    if any(marker in normalized for marker in spice_markers):
        return True
    if looks_like_pepper_vegetable(normalized):
        return False
    return "соль" in normalized or "сахар" in normalized


def detect_pantry_tag_for_ingredient(row: dict[str, Any]) -> str | None:
    """Вернуть pantry-тег для ингредиента (соль/сахар/перец) или None."""
    name = normalize_text(str(row.get("name", "")))
    query = normalize_text(str(row.get("search_query", "")))
    text = f"{name} {query}".strip()
    if not text:
        return None
    if "соль" in text:
        return PANTRY_TAG_SALT
    if "сахар" in text:
        return PANTRY_TAG_SUGAR
    if "перец" in text and not looks_like_pepper_vegetable(text):
        return PANTRY_TAG_PEPPER
    return None


def extract_explicit_pantry_requests(user_text: str) -> set[str]:
    """Извлечь из текста пользователя явные запросы на pantry-ингредиенты."""
    normalized = normalize_text(user_text)
    requested: set[str] = set()
    if "соль" in normalized:
        requested.add(PANTRY_TAG_SALT)
    if "сахар" in normalized:
        requested.add(PANTRY_TAG_SUGAR)
    if is_explicit_seasoning_pepper_request(normalized):
        requested.add(PANTRY_TAG_PEPPER)
    return requested


def has_explicit_egg_pack_request(text: str) -> bool:
    """Определить, что пользователь явно просит упаковку яиц."""
    normalized = normalize_text(text)
    if not any(stem in normalized for stem in ("яйц", "яиц", "яйк")):
        return False
    pack_markers = ("упаков", "десят", "дюжин")
    return any(marker in normalized for marker in pack_markers)


# ------------------------------------------------------------------
# Парсинг количеств и ингредиентов
# ------------------------------------------------------------------


def parse_quantity_hint(text: str) -> tuple[float, str, str] | None:
    """Попытаться распарсить количество + единицу измерения из текста."""
    normalized = normalize_text(text)
    if not normalized:
        return None
    match = _RANGE_QUANTITY_RE.search(normalized)
    if match is not None:
        low = _safe_float(match.group(1), default=-1.0)
        high = _safe_float(match.group(2), default=-1.0)
        if low > 0 and high > 0:
            unit = RecipeQuantityCalculator._normalize_unit(match.group(3))
            if unit:
                return max(low, high), unit, match.group(0)

    match = _SINGLE_QUANTITY_RE.search(normalized)
    if match is None:
        return None
    quantity = _safe_float(match.group(1), default=-1.0)
    if quantity <= 0:
        return None
    unit = RecipeQuantityCalculator._normalize_unit(match.group(2))
    if not unit:
        return None
    return quantity, unit, match.group(0)


def clean_structured_ingredient_name(name_raw: str) -> str:
    """Очистить имя ингредиента от командных префиксов ('собери корзину ...')."""
    name = name_raw.strip(" ,.;:").strip()
    if not name:
        return ""
    for pattern in _CLEAN_INGREDIENT_PREFIX_RE:
        name = pattern.sub("", name).strip()
    name = _LEADING_AND_RE.sub("", name).strip()
    return name


def extract_structured_ingredient_requests(user_text: str) -> list[dict[str, Any]]:
    """Извлечь структурированные запросы ингредиентов из текста пользователя."""
    if not user_text.strip():
        return []
    rows: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, float, str]] = set()

    def _append_row(name_raw: str, quantity: float, unit: str) -> None:
        cleaned_name = clean_structured_ingredient_name(name_raw)
        if not cleaned_name:
            return
        search_query = SearchProcessor.clean_search_query(cleaned_name) or cleaned_name
        key = (search_query.lower(), round(quantity, 4), unit)
        if key in seen_keys:
            return
        seen_keys.add(key)
        rows.append(
            {
                "name": cleaned_name,
                "quantity": quantity,
                "unit": unit,
                "search_query": search_query,
            }
        )

    normalized_text = user_text.replace("\r", "\n")
    for match in _INLINE_INGREDIENT_RE.finditer(normalized_text):
        name_part = str(match.group("name") or "").strip()
        qty_part = str(match.group("qty") or "").strip()
        parsed = parse_quantity_hint(qty_part)
        if parsed is None:
            continue
        quantity, unit, _fragment = parsed
        _append_row(name_part, quantity, unit)

    for raw_line in normalized_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _INLINE_INGREDIENT_RE.search(line):
            continue
        line = re.sub(r"^\s*\d+[.)]\s*", "", line).strip()
        line = line.lstrip("-• ").strip()
        if not line:
            continue
        name_part = line
        quantity_part = line
        split_match = re.match(r"^(.*?)(?:\s*[-–—:]\s*)(.+)$", line)
        if split_match is not None:
            left = split_match.group(1).strip()
            right = split_match.group(2).strip()
            if left and right:
                name_part = left
                quantity_part = right
        parsed = parse_quantity_hint(quantity_part)
        if parsed is None and quantity_part != line:
            parsed = parse_quantity_hint(line)
        if parsed is None:
            continue
        quantity, unit, _fragment = parsed
        _append_row(name_part, quantity, unit)
    return rows[:30]


def normalize_recipe_ingredient_row(row_raw: Any) -> dict[str, Any]:
    """Нормализовать строку ингредиента (dict или str) в унифицированный dict."""
    if isinstance(row_raw, dict):
        row = dict(row_raw)
        quantity = _safe_float(row.get("quantity"), default=1.0)
        if quantity <= 0:
            quantity = 1.0
        unit = RecipeQuantityCalculator._normalize_unit(row.get("unit")) or "шт"
        row["quantity"] = quantity
        row["unit"] = unit
        search_query = str(row.get("search_query", "")).strip()
        if not search_query:
            search_query = SearchProcessor.clean_search_query(str(row.get("name", "")).strip())
        if search_query:
            row["search_query"] = search_query
        return row
    if not isinstance(row_raw, str):
        return {}

    text = row_raw.strip()
    if not text:
        return {}
    cleaned = text.replace("по вкусу", "").strip(" ,.-")
    parsed = parse_quantity_hint(cleaned)
    if parsed is not None:
        parsed_q, parsed_unit, fragment = parsed
    else:
        parsed_q, parsed_unit, fragment = RecipeQuantityCalculator.parse_quantity_and_unit(cleaned)
    if fragment:
        name = re.sub(re.escape(fragment), " ", cleaned, flags=re.IGNORECASE).strip(" ,.-")
    else:
        name = cleaned
    if not name:
        name = cleaned
    query = SearchProcessor.clean_search_query(name) or name
    return {
        "name": name or text,
        "search_query": query,
        "quantity": parsed_q if parsed_q is not None else 1.0,
        "unit": parsed_unit or "шт",
    }


# ------------------------------------------------------------------
# Matching/overrides
# ------------------------------------------------------------------


def match_requested_ingredient(
    *,
    product: dict[str, Any],
    xml_id: int,
    requested_ingredients: list[dict[str, Any]],
    search_query_by_xml_id: dict[int, str] | None,
) -> dict[str, Any] | None:
    """Найти наиболее подходящий запрошенный ингредиент для товара."""
    if not requested_ingredients:
        return None

    def _score_pair(left: str, right: str) -> float:
        left_tokens = tokenize_query_terms(left)
        right_tokens = tokenize_query_terms(right)
        if not left_tokens or not right_tokens:
            return 0.0
        left_set = set(left_tokens)
        right_set = set(right_tokens)
        inter = len(left_set & right_set)
        if inter <= 0:
            return 0.0
        return inter / max(len(left_set), len(right_set))

    best_score = 0.0
    best_row: dict[str, Any] | None = None
    product_name = str(product.get("name", "")).strip()
    query_hint = ""
    if search_query_by_xml_id is not None:
        query_hint = str(search_query_by_xml_id.get(xml_id, "")).strip()

    for row in requested_ingredients:
        if not isinstance(row, dict):
            continue
        row_name = str(row.get("name", "")).strip()
        row_query = str(row.get("search_query", "")).strip()
        score = max(
            _score_pair(row_query, query_hint),
            _score_pair(row_name, query_hint),
            _score_pair(row_query, product_name),
            _score_pair(row_name, product_name),
        )
        if score > best_score:
            best_score = score
            best_row = row

    if best_row is None or best_score < 0.2:
        return None
    return best_row


def apply_requested_quantity_overrides(
    snapshot: list[dict[str, Any]],
    overrides: dict[int, float],
) -> list[dict[str, Any]]:
    """Применить переопределения количеств к snapshot корзины."""
    if not snapshot or not overrides:
        return snapshot
    updated: list[dict[str, Any]] = []
    for row in snapshot:
        if not isinstance(row, dict):
            continue
        current = dict(row)
        xml_id_raw = current.get("xml_id")
        if isinstance(xml_id_raw, bool):
            updated.append(current)
            continue
        with contextlib.suppress(TypeError, ValueError):
            xml_id = int(xml_id_raw)
            if xml_id in overrides:
                current["q"] = overrides[xml_id]
        updated.append(current)
    return updated


def filter_recipe_ingredients_list(
    *,
    ingredients: list[Any],
    explicit_pantry_requests: set[str],
) -> tuple[list[Any], list[str]]:
    """Отфильтровать pantry-ингредиенты из списка, если не запрошены явно."""
    filtered: list[Any] = []
    removed: list[str] = []
    for row in ingredients:
        if not isinstance(row, dict):
            filtered.append(row)
            continue
        pantry_tag = detect_pantry_tag_for_ingredient(row)
        if pantry_tag and pantry_tag not in explicit_pantry_requests:
            removed.append(str(row.get("name", "")).strip())
            continue
        filtered.append(row)
    return filtered, removed


# ------------------------------------------------------------------
# Рецептурные эквиваленты и fallback
# ------------------------------------------------------------------


def enrich_recipe_equivalents(ingredient: dict[str, Any]) -> None:
    """Добавить kg_equivalent / l_equivalent / pack_equivalent к ингредиенту."""
    unit = str(ingredient.get("unit", "")).lower().strip()
    quantity = _safe_float(ingredient.get("quantity"), default=0.0)
    name = str(ingredient.get("name", "")).lower()
    if quantity <= 0:
        return
    if unit == "г":
        ingredient["kg_equivalent"] = round(quantity / 1000, 3)
        return
    if unit == "мл":
        ingredient["l_equivalent"] = round(quantity / 1000, 3)
        return
    if "яйц" in name and unit in {"шт", "штука", "штук"}:
        ingredient["pack_equivalent"] = max(1, math.ceil(quantity / 10))


def fallback_borscht_ingredients(servings: int) -> list[dict[str, Any]]:
    """Захардкоженный fallback-рецепт борща."""
    base_servings = 2
    factor = servings / base_servings if servings > 0 else 1.0
    base = [
        {"name": "свёкла", "quantity": 0.67, "unit": "шт", "search_query": "свекла"},
        {
            "name": "капуста белокочанная",
            "quantity": 0.17,
            "unit": "кг",
            "search_query": "капуста белокочанная",
        },
        {"name": "картофель", "quantity": 0.2, "unit": "кг", "search_query": "картофель"},
        {"name": "морковь", "quantity": 0.05, "unit": "кг", "search_query": "морковь"},
        {
            "name": "лук репчатый",
            "quantity": 0.03,
            "unit": "кг",
            "search_query": "лук репчатый",
        },
        {"name": "помидоры", "quantity": 0.1, "unit": "кг", "search_query": "помидоры свежие"},
        {"name": "говядина", "quantity": 0.4, "unit": "кг", "search_query": "говядина"},
        {"name": "чеснок", "quantity": 10, "unit": "г", "search_query": "чеснок"},
        {
            "name": "масло растительное",
            "quantity": 30,
            "unit": "мл",
            "search_query": "масло растительное",
        },
        {
            "name": "томатная паста",
            "quantity": 60,
            "unit": "г",
            "search_query": "томатная паста",
        },
    ]
    result: list[dict[str, Any]] = []
    for row in base:
        item = dict(row)
        base_quantity = _safe_float(row.get("quantity"), default=1.0)
        item["quantity"] = round(base_quantity * factor, 3)
        enrich_recipe_equivalents(item)
        result.append(item)
    return result


# ------------------------------------------------------------------
# Детекция рецептурного followup
# ------------------------------------------------------------------


def is_recipe_followup(*, text: str, history: list[dict[str, Any]] | None) -> bool:
    """Определить, является ли сообщение продолжением рецептурного диалога."""
    if not history:
        return False
    normalized = normalize_text(text)
    if not normalized or len(normalized) > 120:
        return False
    if any(marker in normalized for marker in ("привяз", "алис", "код", "статус", "отвяз")):
        return False

    recent_user_messages = [
        str(msg.get("content", "")).strip()
        for msg in reversed(history)
        if msg.get("role") == "user" and isinstance(msg.get("content"), str)
    ]
    for prev_text in recent_user_messages[:3]:
        if detect_prompt_profile(prev_text) == "recipe":
            return True

    for msg in reversed(history[-8:]):
        if msg.get("role") == "tool" and msg.get("name") in {
            "recipe_ingredients",
            "recipe_search",
        }:
            return True
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function_data = call.get("function")
            if not isinstance(function_data, dict):
                continue
            name = str(function_data.get("name", "")).strip()
            if name in {"recipe_ingredients", "recipe_search"}:
                return True
    return False


# ------------------------------------------------------------------
# Санитизация tool-результатов recipe_ingredients
# ------------------------------------------------------------------


def sanitize_recipe_ingredients_tool_result(
    *,
    tool_result: str,
    explicit_pantry_requests: set[str],
) -> str:
    """Отфильтровать pantry-ингредиенты из ответа recipe_ingredients."""
    payload = parse_json_payload(tool_result)
    if not isinstance(payload, dict) or not payload:
        return tool_result

    removed_names: list[str] = []
    changed = False

    ingredients = payload.get("ingredients")
    if isinstance(ingredients, list):
        filtered, removed = filter_recipe_ingredients_list(
            ingredients=ingredients,
            explicit_pantry_requests=explicit_pantry_requests,
        )
        if len(filtered) != len(ingredients):
            payload["ingredients"] = filtered
            removed_names.extend(removed)
            changed = True

    data = payload.get("data")
    if isinstance(data, dict):
        nested = data.get("ingredients")
        if isinstance(nested, list):
            filtered, removed = filter_recipe_ingredients_list(
                ingredients=nested,
                explicit_pantry_requests=explicit_pantry_requests,
            )
            if len(filtered) != len(nested):
                data["ingredients"] = filtered
                removed_names.extend(removed)
                changed = True

    if not changed:
        return tool_result

    unique_removed = sorted({name for name in removed_names if name})
    if unique_removed:
        payload["pantry_filtered"] = unique_removed
        logger.info("Filtered pantry ingredients from recipe_ingredients: %s", unique_removed)

    return json.dumps(payload, ensure_ascii=False)
