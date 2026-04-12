"""Системный промпт и текстовые константы для LLM.

Тексты чувствительных промптов НЕ хранятся в публичном коде (ADR-007).
Загрузка через PromptRegistry: Langfuse → env / Lockbox → файл prompts/*.txt → fallback-stub.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

from vkuswill_bot.services.cart_intent_heuristics import looks_like_cart_product_list
from vkuswill_bot.services.prompt_registry import get_registry

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]

# Минимальные fallback-stubs (видны в публичном репо — только базовая роль, без know-how).
_FALLBACK_SYSTEM_PROMPT = (
    "Ты — продавец-консультант ВкусВилл в Telegram-боте. "
    "Помогаешь пользователям подбирать продукты и собирать корзину."
)

_FALLBACK_RECIPE_PROMPT = (
    "Составь список ингредиентов для «{dish}» на {servings} порций.\n"
    "Верни JSON-массив:\n"
    '[{{"name":"...","quantity":N,"unit":"...","search_query":"...","kg_equivalent":N}}]\n'
    "\n"
    "Правила для search_query:\n"
    "- Это запрос для поиска СЫРОГО ПРОДУКТА в магазине ВкусВилл.\n"
    "- Пиши МАКСИМАЛЬНО КОРОТКОЕ название: 1-2 слова.\n"
    '- Хорошо: "гречка", "рис", "нори", "паприка", "кускус", "авокадо", "песто".\n'
    '- НЕ добавляй контекст блюда: НЕ "рис для суши", НЕ "масло для жарки".\n'
    "- НЕ добавляй прилагательные: "
    '"свежий", "молотый", "консервированный", "спелый", "готовый", "нарезной".\n'
    '- НЕ уточняй сорт: "рис" (НЕ "рис басмати"), "гречка" (НЕ "гречневая крупа").\n'
    '- ИСКЛЮЧЕНИЕ: диетические уточнения оставляй: "безлактозное молоко", "тофу".\n'
    "- Если ингредиент — вода, соль, сахар, перец — всё равно включи в список."
)

_FALLBACK_MEAL_PLAN_GENERATION_PROMPT = (
    "Составь meal plan в JSON-формате для параметров ниже.\n"
    "Учитывай hard_constraints строго, soft_preferences — максимально возможно.\n"
    "Верни только JSON с полями schema_version=1 и dishes[].\n"
    "Сгенерируй {min_dishes}..{max_dishes} уникальных блюд, без повторов названий.\n"
    "Распредели блюда по ВСЕМ {days} дням — у каждого дня минимум 1 блюдо.\n"
    "Если в запросе указаны requested_meal_types (например breakfast+lunch+dinner),\n"
    "КАЖДЫЙ день ОБЯЗАН содержать ВСЕ указанные типы приёмов пищи.\n"
    "Если requested_meal_types заданы и groups содержит одну общую группу,\n"
    "на каждый день должно быть РОВНО по одному блюду на каждый requested_meal_type.\n"
    "Не добавляй второе блюдо в тот же слот того же дня.\n"
    "КРИТИЧНО: если в hard_constraints указаны allergens_excluded (например лактоза, орехи),\n"
    "НИКАКОЕ блюдо не должно содержать запрещённые ингредиенты в названии.\n"
    "Например: при без лактозы ЗАПРЕЩЕНЫ: творог, сыр, сметана, молоко, сливки, йогурт, кефир.\n"
    "Все названия блюд ДОЛЖНЫ быть на русском языке.\n"
    "Постарайся обеспечить soft_preferences coverage >= 0.70 по каждой группе.\n"
    "Параметры запроса:\n{request_payload}\n\n"
    "Формат dishes[]:\n"
    "- name: string (на русском языке)\n"
    "- day: int\n"
    '- meal_type: "breakfast"|"lunch"|"dinner"|"snack_1"|"snack_2"|"snack_3"\n'
    "- servings_total: int >= 1\n- audience_groups: string[]\n- cuisine_tags: string[]"
)

_FALLBACK_MEAL_PLAN_REQUEST_PARSE_PROMPT = (
    "Извлеки параметры meal-plan запроса пользователя для бота ВкусВилл.\n"
    "Верни ТОЛЬКО один JSON-объект без markdown и без пояснений.\n"
    "Формат ответа:\n"
    "{{\n"
    '  "days": 2,\n'
    '  "people_total": null,\n'
    '  "requested_meal_types": ["lunch"],\n'
    '  "child_count": null,\n'
    '  "child_age_years": null,\n'
    '  "diet": null,\n'
    '  "cuisines": [],\n'
    '  "allergens_excluded": [],\n'
    '  "confidence": 0.95,\n'
    '  "reason": "обеды на два дня"\n'
    "}}\n\n"
    "Правила:\n"
    "- Не выдумывай значения. Если параметр не указан явно, верни null или [].\n"
    "- days: целое число 1..14. 'на неделю' = 7, 'на рабочую неделю' = 5.\n"
    "- people_total: целое число 1..20 только если количество людей явно указано.\n"
    "- Число дней НЕ означает число людей: 'на два дня' => days=2, people_total=null.\n"
    "- requested_meal_types: список только из breakfast, lunch, dinner, snack.\n"
    "- 'обеды' => ['lunch'], 'завтрак и ужин' => ['breakfast','dinner'].\n"
    "- diet: только vegan, vegetarian, halal или null.\n"
    "- cuisines: только italian, asian, georgian, russian, mediterranean.\n"
    "- allergens_excluded: короткие слова на русском из запроса"
    " ('орехи', 'глютен', 'лактоза', 'яйца').\n"
    "- confidence: число от 0 до 1.\n"
    "- reason: кратко, до 12 слов.\n\n"
    "Примеры:\n"
    "Сообщение: собери мне обеды для здорового питания на два дня\n"
    'Ответ: {{"days":2,"people_total":null,"requested_meal_types":["lunch"],'
    '"child_count":null,"child_age_years":null,"diet":null,"cuisines":[],'
    '"allergens_excluded":[],"confidence":0.95,"reason":"обеды на два дня"}}\n\n'
    "Сообщение: меню на 3 дня для 2 человек\n"
    'Ответ: {{"days":3,"people_total":2,"requested_meal_types":[],'
    '"child_count":null,"child_age_years":null,"diet":null,"cuisines":[],'
    '"allergens_excluded":[],"confidence":0.97,"reason":"меню на 3 дня для 2 человек"}}\n\n'
    "Сообщение пользователя:\n{text}"
)

_FALLBACK_PROFILE_CORE = (
    "Ты — продавец-консультант ВкусВилл в Telegram-боте. "
    "Отвечай только по продуктам, корзине и заказу ВкусВилл."
)

_FALLBACK_PROFILES: dict[str, str] = {
    "general": "[PROMPT_PROFILE:general]\nЦель: помочь подобрать товары.\n"
    "Если пользователь перечислил товары — сразу ищи и собирай корзину,"
    " не переспрашивай.",
    "cart": "[PROMPT_PROFILE:cart]\nЦель: собрать корзину.\n"
    "ГЛАВНОЕ ПРАВИЛО: НИКОГДА НЕ ПЕРЕСПРАШИВАЙ, СРАЗУ ИЩИ И СОБИРАЙ."
    " Получил название продуктов — немедленно вызывай vkusvill_products_search."
    " НЕ уточняй количество, НЕ уточняй сорт, НЕ уточняй предпочтения."
    " Если кол-во не указано — бери 1 шт. Если есть несколько вариантов — бери первый.\n"
    "Примеры ПРАВИЛЬНОГО поведения:\n"
    "- 'молоко' → сразу ищи 'молоко', бери первый результат, q=1\n"
    "- 'молочку и хлеб' → ищи 'молоко', 'кефир', 'сметана', 'сыр', 'хлеб'\n"
    "- 'всё кроме мяса' → ищи овощи, фрукты, молочное, крупы, хлеб\n"
    "- 'трюфели, фуа-гра' → ищи 'трюфели', 'фуа-гра', q=1 каждый\n"
    "- English: 'milk, bread' → ищи 'молоко', 'хлеб'\n"
    "Добавляй в корзину ТОЛЬКО те продукты, которые пользователь назвал."
    " Не дополняй список самостоятельно — не добавляй лук, масло, соль и т.п.,"
    " если пользователь их не просил.\n"
    "КОЛИЧЕСТВА: если пользователь просит N кг/л продукта, а найден товар"
    " весом X г или объёмом Y мл, ОБЯЗАТЕЛЬНО рассчитай кол-во упаковок:"
    " q = ceil(N×1000÷X). НИКОГДА не ставь q=1 если запрошено много."
    " Примеры: 5 кг риса, фасовка 900г → q=6 (НЕ 1!)."
    " 10 л молока, фасовка 900мл → q=12. 20 кг картошки, 2.5кг → q=8.\n"
    "МОДИФИКАЦИЯ КОРЗИНЫ: при добавлении товаров к существующей корзине"
    " СОХРАНЯЙ ТОЧНЫЕ количества старых товаров без изменений."
    " Изменяй qty ТОЛЬКО для товаров, которые пользователь явно попросил изменить.\n"
    "АБСТРАКТНЫЕ КАТЕГОРИИ: 'молочка' = молоко, кефир, сметана, сыр;"
    " 'к чаю' = печенье, пряники; 'хлеб' = хлеб."
    " Подбирай популярные товары, НЕ ПЕРЕСПРАШИВАЙ.\n"
    "АБСТРАКТНЫЕ ПРИЁМЫ ПИЩИ: если пользователь говорит только"
    " 'на завтрак', 'на обед', 'на ужин', 'перекус' БЕЗ названия блюда,"
    " это НЕ рецепт. Подбирай готовые или базовые товары под этот случай"
    " и НЕ вызывай recipe_ingredients для буквальных слов"
    " 'завтрак'/'обед'/'ужин'/'перекус'.\n"
    "ЧИСЛИТЕЛЬНЫЕ: 'полтора кило'=1.5кг, 'пяток'=5шт, 'тройку'=3шт,"
    " 'десяток'=10шт, 'дюжину'=12шт, 'пару'=2шт, 'четверть кило'=250г.\n"
    "ДИЗАМБИГУАЦИЯ: если указан вес (кг, г), это продукт, не десерт."
    " 'Картошка 1.5 кг' = картофель (овощ), не десерт 'Картошка'.\n"
    "ЧИСЛА БЕЗ ЕДИНИЦ: если число стоит перед названием продукта"
    " без единицы измерения (кг, г, л, мл) — это ШТУКИ.\n"
    "НЕНАЙДЕННЫЕ ТОВАРЫ: если часть продуктов не найдена в каталоге"
    " — ОБЯЗАТЕЛЬНО создай корзину из найденных. Сообщи что не нашлось."
    " НИКОГДА не отказывайся формировать корзину из-за одного ненайденного товара.\n"
    "НЕСКОЛЬКО БЛЮД: если пользователь просит собрать продукты для нескольких"
    " блюд/приёмов пищи (завтрак+обед+ужин), обработай ВСЕ блюда ДО ЕДИНОГО."
    " Для каждого блюда: вызови recipe_ingredients → найди товары."
    " Создавай корзину ТОЛЬКО после обработки ВСЕХ блюд.\n"
    "XML_ID: используй ТОЛЬКО xml_id из результатов vkusvill_products_search."
    " НИКОГДА не подставляй выдуманные числа вместо реальных xml_id.",
    "recipe": "[PROMPT_PROFILE:recipe]\nЦель: собрать ингредиенты для блюда.\n"
    "КРИТИЧЕСКОЕ ПРАВИЛО — НЕСКОЛЬКО БЛЮД: если пользователь просит собрать"
    " продукты для нескольких блюд (завтрак+обед+ужин+десерт, или перечисляет"
    " несколько блюд через запятую), ты ОБЯЗАН обработать ВСЕ блюда ДО ЕДИНОГО."
    " Алгоритм: для КАЖДОГО блюда → вызови recipe_ingredients → найди товары."
    " Только когда ВСЕ блюда обработаны → создай ОДНУ корзину со ВСЕМИ"
    " продуктами от ВСЕХ блюд. Если создашь корзину после первого блюда"
    " — это ГРУБАЯ ОШИБКА, пользователь получит неполный заказ.\n"
    "Если пользователь назвал блюдо — сразу ищи ингредиенты и собирай корзину.\n"
    "ВАЖНО: если пользователь УЖЕ перечислил конкретные ингредиенты"
    " с количествами (например: свинина 4 кг, лук 2 кг), ИСПОЛЬЗУЙ ИМЕННО"
    " ИХ список. НЕ заменяй на свой рецепт, НЕ подставляй другие продукты.\n"
    "КОЛИЧЕСТВА: если пользователь просит N кг продукта, а найден товар"
    " весом X г, рассчитай кол-во упаковок: ceil(N×1000÷X)."
    " Пример: 4 кг свинины, фасовка 500г → q=8.\n"
    "НЕНАЙДЕННЫЕ ТОВАРЫ: если часть ингредиентов не найдена в каталоге"
    " ВкусВилл — ОБЯЗАТЕЛЬНО создай корзину из найденных товаров."
    " В ответе укажи, что не удалось найти. НИКОГДА не отказывайся"
    " формировать корзину целиком из-за одного ненайденного товара"
    " (например, уголь для мангала, листы для лазаньи).\n"
    "АБСТРАКТНЫЕ ПРИЁМЫ ПИЩИ: слова 'завтрак', 'обед', 'ужин', 'перекус'"
    " сами по себе НЕ являются названиями блюд. НИКОГДА не вызывай"
    " recipe_ingredients для буквальных слов 'завтрак'/'обед'/'ужин'/'перекус'"
    " без конкретного блюда после них.\n"
    "АЛЛЕРГЕНЫ: при запросе 'без яиц/глютена/лактозы' — подбирай"
    " альтернативные продукты. Не отказывайся собирать корзину,"
    " ищи замены (безглютеновая мука, растительное молоко и т.п.).\n"
    "ГОТОВЫЕ БЛЮДА: СТРОГО ЗАПРЕЩЕНО подставлять готовые блюда вместо"
    " сырых ингредиентов. 'Суп куриный', 'Каша', 'Салат оливье' — это"
    " НЕ ингредиенты для рецепта! Для лазаньи нужны: фарш, лазанья"
    " (листы/макароны), моцарелла, томатная паста — но НЕ 'Суп куриный'."
    " Если ингредиент не найден — пропусти его, добавь в ненайденные.\n"
    "АДЕКВАТНЫЕ КОЛИЧЕСТВА: если пользователь НЕ указал конкретный вес/объём,"
    " ставь разумные количества: 1-2 упаковки каждого ингредиента"
    " на 1-4 человек. Никогда не ставь qty > 10 без явного запроса.\n"
    "XML_ID: используй ТОЛЬКО xml_id из результатов recipe_search /"
    " vkusvill_products_search. НИКОГДА не придумывай xml_id.",
    "meal_plan": "[PROMPT_PROFILE:meal_plan]\nЦель: собрать план питания и корзину.\n"
    "КРИТИЧНО: при создании корзины используй ТОЛЬКО xml_id из результатов "
    "recipe_search / vkusvill_products_search. НИКОГДА не придумывай xml_id.",
    "status": "[PROMPT_PROFILE:status]\nЦель: ответ по статусу.",
    "linking": "[PROMPT_PROFILE:linking]\nЦель: помочь с привязкой аккаунта.",
}

_FALLBACK_MODES: dict[str, str] = {
    "start": "[PROMPT_MODE:expanded_start]\nСоставь план и выполняй.",
    "compact": "[PROMPT_MODE:compact_followup]\nПродолжай кратко.",
    "finalize": "[PROMPT_MODE:finalize]\nФинишируй ответ по корзине.",
}


def _load_prompt_file(filename: str) -> str | None:
    """Загрузить текст промпта из prompts/ (gitignored)."""
    path = _PROJECT_ROOT / "prompts" / filename
    if path.is_file():
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            logger.warning("Не удалось прочитать %s", path)
    return None


def get_system_prompt() -> str:
    """Получить системный промпт: Langfuse → env → файл → fallback-stub."""
    return get_system_prompt_with_metadata()[0]


def get_system_prompt_with_metadata() -> tuple[str, dict[str, Any]]:
    """Получить системный промпт вместе с источником для trace/debug."""
    registry = get_registry()
    if registry is not None:
        resolution = registry.resolve("system-prompt")
        if resolution.text:
            return resolution.text, resolution.as_dict()

    from vkuswill_bot.config import config

    if config.system_prompt:
        text = config.system_prompt
        return text, {
            "name": "system-prompt",
            "source": "config",
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        }
    file_prompt = _load_prompt_file("system_prompt.txt")
    if file_prompt:
        return file_prompt, {
            "name": "system-prompt",
            "source": "file",
            "path": str(_PROJECT_ROOT / "prompts" / "system_prompt.txt"),
            "sha256": hashlib.sha256(file_prompt.encode("utf-8")).hexdigest()[:16],
        }
    return _FALLBACK_SYSTEM_PROMPT, {
        "name": "system-prompt",
        "source": "stub",
        "sha256": hashlib.sha256(_FALLBACK_SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:16],
    }


def get_recipe_extraction_prompt() -> str:
    """Получить промпт извлечения рецептов: Langfuse → env → файл → fallback-stub."""
    return get_recipe_extraction_prompt_with_metadata()[0]


def get_recipe_extraction_prompt_with_metadata() -> tuple[str, dict[str, Any]]:
    """Получить промпт recipe extraction вместе с provenance metadata."""
    registry = get_registry()
    if registry is not None:
        resolution = registry.resolve("recipe-extraction")
        if resolution.text:
            return resolution.text, resolution.as_dict()

    from vkuswill_bot.config import config

    if config.recipe_extraction_prompt:
        text = config.recipe_extraction_prompt
        return text, {
            "name": "recipe-extraction",
            "source": "config",
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        }
    file_prompt = _load_prompt_file("recipe_extraction_prompt.txt")
    if file_prompt:
        return file_prompt, {
            "name": "recipe-extraction",
            "source": "file",
            "path": str(_PROJECT_ROOT / "prompts" / "recipe_extraction_prompt.txt"),
            "sha256": hashlib.sha256(file_prompt.encode("utf-8")).hexdigest()[:16],
        }
    return _FALLBACK_RECIPE_PROMPT, {
        "name": "recipe-extraction",
        "source": "stub",
        "sha256": hashlib.sha256(_FALLBACK_RECIPE_PROMPT.encode("utf-8")).hexdigest()[:16],
    }


def get_meal_plan_generation_prompt(*, request_payload: dict[str, Any]) -> str:
    """Получить промпт генерации meal plan: registry -> fallback-stub."""
    return get_meal_plan_generation_prompt_with_metadata(request_payload=request_payload)[0]


def get_meal_plan_generation_prompt_with_metadata(
    *,
    request_payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Получить meal-plan generation prompt вместе с provenance metadata."""
    payload_text = json.dumps(request_payload, ensure_ascii=False)
    days = request_payload.get("days", 7)
    min_dishes = request_payload.get("min_dishes", 7)
    max_dishes = request_payload.get("max_dishes", 10)
    registry = get_registry()
    if registry is not None:
        resolution = registry.resolve(
            "meal-plan-generation",
            request_payload=payload_text,
        )
        if resolution.text:
            return resolution.text, resolution.as_dict()
    text = _FALLBACK_MEAL_PLAN_GENERATION_PROMPT.format(
        request_payload=payload_text,
        days=days,
        min_dishes=min_dishes,
        max_dishes=max_dishes,
    )
    return text, {
        "name": "meal-plan-generation",
        "source": "stub",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    }


