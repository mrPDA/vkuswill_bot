"""Извлечение и кэширование рецептов.

Отвечает за:
- Извлечение ингредиентов через GigaChat
- Кэширование через RecipeStore
- Обогащение весами (_enrich_with_kg)
- Масштабирование порций
- Форматирование результата для function calling
"""

import asyncio
import json
import logging

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from vkuswill_bot.services.prompts import RECIPE_EXTRACTION_PROMPT
from vkuswill_bot.services.recipe_store import RecipeStore

logger = logging.getLogger(__name__)

# ---- Ферментированные / консервированные продукты ----
# Эти продукты НЕЛЬЗЯ разбирать на сырые ингредиенты,
# их приготовление занимает дни/недели. Бот должен искать готовые.
FERMENTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "квашеная",
        "квашеный",
        "квашеное",
        "квашеные",
        "солёная",
        "солёный",
        "солёное",
        "солёные",
        "соленая",
        "соленый",
        "соленое",
        "соленые",
        "маринованная",
        "маринованный",
        "маринованное",
        "маринованные",
        "мочёная",
        "мочёный",
        "мочёное",
        "мочёные",
        "моченая",
        "моченый",
        "моченое",
        "моченые",
        "кимчи",
        "аджика",
        "ткемали",
        "горчица",
        "варенье",
        "джем",
        "повидло",
        "конфитюр",
    }
)

# Приблизительный вес 1 штуки в кг для овощей/фруктов
PIECE_WEIGHT_KG: dict[str, float] = {
    "картофель": 0.15,
    "картошка": 0.15,
    "морковь": 0.15,
    "морковка": 0.15,
    "свекла": 0.3,
    "буряк": 0.3,
    "лук": 0.1,
    "луковица": 0.1,
    "яблоко": 0.2,
    "помидор": 0.15,
    "томат": 0.15,
    "огурец": 0.12,
    "перец": 0.15,
    "перец болгарский": 0.15,
    "баклажан": 0.3,
    "кабачок": 0.3,
}


