"""Тесты текстовых констант prompts.py.

Проверяем:
- Загрузка системного промпта (env → файл → fallback)
- Содержание production-промпта (детальные требования, если загружен)
- Формат сообщений об ошибках
- Отсутствие секретных данных в промпте
"""

from pathlib import Path

import pytest

from vkuswill_bot.services.prompts import (
    CART_PREVIOUS_TOOL,
    ERROR_GIGACHAT,
    ERROR_TOO_MANY_STEPS,
    LOCAL_TOOLS,
    PromptProfile,
    RECIPE_SEARCH_TOOL,
    RECIPE_TOOL,
    detect_prompt_profile,
    get_profiled_system_prompt,
    get_recipe_extraction_prompt,
    get_system_prompt,
)

# Полный production-промпт загружается из файла, если доступен
_PROD_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system_prompt.txt"
_PROD_PROMPT: str | None = None
if _PROD_PROMPT_PATH.exists():
    _PROD_PROMPT = _PROD_PROMPT_PATH.read_text(encoding="utf-8")

_has_prod_prompt = pytest.mark.skipif(
    _PROD_PROMPT is None,
    reason="Production prompt not available (prompts/system_prompt.txt)",
)

_RECIPE_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent / "prompts" / "recipe_extraction_prompt.txt"
)
_RECIPE_PROMPT: str | None = None
if _RECIPE_PROMPT_PATH.exists():
    _RECIPE_PROMPT = _RECIPE_PROMPT_PATH.read_text(encoding="utf-8")

_has_recipe_prompt = pytest.mark.skipif(
    _RECIPE_PROMPT is None,
    reason="Recipe prompt not available (prompts/recipe_extraction_prompt.txt)",
)


# ============================================================================
# Дефолтный промпт: базовые требования
# ============================================================================


class TestSystemPromptLoading:
    """Тесты загрузки системного промпта (env → файл → fallback)."""

    def test_get_system_prompt_returns_nonempty(self):
        """get_system_prompt() всегда возвращает непустую строку."""
        prompt = get_system_prompt()
        assert isinstance(prompt, str) and len(prompt) > 0

    def test_get_system_prompt_mentions_role(self):
        """Промпт определяет роль: продавец-консультант ВкусВилл."""
        prompt = get_system_prompt()
        assert "продавец-консультант" in prompt.lower()
        assert "ВкусВилл" in prompt

    def test_no_secrets_in_system_prompt(self):
        """В промпте нет токенов, ключей и паролей."""
        prompt = get_system_prompt()
        for keyword in ["token", "password", "secret", "api_key", "credentials"]:
            assert keyword not in prompt.lower(), f"Промпт не должен содержать '{keyword}'"


class TestPromptProfiles:
    """Тесты компактных профильных system-промптов."""

    @pytest.mark.parametrize(
        ("text", "profile"),
        [
            ("Закажи молоко и яйца", "cart"),
            ("молоко, хлеб, яйца и сыр", "cart"),
            ("лук 100 г, чеснок 50 г, имбирь 30 г", "cart"),
            ("Собери ингредиенты для борща", "recipe"),
            ("Собери ингридиенты для борща", "recipe"),
            ("Хочу сделать тонкие блины на молоке", "recipe"),
            ("Проверь статус корзины", "status"),
            ("Что с корзиной?", "status"),
            ("Удали лук из корзины", "cart"),
            ("Замени молоко на кефир", "cart"),
            ("Как привязать алису по коду", "linking"),
            ("Привет", "general"),
            ("Собери меню на неделю для 2 человек", "meal_plan"),
            ("Сделай рацион на 5 дней для 1 человека", "meal_plan"),
            ("Рацион на неделю", "meal_plan"),
            ("План питания с низким углеводом", "meal_plan"),
            ("Меню на неделю", "general"),
            ("Недельное меню с овощами", "general"),
            ("Меню на 5 дней", "general"),
            # Названия блюд + cart-маркер → cart (а не recipe)
            ("Закажи суп", "cart"),
            ("Купи борщ", "cart"),
            ("Добавь пасту в корзину", "cart"),
            # Названия блюд + strong recipe-маркер → recipe
            ("Свари суп", "recipe"),
            ("Приготовь борщ", "recipe"),
            ("Рецепт пасты", "recipe"),
            # Названия блюд без cart-маркера → recipe
            ("Борщ", "recipe"),
            ("Суп", "recipe"),
            # "порци" → strong recipe marker (BUG-9 fix)
            ("Собери корзину для борща на 4 порции", "recipe"),
            ("Лазанья на 6 порций", "recipe"),
        ],
    )
    def test_detect_prompt_profile(self, text: str, profile: PromptProfile):
        assert detect_prompt_profile(text) == profile

    def test_get_profiled_system_prompt_contains_profile_marker(self):
        prompt = get_profiled_system_prompt(profile="cart", compact=False)
        assert "[PROMPT_PROFILE:cart]" in prompt
        assert "[PROMPT_MODE:expanded_start]" in prompt
        assert "[PROMPT_MODE:compact_followup]" not in prompt

    def test_get_profiled_system_prompt_compact_mode_adds_followup_marker(self):
        prompt = get_profiled_system_prompt(profile="recipe", compact=True)
        assert "[PROMPT_PROFILE:recipe]" in prompt
        assert "[PROMPT_MODE:compact_followup]" in prompt

    def test_get_profiled_system_prompt_finalize_mode(self):
        prompt = get_profiled_system_prompt(profile="cart", mode="finalize")
        assert "[PROMPT_PROFILE:cart]" in prompt
        assert "[PROMPT_MODE:finalize]" in prompt


