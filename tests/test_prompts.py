"""Тесты текстовых констант prompts.py.

Проверяем:
- Содержание системного промпта (ключевые блоки)
- Формат сообщений об ошибках
- Отсутствие секретных данных в промпте
"""

from vkuswill_bot.services.prompts import (
    CART_PREVIOUS_TOOL,
    ERROR_GIGACHAT,
    ERROR_TOO_MANY_STEPS,
    LOCAL_TOOLS,
    RECIPE_EXTRACTION_PROMPT,
    RECIPE_TOOL,
    SYSTEM_PROMPT,
)


# ============================================================================
# Системный промпт: содержание
# ============================================================================


class TestSystemPromptContent:
    """Тесты содержания системного промпта."""

    def test_defines_role(self):
        """Промпт определяет роль: продавец-консультант ВкусВилл."""
        assert "продавец-консультант" in SYSTEM_PROMPT.lower()
        assert "ВкусВилл" in SYSTEM_PROMPT

    def test_defines_workflow(self):
        """Промпт описывает рабочий процесс (шаги)."""
        assert "Шаг 1" in SYSTEM_PROMPT
        assert "Шаг 2" in SYSTEM_PROMPT
        assert "Шаг 3" in SYSTEM_PROMPT

    def test_defines_cart_format(self):
        """Промпт описывает формат вызова vkusvill_cart_link_create."""
        assert "vkusvill_cart_link_create" in SYSTEM_PROMPT
        assert "xml_id" in SYSTEM_PROMPT
        assert "products" in SYSTEM_PROMPT

    def test_mentions_preferences(self):
        """Промпт содержит инструкции про предпочтения."""
        assert "user_preferences_get" in SYSTEM_PROMPT
        assert "user_preferences_set" in SYSTEM_PROMPT
        assert "user_preferences_delete" in SYSTEM_PROMPT

    def test_mentions_disclaimer(self):
        """Промпт требует дисклеймер после корзины."""
        assert (
            "дисклеймер" in SYSTEM_PROMPT.lower() or "Наличие и точное количество" in SYSTEM_PROMPT
        )

    def test_format_rules(self):
        """Промпт содержит правила формата ответа."""
        assert "Русский язык" in SYSTEM_PROMPT
        assert "price_summary" in SYSTEM_PROMPT

    def test_no_secrets(self):
        """В промпте нет токенов, ключей и паролей."""
        for keyword in ["token", "password", "secret", "api_key", "credentials"]:
            assert keyword not in SYSTEM_PROMPT.lower(), f"Промпт не должен содержать '{keyword}'"

    def test_reasonable_length(self):
        """Промпт разумного размера (не пустой, не гигантский)."""
        assert 500 < len(SYSTEM_PROMPT) < 15000

    def test_mentions_recipe_ingredients(self):
        """Промпт содержит инструкции про recipe_ingredients."""
        assert "recipe_ingredients" in SYSTEM_PROMPT

    def test_defines_recipe_workflow(self):
        """Промпт описывает алгоритм работы с рецептами."""
        assert "рецепт" in SYSTEM_PROMPT.lower() or "РЕЦЕПТ" in SYSTEM_PROMPT

    def test_forbids_adding_extra_items_for_recipes(self):
        """Промпт запрещает добавлять от себя дополнительные ингредиенты к рецептам."""
        lower = SYSTEM_PROMPT.lower()
        assert "запрещено добавлять" in lower or "не добавляй" in lower
        assert "не нужен" in lower

    def test_defines_quantity_calculation(self):
        """Промпт содержит инструкции по расчёту количества."""
        assert "Расчёт количества" in SYSTEM_PROMPT or "q=" in SYSTEM_PROMPT

    def test_defines_unit_examples(self):
        """Промпт содержит примеры расчёта по единицам (кг, шт)."""
        assert "unit=" in SYSTEM_PROMPT
        assert "кг" in SYSTEM_PROMPT

    def test_ready_vs_cook_clarification(self):
        """Промпт содержит инструкцию уточнять: готовое или приготовить."""
        lower = SYSTEM_PROMPT.lower()
        assert "готовое блюдо" in lower or "готовое" in lower
        assert "приготовить" in lower

    def test_fermented_products_section(self):
        """Промпт содержит секцию про соленья/квашения/консервацию."""
        lower = SYSTEM_PROMPT.lower()
        assert "квашеная капуста" in lower
        assert "ферментированн" in lower or "консервац" in lower
        assert "recipe_ingredients" in SYSTEM_PROMPT

    def test_ambiguous_queries_section(self):
        """Промпт содержит секцию про неоднозначные поисковые запросы."""
        lower = SYSTEM_PROMPT.lower()
        assert "неоднозначн" in lower
        assert "ром" in lower
        assert "уточняющее слово" in lower

    def test_packaging_section(self):
        """Промпт содержит секцию про упаковки ВкусВилл."""
        lower = SYSTEM_PROMPT.lower()
        assert "упаковк" in lower
        assert "яйц" in lower

    def test_cart_merge_section(self):
        """Промпт содержит секцию про объединение корзин."""
        assert "get_previous_cart" in SYSTEM_PROMPT
        assert "объедин" in SYSTEM_PROMPT.lower()

    def test_relevance_check_section(self):
        """Промпт содержит секцию проверки соответствия товаров запросу."""
        assert "relevance_warning" in SYSTEM_PROMPT
        assert "несоответстви" in SYSTEM_PROMPT.lower() or "не найден" in SYSTEM_PROMPT.lower()

    def test_alcohol_in_recipes_section(self):
        """Промпт содержит секцию про алкоголь в рецептах."""
        lower = SYSTEM_PROMPT.lower()
        assert "алкоголь" in lower
        assert "optional" in lower
        assert "ром" in lower

    def test_duplicate_warning_section(self):
        """Промпт содержит секцию про дубли товаров в корзине."""
        assert "duplicate_warning" in SYSTEM_PROMPT
        lower = SYSTEM_PROMPT.lower()
        assert "дубль" in lower or "дубл" in lower

    def test_default_servings_changed_to_2(self):
        """Промпт указывает servings=2 по умолчанию (не 4)."""
        assert "по умолчанию 2" in SYSTEM_PROMPT.lower()


