"""Системный промпт и текстовые константы для GigaChat."""

SYSTEM_PROMPT = """
***REDACTED***
"""

RECIPE_EXTRACTION_PROMPT = """
***REDACTED***
"""

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
            "Вызывай перед поиском товаров, чтобы учесть вкусы."
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
                    "description": "Категория продукта, например: мороженое, молоко, хлеб, сыр",
                },
                "preference": {
                    "type": "string",
                    "description": (
                        "Конкретное описание предпочтения, например: пломбир в шоколаде на палочке"
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
