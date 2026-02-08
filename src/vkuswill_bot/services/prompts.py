"""Системный промпт и текстовые константы для GigaChat."""

SYSTEM_PROMPT = """
***REDACTED***
"""

RECIPE_EXTRACTION_PROMPT = """
***REDACTED***
"""

# ---- Описания инструментов для function calling ----

RECIPE_TOOL: dict = {
    "name": "recipe_ingredients",
    "description": (
        "Получить полный список ингредиентов для блюда/рецепта. "
        "ОБЯЗАТЕЛЬНО вызывай когда пользователь просит собрать "
        "продукты для конкретного блюда (борщ, паста, азу и т.д.). "
        "Возвращает ингредиенты с количествами и поисковыми запросами."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "dish": {
                "type": "string",
                "description": (
                    "Название блюда, например: "
                    "азу из говядины, борщ, паста карбонара"
                ),
            },
            "servings": {
                "type": "integer",
                "description": "Количество порций (человек). По умолчанию 4.",
            },
        },
        "required": ["dish"],
    },
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
                    "description": "Конкретное описание предпочтения, например: пломбир в шоколаде на палочке",
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
    "Произошла ошибка при обращении к GigaChat. "
    "Попробуйте позже или начните новый диалог: /reset"
)

ERROR_TOO_MANY_STEPS = (
    "Обработка заняла слишком много шагов. "
    "Попробуйте упростить запрос или начните заново: /reset"
)