def get_meal_plan_request_parse_prompt_with_metadata(
    *,
    text: str,
) -> tuple[str, dict[str, Any]]:
    """Получить prompt для извлечения параметров meal plan."""
    registry = get_registry()
    if registry is not None:
        resolution = registry.resolve("meal-plan-request-parse", text=text)
        if resolution.text:
            return resolution.text, resolution.as_dict()
    prompt = _FALLBACK_MEAL_PLAN_REQUEST_PARSE_PROMPT.format(text=text)
    return prompt, {
        "name": "meal-plan-request-parse",
        "source": "stub",
        "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
    }


SYSTEM_PROMPT = _FALLBACK_SYSTEM_PROMPT

RECIPE_EXTRACTION_PROMPT = _FALLBACK_RECIPE_PROMPT

PromptProfile = Literal["general", "cart", "recipe", "meal_plan", "status", "linking"]
PromptMode = Literal["start", "compact", "finalize"]


def _get_profile_part(registry_name: str, fallback: str) -> str:
    """Load a profile/mode prompt from registry with fallback to stub."""
    return _get_profile_part_with_metadata(registry_name, fallback)[0]


def _get_profile_part_with_metadata(
    registry_name: str,
    fallback: str,
) -> tuple[str, dict[str, Any]]:
    """Load a profile/mode prompt and return provenance metadata."""
    registry = get_registry()
    if registry is not None:
        resolution = registry.resolve(registry_name)
        if resolution.text:
            return resolution.text, resolution.as_dict()
    return fallback, {
        "name": registry_name,
        "source": "stub",
        "sha256": hashlib.sha256(fallback.encode("utf-8")).hexdigest()[:16],
    }