class RecipeService:
    """Извлечение и кэширование рецептов.

    Использует GigaChat для извлечения списка ингредиентов
    и RecipeStore для кэширования результатов.
    """

    def __init__(
        self,
        gigachat_client: GigaChat,
        recipe_store: RecipeStore,
    ) -> None:
        self._client = gigachat_client
        self._recipe_store = recipe_store

    @staticmethod
    def is_fermented_product(dish: str) -> bool:
        """Проверить, является ли блюдо ферментированным/консервированным.

        Такие продукты нельзя разбирать на сырые ингредиенты —
        их приготовление занимает дни и недели.
        """
        words = dish.lower().split()
        return any(word in FERMENTED_KEYWORDS for word in words)

    async def get_ingredients(self, args: dict) -> str:
        """Обработать вызов recipe_ingredients: кеш → LLM-fallback → кеш.

        Args:
            args: Словарь с ключами dish (str) и servings (int, optional).

        Returns:
            JSON-строка с ингредиентами рецепта.
        """
        dish = args.get("dish", "").strip()
        if not dish:
            return json.dumps(
                {"ok": False, "error": "Не указано название блюда"},
                ensure_ascii=False,
            )

        # Блокируем ферментированные/консервированные продукты
        if self.is_fermented_product(dish):
            logger.info(
                "Блокировка рецепта для ферментированного продукта: %r",
                dish,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"«{dish}» — это готовый ферментированный/консервированный "
                        "продукт. Его нельзя приготовить за вечер! "
                        "Ищи его как ГОТОВЫЙ ТОВАР через "
                        f'vkusvill_products_search(q="{dish}"). '
                        "НЕ разбирай на сырые ингредиенты."
                    ),
                },
                ensure_ascii=False,
            )

        servings = args.get("servings", 2)
        if not isinstance(servings, int) or servings <= 0:
            servings = 2

        # 1. Проверяем кеш
        cached = await self._recipe_store.get(dish)
        if cached is not None:
            ingredients = cached["ingredients"]
            # Масштабируем если другое количество порций
            if cached["servings"] != servings:
                ingredients = RecipeStore.scale_ingredients(
                    ingredients,
                    cached["servings"],
                    servings,
                )
            logger.info(
                "Рецепт из кеша: %s на %d порций (%d ингредиентов)",
                dish,
                servings,
                len(ingredients),
            )
            # Обогащаем ингредиенты эквивалентом в кг
            ingredients = self._enrich_with_kg(ingredients, PIECE_WEIGHT_KG)
            return self._format_result(
                dish,
                servings,
                ingredients,
                cached=True,
            )

        # 2. Извлекаем через GigaChat
        try:
            ingredients = await self._extract_from_llm(dish, servings)
        except Exception as e:
            logger.error(
                "Ошибка извлечения рецепта '%s': %s",
                dish,
                e,
                exc_info=True,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": (
                        f"Не удалось получить рецепт для «{dish}». "
                        "Составь список ингредиентов самостоятельно."
                    ),
                },
                ensure_ascii=False,
            )

        # 3. Сохраняем в кеш
        try:
            await self._recipe_store.save(dish, servings, ingredients)
        except Exception as e:
            logger.warning("Не удалось закешировать рецепт '%s': %s", dish, e)

        # Обогащаем ингредиенты эквивалентом в кг
        ingredients = self._enrich_with_kg(ingredients, PIECE_WEIGHT_KG)
        return self._format_result(
            dish,
            servings,
            ingredients,
            cached=False,
        )

    async def _extract_from_llm(
        self,
        dish: str,
        servings: int,
    ) -> list[dict]:
        """Извлечь ингредиенты рецепта через отдельный вызов GigaChat.

        Делает один точечный запрос без function calling.

        Returns:
            Список ингредиентов [{name, quantity, unit, search_query}].

        Raises:
            ValueError: если ответ не является валидным JSON-массивом.
        """
        prompt = RECIPE_EXTRACTION_PROMPT.format(dish=dish, servings=servings)
        logger.info("Извлечение рецепта: %s на %d порций", dish, servings)

        response = await asyncio.to_thread(
            self._client.chat,
            Chat(
                messages=[Messages(role=MessagesRole.USER, content=prompt)],
            ),
        )

        content = response.choices[0].message.content or ""
        ingredients = self._parse_json(content)

        if not isinstance(ingredients, list) or not ingredients:
            raise ValueError(f"Ожидался непустой JSON-массив, получено: {content[:200]}")

        logger.info(
            "Извлечено %d ингредиентов для '%s'",
            len(ingredients),
            dish,
        )
        return ingredients

    @staticmethod
    def _parse_json(content: str) -> list | dict:
        """Распарсить JSON из ответа GigaChat, убирая markdown-обёртку.

        GigaChat иногда оборачивает JSON в ```json...```.
        """
        text = content.strip()

        # Убираем markdown code block
        if text.startswith("```"):
            lines = text.split("\n")
            # Убираем первую строку (```json или ```)
            if lines[0].startswith("```"):
                lines = lines[1:]
            # Убираем последнюю строку (```)
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        return json.loads(text)

    @staticmethod
    def _enrich_with_kg(
        ingredients: list[dict],
        piece_weights: dict[str, float],
    ) -> list[dict]:
        """Добавить поле kg_equivalent для ингредиентов.

        Конвертирует разные единицы в кг, чтобы GigaChat
        не ошибался при расчёте q для весовых товаров:
        - г → кг (200 г → 0.2 кг)
        - шт → кг (3 картофелины → 0.45 кг через таблицу весов)
        - кг → без изменений (уже готовое значение)
        """
        for item in ingredients:
            if not isinstance(item, dict):
                continue
            unit = item.get("unit", "")
            quantity = item.get("quantity", 0)
            name = item.get("name", "").lower()

            if not quantity or quantity <= 0:
                continue

            # Граммы → килограммы (прямая конвертация)
            if unit == "г":
                item["kg_equivalent"] = round(quantity / 1000, 3)
                continue

            # Миллилитры → литры (прямая конвертация)
            if unit == "мл":
                item["l_equivalent"] = round(quantity / 1000, 3)
                continue

            # кг и л — уже готовые значения, пропускаем
            if unit in ("кг", "л"):
                continue

            # Штучные: ищем совпадение в таблице весов
            weight_per_piece = None
            for key, w in piece_weights.items():
                if key in name:
                    weight_per_piece = w
                    break

            if weight_per_piece is not None:
                kg_eq = round(quantity * weight_per_piece, 2)
                item["kg_equivalent"] = kg_eq

        return ingredients

    @staticmethod
    def _format_result(
        dish: str,
        servings: int,
        ingredients: list[dict],
        cached: bool,
    ) -> str:
        """Сформировать JSON-результат recipe_ingredients."""
        return json.dumps(
            {
                "ok": True,
                "dish": dish,
                "servings": servings,
                "ingredients": ingredients,
                "cached": cached,
                "hint": (
                    "ТВОЙ СЛЕДУЮЩИЙ ШАГ: вызови vkusvill_products_search "
                    "для КАЖДОГО ингредиента из списка, используя search_query. "
                    "Затем из результатов поиска возьми xml_id лучшего товара "
                    "и создай корзину через vkusvill_cart_link_create. "
                    "НЕ пропускай ни одного ингредиента! "
                    "НЕ ищи и НЕ добавляй от себя соль, молотый перец, "
                    "воду и другие продукты, которых нет в списке! "
                    "РАСЧЁТ КОЛИЧЕСТВА (q): "
                    "если у ингредиента есть kg_equivalent — "
                    "используй его как q для товаров в кг. "
                    "Если есть l_equivalent — используй его как q "
                    "для товаров в литрах. "
                    "Примеры: kg_equivalent=0.2 → q=0.2 (для товара в кг); "
                    "l_equivalent=0.5 → q=0.5 (для товара в л); "
                    "Для штучных товаров (unit='шт') — q = целое число."
                ),
            },
            ensure_ascii=False,
        )