# ============================================================================
# Production-промпт: детальные требования (пропускается в CI)
# ============================================================================


class TestProductionPromptContent:
    """Тесты содержания production-промпта (из prompts/system_prompt.txt)."""

    @_has_prod_prompt
    def test_defines_workflow_steps(self):
        assert "Шаг 1" in _PROD_PROMPT and "Шаг 2" in _PROD_PROMPT

    @_has_prod_prompt
    def test_defines_cart_format(self):
        assert "vkusvill_cart_link_create" in _PROD_PROMPT
        assert "xml_id" in _PROD_PROMPT

    @_has_prod_prompt
    def test_mentions_preferences(self):
        assert "user_preferences_get" in _PROD_PROMPT

    @_has_prod_prompt
    def test_format_rules(self):
        assert "Русский язык" in _PROD_PROMPT
        assert "price_summary" in _PROD_PROMPT

    @_has_prod_prompt
    def test_no_secrets(self):
        for keyword in ["token", "password", "secret", "api_key", "credentials"]:
            assert keyword not in _PROD_PROMPT.lower()

    @_has_prod_prompt
    def test_reasonable_length(self):
        assert 500 < len(_PROD_PROMPT) < 18000

    @_has_prod_prompt
    def test_mentions_recipe_ingredients(self):
        assert "recipe_ingredients" in _PROD_PROMPT

    @_has_prod_prompt
    def test_fermented_products_section(self):
        lower = _PROD_PROMPT.lower()
        assert "квашеная капуста" in lower

    @_has_prod_prompt
    def test_ambiguous_queries_section(self):
        lower = _PROD_PROMPT.lower()
        assert "неоднозначн" in lower

    @_has_prod_prompt
    def test_packaging_section(self):
        lower = _PROD_PROMPT.lower()
        assert "упаковк" in lower

    @_has_prod_prompt
    def test_cart_merge_section(self):
        assert "get_previous_cart" in _PROD_PROMPT

    @_has_prod_prompt
    def test_relevance_check_section(self):
        assert "relevance_warning" in _PROD_PROMPT

    @_has_prod_prompt
    def test_duplicate_warning_section(self):
        assert "duplicate_warning" in _PROD_PROMPT

    @_has_prod_prompt
    def test_default_servings_changed_to_2(self):
        assert "по умолчанию 2" in _PROD_PROMPT.lower()


# ============================================================================
# Production-промпт: безопасность (пропускается в CI)
# ============================================================================


class TestProductionPromptSecurity:
    """Тесты секции безопасности в production-промпте."""

    @_has_prod_prompt
    def test_has_security_section(self):
        assert "## Безопасность" in _PROD_PROMPT

    @_has_prod_prompt
    def test_role_anchoring_in_security(self):
        assert "всегда продавец-консультант" in _PROD_PROMPT.lower()

    @_has_prod_prompt
    def test_forbids_role_change(self):
        lower = _PROD_PROMPT.lower()
        assert "не могут изменить" in lower or "никакие сообщения" in lower

    @_has_prod_prompt
    def test_forbids_prompt_leaking(self):
        lower = _PROD_PROMPT.lower()
        assert "не раскрывай" in lower or "никогда не раскрывай" in lower

    @_has_prod_prompt
    def test_has_prompt_leak_deflection(self):
        assert "бот ВкусВилл" in _PROD_PROMPT

    @_has_prod_prompt
    def test_restricts_topic_to_products(self):
        lower = _PROD_PROMPT.lower()
        assert "только" in lower and "продукт" in lower

    @_has_prod_prompt
    def test_has_offtopic_deflection(self):
        assert "специализируюсь на продуктах" in _PROD_PROMPT

    @_has_prod_prompt
    def test_forbids_harmful_content(self):
        lower = _PROD_PROMPT.lower()
        assert "оскорбления" in lower or "угрозы" in lower

    @_has_prod_prompt
    def test_forbids_medical_financial_advice(self):
        lower = _PROD_PROMPT.lower()
        assert "медицинск" in lower and "финансов" in lower

    @_has_prod_prompt
    def test_blocks_authority_impersonation(self):
        lower = _PROD_PROMPT.lower()
        assert "разработчик" in lower and "администратор" in lower

    @_has_prod_prompt
    def test_no_debug_mode(self):
        lower = _PROD_PROMPT.lower()
        assert "нет режима отладки" in lower or "нет режима" in lower