# ============================================================================
# Системный промпт: безопасность
# ============================================================================


class TestSystemPromptSecurity:
    """Тесты секции безопасности в системном промпте."""

    def test_has_security_section(self):
        """Промпт содержит секцию безопасности."""
        assert "## Безопасность" in SYSTEM_PROMPT

    def test_role_anchoring_in_security(self):
        """Секция безопасности якорит роль продавца-консультанта."""
        lower = SYSTEM_PROMPT.lower()
        assert "всегда продавец-консультант" in lower

    def test_forbids_role_change(self):
        """Промпт запрещает изменение роли через сообщения пользователя."""
        lower = SYSTEM_PROMPT.lower()
        assert "не могут изменить" in lower or "никакие сообщения" in lower

    def test_forbids_prompt_leaking(self):
        """Промпт запрещает раскрытие инструкций."""
        lower = SYSTEM_PROMPT.lower()
        assert "не раскрывай" in lower or "никогда не раскрывай" in lower
        assert "системный промпт" in lower or "инструкции" in lower

    def test_has_prompt_leak_deflection(self):
        """Промпт содержит шаблон ответа на попытку извлечения промпта."""
        assert "бот ВкусВилл" in SYSTEM_PROMPT
        assert "помогаю подобрать продукты" in SYSTEM_PROMPT

    def test_restricts_topic_to_products(self):
        """Промпт ограничивает тематику продуктами и едой."""
        lower = SYSTEM_PROMPT.lower()
        assert "только" in lower
        assert "продукт" in lower
        assert "посторонние темы" in lower or "посторонн" in lower

    def test_has_offtopic_deflection(self):
        """Промпт содержит шаблон ответа на off-topic запросы."""
        assert "специализируюсь на продуктах" in SYSTEM_PROMPT

    def test_forbids_harmful_content(self):
        """Промпт запрещает генерацию вредоносного контента."""
        lower = SYSTEM_PROMPT.lower()
        assert "оскорбления" in lower or "угрозы" in lower
        assert "незаконный контент" in lower or "незаконн" in lower

    def test_forbids_medical_financial_advice(self):
        """Промпт запрещает медицинские и финансовые советы."""
        lower = SYSTEM_PROMPT.lower()
        assert "медицинск" in lower
        assert "финансов" in lower

    def test_blocks_authority_impersonation(self):
        """Промпт защищён от impersonation разработчика/администратора."""
        lower = SYSTEM_PROMPT.lower()
        assert "разработчик" in lower
        assert "администратор" in lower
        assert "режим отладки" in lower or "диагностик" in lower

    def test_no_debug_mode(self):
        """Промпт явно отрицает наличие режима отладки."""
        lower = SYSTEM_PROMPT.lower()
        assert "нет режима отладки" in lower or "нет режима" in lower