def detect_prompt_profile(text: str) -> PromptProfile:
    """Определить профиль промпта по тексту запроса пользователя."""
    low = text.lower()

    # Явное намерение готовить — всегда recipe
    strong_recipe_markers = (
        "рецепт",
        "ингредиент",
        "ингридиент",
        "приготов",
        "сдела",
        "свари",
        "испеч",
        "порци",
    )
    # Названия блюд — recipe только если нет cart-маркера
    dish_name_markers = (
        "борщ",
        "суп",
        "паста",
        "лазань",
        "пицц",
        "салат",
        "карбонар",
        "плов",
        "окрошк",
        "блин",
        "оладь",
        "каш",
        "омлет",
    )
    status_markers = (
        "статус",
        "где заказ",
        "что с заказом",
        "что с корзин",
        "статус корзин",
        "проверь статус",
    )
    linking_markers = ("привяз", "код", "алис", "voice", "отвяз")
    meal_plan_strong_markers = (
        "план питания",
        "рацион",
    )
    meal_plan_period_markers = (
        "меню на",
        "на неделю",
        "недельн",
    )
    has_days_period = re.search(r"на\s+\d+\s+д", low) is not None
    has_loose_days = re.search(r"\d+\s+д(?:ней|ня|ень)", low) is not None
    has_people_count = re.search(r"для\s+\d+\s+(чел|человек)", low) is not None
    has_period_marker = has_days_period or any(marker in low for marker in meal_plan_period_markers)
    meal_type_words = ("завтрак", "обед", "ужин", "перекус", "полдник")
    cart_markers = (
        "купи",
        "закажи",
        "добав",
        "собери",
        "подбер",
        "убер",
        "удал",
        "замен",
        "измени",
        "поменя",
        "объедин",
    )

    if any(marker in low for marker in status_markers):
        return "status"
    if any(marker in low for marker in linking_markers):
        return "linking"
    if any(marker in low for marker in meal_plan_strong_markers):
        return "meal_plan"
    if has_period_marker and has_people_count:
        return "meal_plan"
    has_meal_type_word = any(w in low for w in meal_type_words)
    if (has_days_period or has_loose_days) and has_meal_type_word:
        return "meal_plan"
    if any(marker in low for marker in strong_recipe_markers):
        return "recipe"
    if any(marker in low for marker in cart_markers):
        return "cart"
    if looks_like_cart_product_list(low):
        return "cart"
    if any(marker in low for marker in dish_name_markers):
        return "recipe"
    return "general"