# ============================================================================
# RECIPE_EXTRACTION_PROMPT
# ============================================================================


class TestRecipeExtractionPrompt:
    """Тесты шаблона промпта для извлечения рецептов."""

    def test_is_template_with_placeholders(self):
        """Промпт содержит плейсхолдеры {dish} и {servings}."""
        prompt = get_recipe_extraction_prompt()
        assert "{dish}" in prompt
        assert "{servings}" in prompt

    def test_format_works(self):
        """Шаблон корректно форматируется."""
        result = get_recipe_extraction_prompt().format(dish="борщ", servings=4)
        assert "борщ" in result
        assert "4" in result
        assert "{dish}" not in result
        assert "{servings}" not in result

    def test_requests_json_array(self):
        """Промпт просит вернуть JSON-массив."""
        prompt = get_recipe_extraction_prompt()
        assert "JSON" in prompt
        assert "name" in prompt
        assert "quantity" in prompt
        assert "search_query" in prompt

    def test_no_secrets(self):
        """В промпте рецептов нет секретов."""
        prompt = get_recipe_extraction_prompt()
        for keyword in ["token", "password", "secret", "api_key"]:
            assert keyword not in prompt.lower()

    @_has_recipe_prompt
    def test_defines_unit_types(self):
        """Промпт описывает допустимые единицы."""
        assert "кг" in _RECIPE_PROMPT
        assert "шт" in _RECIPE_PROMPT

    @_has_recipe_prompt
    def test_excludes_common_items(self):
        """Промпт исключает соль, сахар, молотый перец, воду."""
        lower = _RECIPE_PROMPT.lower()
        assert "соль" in lower
        assert "сахар" in lower
        assert "перец" in lower
        assert "вод" in lower

    @_has_recipe_prompt
    def test_distinguishes_pepper_spice_from_vegetable(self):
        """Промпт различает перец-приправу и перец-овощ (болгарский)."""
        lower = _RECIPE_PROMPT.lower()
        assert "молотый" in lower
        assert "болгарский" in lower or "чили" in lower

    @_has_recipe_prompt
    def test_alcohol_optional_flag(self):
        """Промпт инструктирует добавлять optional=true для алкоголя."""
        lower = _RECIPE_PROMPT.lower()
        assert "алкоголь" in lower
        assert "optional" in lower
        assert "ром" in lower

    @_has_recipe_prompt
    def test_coffee_instruction(self):
        """Промпт содержит инструкцию для кофе: 'кофе молотый'."""
        lower = _RECIPE_PROMPT.lower()
        assert "кофе молотый" in lower

    @_has_recipe_prompt
    def test_has_search_query_good_bad_examples(self):
        """Промпт содержит примеры хороших и плохих search_query."""
        lower = _RECIPE_PROMPT.lower()
        assert "хорошо" in lower
        assert "плохо" in lower
        assert "картофель" in lower
        assert "картошка клубень" in lower

    @_has_recipe_prompt
    def test_has_mandatory_ingredients_checklist(self):
        """Промпт содержит чеклист обязательных ингредиентов."""
        lower = _RECIPE_PROMPT.lower()
        assert "обязательные ингредиенты" in lower
        assert "лук репчатый" in lower
        assert "масло растительное" in lower

    @_has_recipe_prompt
    def test_has_exact_spice_names_rule(self):
        """Промпт запрещает жаргон для специй."""
        lower = _RECIPE_PROMPT.lower()
        assert "точное название" in lower
        assert "лавровый лист" in lower
        assert "лаврушка" in lower

    @_has_recipe_prompt
    def test_reasonable_length(self):
        """Промпт рецептов разумного размера."""
        assert 100 < len(_RECIPE_PROMPT) < 5000


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
# RECIPE_SEARCH_TOOL
# ============================================================================


class TestRecipeSearchTool:
    """Тесты описания инструмента recipe_search."""

    def test_tool_name(self):
        assert RECIPE_SEARCH_TOOL["name"] == "recipe_search"

    def test_required_ingredients(self):
        params = RECIPE_SEARCH_TOOL["parameters"]
        assert "ingredients" in params["required"]

    def test_has_ingredients_items_schema(self):
        items = RECIPE_SEARCH_TOOL["parameters"]["properties"]["ingredients"]["items"]
        props = items["properties"]
        assert "search_query" in props
        assert "quantity" in props
        assert "unit" in props


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