# ============================================================================
# RECIPE_EXTRACTION_PROMPT
# ============================================================================


class TestRecipeExtractionPrompt:
    """Тесты шаблона промпта для извлечения рецептов."""

    def test_is_template_with_placeholders(self):
        """Промпт содержит плейсхолдеры {dish} и {servings}."""
        assert "{dish}" in RECIPE_EXTRACTION_PROMPT
        assert "{servings}" in RECIPE_EXTRACTION_PROMPT

    def test_format_works(self):
        """Шаблон корректно форматируется."""
        result = RECIPE_EXTRACTION_PROMPT.format(dish="борщ", servings=4)
        assert "борщ" in result
        assert "4" in result
        assert "{dish}" not in result
        assert "{servings}" not in result

    def test_requests_json_array(self):
        """Промпт просит вернуть JSON-массив."""
        assert "JSON" in RECIPE_EXTRACTION_PROMPT
        assert "name" in RECIPE_EXTRACTION_PROMPT
        assert "quantity" in RECIPE_EXTRACTION_PROMPT
        assert "search_query" in RECIPE_EXTRACTION_PROMPT

    def test_defines_unit_types(self):
        """Промпт описывает допустимые единицы."""
        assert "кг" in RECIPE_EXTRACTION_PROMPT
        assert "шт" in RECIPE_EXTRACTION_PROMPT

    def test_excludes_common_items(self):
        """Промпт исключает соль, молотый перец, воду."""
        lower = RECIPE_EXTRACTION_PROMPT.lower()
        assert "соль" in lower
        assert "перец" in lower
        assert "вод" in lower

    def test_distinguishes_pepper_spice_from_vegetable(self):
        """Промпт различает перец-приправу и перец-овощ (болгарский)."""
        lower = RECIPE_EXTRACTION_PROMPT.lower()
        assert "молотый" in lower
        assert "болгарский" in lower or "чили" in lower

    def test_alcohol_optional_flag(self):
        """Промпт инструктирует добавлять optional=true для алкоголя."""
        lower = RECIPE_EXTRACTION_PROMPT.lower()
        assert "алкоголь" in lower
        assert "optional" in lower
        assert "ром" in lower

    def test_coffee_instruction(self):
        """Промпт содержит инструкцию для кофе: 'кофе молотый'."""
        lower = RECIPE_EXTRACTION_PROMPT.lower()
        assert "кофе молотый" in lower

    def test_no_secrets(self):
        """В промпте рецептов нет секретов."""
        for keyword in ["token", "password", "secret", "api_key"]:
            assert keyword not in RECIPE_EXTRACTION_PROMPT.lower()

    def test_reasonable_length(self):
        """Промпт рецептов разумного размера."""
        assert 100 < len(RECIPE_EXTRACTION_PROMPT) < 5000


# ============================================================================
# Сообщения об ошибках
# ============================================================================


class TestErrorMessages:
    """Тесты сообщений об ошибках."""

    def test_error_gigachat_contains_reset(self):
        """ERROR_GIGACHAT предлагает /reset."""
        assert "/reset" in ERROR_GIGACHAT

    def test_error_gigachat_user_friendly(self):
        """ERROR_GIGACHAT на русском и дружелюбный."""
        assert "ошибк" in ERROR_GIGACHAT.lower()
        assert "Попробуйте" in ERROR_GIGACHAT

    def test_error_too_many_steps_contains_reset(self):
        """ERROR_TOO_MANY_STEPS предлагает /reset."""
        assert "/reset" in ERROR_TOO_MANY_STEPS

    def test_error_too_many_steps_user_friendly(self):
        """ERROR_TOO_MANY_STEPS на русском и дружелюбный."""
        assert "слишком много" in ERROR_TOO_MANY_STEPS.lower()

    def test_errors_are_strings(self):
        """Оба сообщения — непустые строки."""
        assert isinstance(ERROR_GIGACHAT, str) and len(ERROR_GIGACHAT) > 0
        assert isinstance(ERROR_TOO_MANY_STEPS, str) and len(ERROR_TOO_MANY_STEPS) > 0

    def test_errors_no_technical_details(self):
        """Ошибки не раскрывают технических деталей."""
        for msg in [ERROR_GIGACHAT, ERROR_TOO_MANY_STEPS]:
            for keyword in ["traceback", "exception", "stack", "debug"]:
                assert keyword not in msg.lower(), (
                    f"Сообщение об ошибке не должно содержать '{keyword}'"
                )


