"""Системный промпт и текстовые константы для LLM.

Тексты чувствительных промптов НЕ хранятся в публичном коде (ADR-007).
Загрузка через PromptRegistry: Langfuse → env / Lockbox → файл prompts/*.txt → fallback-stub.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Literal

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
    '- Пиши ТОЛЬКО название продукта: "говядина", "соевый соус", "лапша яичная".\n'
    "- НЕ добавляй название блюда и контекст: "
    'НЕ "говядина для лагмана", НЕ "соевый соус для лагмана".\n'
    '- НЕ добавляй "свежий/свежая" — пиши просто продукт.\n'
    "- Если ингредиент — вода, соль, сахар, молотый перец — всё равно включи в список."
)

_FALLBACK_MEAL_PLAN_GENERATION_PROMPT = (
    "Составь meal plan в JSON-формате для параметров ниже.\n"
    "Учитывай hard_constraints строго, soft_preferences — максимально возможно.\n"
    "Верни только JSON с полями schema_version=1 и dishes[].\n"
    "Сгенерируй 7..10 уникальных блюд, без повторов названий.\n"
    "Постарайся обеспечить soft_preferences coverage >= 0.70 по каждой группе.\n"
    "Параметры запроса:\n{request_payload}\n\n"
    "Формат dishes[]:\n"
    "- name: string\n"
    "- day: int\n"
    '- meal_type: "breakfast"|"lunch"|"dinner"|"snack_1"|"snack_2"|"snack_3"\n'
    "- servings_total: int >= 1\n- audience_groups: string[]\n- cuisine_tags: string[]"
)

_FALLBACK_PROFILE_CORE = (
    "Ты — продавец-консультант ВкусВилл в Telegram-боте. "
    "Отвечай только по продуктам, корзине и заказу ВкусВилл."
)

_FALLBACK_PROFILES: dict[str, str] = {
    "general": "[PROMPT_PROFILE:general]\nЦель: помочь подобрать товары.",
    "cart": "[PROMPT_PROFILE:cart]\nЦель: собрать корзину.",
    "recipe": "[PROMPT_PROFILE:recipe]\nЦель: собрать ингредиенты для блюда.",
    "meal_plan": "[PROMPT_PROFILE:meal_plan]\nЦель: собрать план питания и корзину.",
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
    registry = get_registry()
    if registry is not None:
        result = registry.get("system-prompt")
        if result:
            return result

    from vkuswill_bot.config import config

    if config.system_prompt:
        return config.system_prompt
    file_prompt = _load_prompt_file("system_prompt.txt")
    if file_prompt:
        return file_prompt
    return _FALLBACK_SYSTEM_PROMPT


def get_recipe_extraction_prompt() -> str:
    """Получить промпт извлечения рецептов: Langfuse → env → файл → fallback-stub."""
    registry = get_registry()
    if registry is not None:
        result = registry.get("recipe-extraction")
        if result:
            return result

    from vkuswill_bot.config import config

    if config.recipe_extraction_prompt:
        return config.recipe_extraction_prompt
    file_prompt = _load_prompt_file("recipe_extraction_prompt.txt")
    if file_prompt:
        return file_prompt
    return _FALLBACK_RECIPE_PROMPT


def get_meal_plan_generation_prompt(*, request_payload: dict[str, Any]) -> str:
    """Получить промпт генерации meal plan: registry -> fallback-stub."""
    payload_text = json.dumps(request_payload, ensure_ascii=False)
    registry = get_registry()
    if registry is not None:
        result = registry.get(
            "meal-plan-generation",
            request_payload=payload_text,
        )
        if result:
            return result
    return _FALLBACK_MEAL_PLAN_GENERATION_PROMPT.format(request_payload=payload_text)


SYSTEM_PROMPT = _FALLBACK_SYSTEM_PROMPT

RECIPE_EXTRACTION_PROMPT = _FALLBACK_RECIPE_PROMPT

PromptProfile = Literal["general", "cart", "recipe", "meal_plan", "status", "linking"]
PromptMode = Literal["start", "compact", "finalize"]


def _get_profile_part(registry_name: str, fallback: str) -> str:
    """Load a profile/mode prompt from registry with fallback to stub."""
    registry = get_registry()
    if registry is not None:
        result = registry.get(registry_name)
        if result:
            return result
    return fallback


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
    )
    # Названия блюд — recipe только если нет cart-маркера
    dish_name_markers = (
        "борщ",
        "суп",
        "паста",
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
    has_people_count = re.search(r"для\s+\d+\s+(чел|человек)", low) is not None
    has_period_marker = has_days_period or any(marker in low for marker in meal_plan_period_markers)
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
    if any(marker in low for marker in strong_recipe_markers):
        return "recipe"
    if any(marker in low for marker in cart_markers):
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
    resolved_mode: PromptMode = mode or ("compact" if compact else "start")

    core = _get_profile_part("profile-core", _FALLBACK_PROFILE_CORE)
    profile_text = _get_profile_part(
        f"profile-{profile}", _FALLBACK_PROFILES.get(profile, _FALLBACK_PROFILES["general"])
    )
    mode_text = _get_profile_part(
        f"mode-{resolved_mode}", _FALLBACK_MODES.get(resolved_mode, _FALLBACK_MODES["start"])
    )

    return "\n\n".join([core, profile_text, mode_text])


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