def get_profiled_system_prompt(
    *,
    profile: PromptProfile,
    compact: bool = False,
    mode: PromptMode | None = None,
) -> str:
    """Скомпоновать профильный system-prompt для конкретного режима."""
    return get_profiled_system_prompt_with_metadata(
        profile=profile,
        compact=compact,
        mode=mode,
    )[0]


def get_profiled_system_prompt_with_metadata(
    *,
    profile: PromptProfile,
    compact: bool = False,
    mode: PromptMode | None = None,
) -> tuple[str, dict[str, Any]]:
    """Скомпоновать профильный system-prompt и metadata по его частям."""
    resolved_mode: PromptMode = mode or ("compact" if compact else "start")

    core, core_meta = _get_profile_part_with_metadata("profile-core", _FALLBACK_PROFILE_CORE)
    profile_text, profile_meta = _get_profile_part_with_metadata(
        f"profile-{profile}", _FALLBACK_PROFILES.get(profile, _FALLBACK_PROFILES["general"])
    )
    mode_text, mode_meta = _get_profile_part_with_metadata(
        f"mode-{resolved_mode}", _FALLBACK_MODES.get(resolved_mode, _FALLBACK_MODES["start"])
    )
    text = "\n\n".join([core, profile_text, mode_text])
    return text, {
        "strategy": "profiled",
        "profile": profile,
        "mode": resolved_mode,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        "components": [core_meta, profile_meta, mode_meta],
    }