# ============================================================================
# CART_PREVIOUS_TOOL
# ============================================================================


class TestCartPreviousTool:
    """Тесты описания инструмента get_previous_cart."""

    def test_tool_name(self):
        """Инструмент называется get_previous_cart."""
        assert CART_PREVIOUS_TOOL["name"] == "get_previous_cart"

    def test_has_description(self):
        """Инструмент имеет описание."""
        desc = CART_PREVIOUS_TOOL["description"]
        assert isinstance(desc, str) and len(desc) > 0

    def test_description_mentions_previous_cart(self):
        """Описание упоминает предыдущую корзину."""
        lower = CART_PREVIOUS_TOOL["description"].lower()
        assert "предыдущ" in lower or "корзин" in lower

    def test_has_parameters(self):
        """Инструмент имеет параметры (пустые — без аргументов)."""
        assert "parameters" in CART_PREVIOUS_TOOL
        assert CART_PREVIOUS_TOOL["parameters"]["type"] == "object"

    def test_no_required_params(self):
        """Инструмент не имеет обязательных параметров."""
        params = CART_PREVIOUS_TOOL["parameters"]
        assert "required" not in params or params.get("required") == []


# ============================================================================
# RECIPE_TOOL
# ============================================================================


class TestRecipeTool:
    """Тесты описания инструмента recipe_ingredients."""

    def test_tool_name(self):
        """Инструмент называется recipe_ingredients."""
        assert RECIPE_TOOL["name"] == "recipe_ingredients"

    def test_has_description(self):
        """Инструмент имеет описание."""
        desc = RECIPE_TOOL["description"]
        assert isinstance(desc, str) and len(desc) > 0

    def test_dish_parameter_required(self):
        """Параметр dish обязателен."""
        params = RECIPE_TOOL["parameters"]
        assert "dish" in params["required"]

    def test_servings_parameter_optional(self):
        """Параметр servings опционален."""
        params = RECIPE_TOOL["parameters"]
        assert "servings" not in params.get("required", [])

    def test_servings_description_mentions_default_2(self):
        """Описание servings упоминает значение по умолчанию 2."""
        desc = RECIPE_TOOL["parameters"]["properties"]["servings"]["description"]
        assert "2" in desc

    def test_description_mentions_servings(self):
        """Описание инструмента упоминает servings."""
        desc = RECIPE_TOOL["description"]
        assert "servings" in desc.lower()


# ============================================================================
# LOCAL_TOOLS
# ============================================================================


class TestLocalTools:
    """Тесты описаний локальных инструментов."""

    def test_local_tools_is_list(self):
        """LOCAL_TOOLS — список."""
        assert isinstance(LOCAL_TOOLS, list)
        assert len(LOCAL_TOOLS) > 0

    def test_all_tools_have_name(self):
        """Все инструменты имеют имя."""
        for tool in LOCAL_TOOLS:
            assert "name" in tool
            assert isinstance(tool["name"], str)

    def test_all_tools_have_description(self):
        """Все инструменты имеют описание."""
        for tool in LOCAL_TOOLS:
            assert "description" in tool
            assert isinstance(tool["description"], str)
            assert len(tool["description"]) > 0

    def test_contains_preferences_tools(self):
        """LOCAL_TOOLS содержит инструменты предпочтений."""
        names = [t["name"] for t in LOCAL_TOOLS]
        assert "user_preferences_get" in names
        assert "user_preferences_set" in names
        assert "user_preferences_delete" in names

    def test_no_secrets_in_descriptions(self):
        """Описания не содержат секретов."""
        for tool in LOCAL_TOOLS:
            desc = tool["description"].lower()
            for keyword in ["token", "password", "secret", "api_key"]:
                assert keyword not in desc