# ---- Описания инструментов для function calling ----

NUTRITION_TOOL: dict = {
    "name": "nutrition_lookup",
    "description": (
        "Получить КБЖУ (калории, белки, жиры, углеводы) продукта или блюда "
        "на 100 г. Данные из открытой базы Open Food Facts. "
        "Передавай query НА РУССКОМ как ОБЩЕЕ название продукта "
        "(борщ, куриная грудка, плов, творог). "
        "НЕ передавай точные торговые названия ВкусВилл! "
        "Используй ТОЛЬКО когда пользователь ЯВНО спрашивает "
        "про калорийность, КБЖУ, БЖУ, диету, или просит подобрать еду "
        "с ограничением по калориям."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "ОБЩЕЕ название продукта на русском (1-3 слова). "
                    "Хорошо: куриная грудка, картофель, молоко, масло сливочное, плов. "
                    "Плохо: Филе грудки цыпленка-бройлера, Молоко 3,2% 1 л "
                    "(точные названия из магазина НЕ находятся!)"
                ),
            },
        },
        "required": ["query"],
    },
}

RECIPE_TOOL: dict = {
    "name": "recipe_ingredients",
    "description": (
        "Получить полный список ингредиентов для блюда/рецепта. "
        "ОБЯЗАТЕЛЬНО вызывай когда пользователь просит собрать "
        "продукты для конкретного блюда (борщ, паста, азу и т.д.). "
        "Возвращает ингредиенты с количествами и поисковыми запросами. "
        "ВАЖНО: ВСЕГДА передавай servings исходя из контекста! "
        "Если пользователь один — servings=1. Если на двоих — servings=2."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dish": {
                "type": "string",
                "description": ("Название блюда, например: азу из говядины, борщ, паста карбонара"),
            },
            "servings": {
                "type": "integer",
                "description": (
                    "Количество порций (человек). "
                    "ОБЯЗАТЕЛЬНО определи из контекста: "
                    "«я хочу» = 1, «на двоих» = 2, «на семью» = 4. "
                    "По умолчанию 2."
                ),
            },
        },
        "required": ["dish"],
    },
}

RECIPE_SEARCH_TOOL: dict = {
    "name": "recipe_search",
    "description": (
        "Пакетный поиск товаров для ингредиентов рецепта. "
        "Вызывай после recipe_ingredients и передавай ВЕСЬ массив ingredients. "
        "Возвращает best_match, alternatives и suggested_q для каждого ингредиента."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ingredients": {
                "type": "array",
                "description": "Массив ингредиентов из результата recipe_ingredients",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "search_query": {"type": "string"},
                        "quantity": {"type": "number"},
                        "unit": {"type": "string"},
                    },
                },
            },
        },
        "required": ["ingredients"],
    },
}

CART_PREVIOUS_TOOL: dict = {
    "name": "get_previous_cart",
    "description": (
        "Получить содержимое предыдущей корзины пользователя. "
        "ОБЯЗАТЕЛЬНО вызывай перед объединением корзин или добавлением "
        "товаров к существующей корзине. Возвращает список товаров "
        "(xml_id, q), ссылку и стоимость."
    ),
    "parameters": {"type": "object", "properties": {}},
}

LOCAL_TOOLS: list[dict] = [
    {
        "name": "user_preferences_get",
        "description": (
            "Получить сохранённые предпочтения пользователя. "
            "Вызывай перед поиском товаров и meal-plan, чтобы учесть вкусы. "
            "Ответ содержит legacy preferences и структурированный profile."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "user_preferences_set",
        "description": (
            "Сохранить предпочтение пользователя. "
            "Вызывай когда пользователь просит запомнить что-то."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": (
                        "Категория предпочтения. Примеры: мороженое, молоко, хлеб, сыр, "
                        "diet, allergens_excluded, cuisines, liked_ingredients, "
                        "disliked_ingredients, meal_types."
                    ),
                },
                "preference": {
                    "type": "string",
                    "description": (
                        "Конкретное описание предпочтения, например: пломбир в шоколаде на "
                        "палочке, vegan, italian, nuts,lactose"
                    ),
                },
            },
            "required": ["category", "preference"],
        },
    },
    {
        "name": "user_preferences_delete",
        "description": "Удалить сохранённое предпочтение по категории.",
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Категория для удаления (например: мороженое)",
                },
            },
            "required": ["category"],
        },
    },
]

# Сообщения об ошибках для пользователя
ERROR_GIGACHAT = (
    "Произошла ошибка при обращении к GigaChat. Попробуйте позже или начните новый диалог: /reset"
)

ERROR_TOO_MANY_STEPS = (
    "Обработка заняла слишком много шагов. Попробуйте упростить запрос или начните заново: /reset"
)
