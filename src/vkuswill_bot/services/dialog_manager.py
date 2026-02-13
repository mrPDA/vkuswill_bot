"""Управление историей диалогов пользователей.

Отвечает за:
- LRU-кэш диалогов (OrderedDict)
- Per-user asyncio.Lock для защиты от race condition
- Обрезку истории (trim / trim_list) с суммаризацией tool results
- Сброс диалога (reset)
- Async-интерфейс (aget_history, save_history, areset) для совместимости
  с RedisDialogManager
"""

import asyncio
import json
import logging
from collections import OrderedDict

from gigachat.models import Messages, MessagesRole

from vkuswill_bot.services.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Лимит одновременно хранимых диалогов (LRU-вытеснение)
MAX_CONVERSATIONS = 1000

# Макс. длина суммаризированного tool result (символы)
MAX_SUMMARY_LENGTH = 200


def _summarize_tool_result(name: str | None, content: str) -> str:
    """Суммаризировать tool result для экономии токенов в истории.

    Заменяет полные JSON-ответы инструментов на краткие резюме.
    Вызывается при обрезке истории для старых FUNCTION-сообщений.

    Returns:
        Краткое текстовое резюме результата инструмента.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        # Не JSON — обрезаем до MAX_SUMMARY_LENGTH
        if len(content) > MAX_SUMMARY_LENGTH:
            return content[:MAX_SUMMARY_LENGTH] + "…"
        return content

    if not isinstance(data, dict):
        if len(content) > MAX_SUMMARY_LENGTH:
            return content[:MAX_SUMMARY_LENGTH] + "…"
        return content

    # vkusvill_products_search → краткое резюме
    if name == "vkusvill_products_search" or "products" in data:
        products = data.get("products", [])
        query = data.get("query", "")
        if products and isinstance(products, list):
            first = products[0]
            first_name = first.get("name", "?")
            first_price = first.get("price", "?")
            return (
                f"Поиск \"{query}\": найдено {len(products)} товаров, "
                f"лучший: {first_name} ({first_price}₽)"
            )
        return f"Поиск \"{query}\": найдено 0 товаров"

    # vkusvill_cart_link_create → краткое резюме
    if name == "vkusvill_cart_link_create" or "cart_link" in data:
        ps = data.get("price_summary", {})
        total = ps.get("total", data.get("total", "?"))
        count = ps.get("count", len(data.get("items", [])))
        link = data.get("cart_link", data.get("link", ""))
        return f"Корзина: {count} товаров, итого {total}₽, ссылка: {link}"

    # user_preferences_get → краткое резюме
    if name == "user_preferences_get" or "preferences" in data:
        prefs = data.get("preferences", data)
        if isinstance(prefs, dict):
            items = [f"{k}: {v}" for k, v in list(prefs.items())[:5]]
            return f"Предпочтения: {', '.join(items)}" if items else "Предпочтения: пусто"
        return f"Предпочтения: {str(prefs)[:MAX_SUMMARY_LENGTH]}"

    # recipe_ingredients → краткое резюме
    if name == "recipe_ingredients" or "ingredients" in data:
        dish = data.get("dish", "?")
        ingredients = data.get("ingredients", [])
        count = len(ingredients) if isinstance(ingredients, list) else "?"
        return f"Рецепт \"{dish}\": {count} ингредиентов"

    # nutrition_lookup → краткое резюме
    if name == "nutrition_lookup":
        product = data.get("product", data.get("query", "?"))
        kcal = data.get("kcal", data.get("calories", "?"))
        return f"КБЖУ \"{product}\": {kcal} ккал/100г"

    # Fallback: обрезка до MAX_SUMMARY_LENGTH
    if len(content) > MAX_SUMMARY_LENGTH:
        return content[:MAX_SUMMARY_LENGTH] + "…"
    return content


class DialogManager:
    """Управление историей диалогов пользователей (in-memory).

    Хранит LRU-кэш диалогов в памяти с per-user lock
    для защиты от параллельных мутаций.

    Предоставляет два API:
    - Sync (get_history, trim, reset) — для обратной совместимости и тестов.
    - Async (aget_history, save_history, trim_list, areset) — единый интерфейс
      с RedisDialogManager, используется в GigaChatService.
    """

    def __init__(
        self,
        max_conversations: int = MAX_CONVERSATIONS,
        max_history: int = 50,
    ) -> None:
        self._max_conversations = max_conversations
        self._max_history = max_history
        self._conversations: OrderedDict[int, list[Messages]] = OrderedDict()
        self._locks: dict[int, asyncio.Lock] = {}

    # ---- Per-user lock (общий для sync и async) ----

    def get_lock(self, user_id: int) -> asyncio.Lock:
        """Per-user lock для защиты от параллельных мутаций.

        Ленивая инициализация: lock создаётся при первом обращении.
        """
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    # ---- Sync API (обратная совместимость) ----

    def get_history(self, user_id: int) -> list[Messages]:
        """Получить или создать историю диалога пользователя.

        Использует LRU-вытеснение: при превышении max_conversations
        удаляется самый давний неиспользуемый диалог.
        """
        if user_id in self._conversations:
            # Перемещаем в конец (самый свежий)
            self._conversations.move_to_end(user_id)
        else:
            # LRU-вытеснение: удаляем самый старый диалог при переполнении
            if len(self._conversations) >= self._max_conversations:
                evicted_user_id, _ = self._conversations.popitem(last=False)
                logger.info(
                    "LRU-вытеснение: удалён диалог пользователя %d (лимит %d диалогов)",
                    evicted_user_id,
                    self._max_conversations,
                )
            self._conversations[user_id] = [
                Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)
            ]
        return self._conversations[user_id]

    def trim(self, user_id: int) -> None:
        """Обрезать историю, оставляя системный промпт и последние сообщения.

        Если история длиннее max_history, оставляем первый элемент
        (системный промпт) + последние (max_history - 1) сообщений.
        """
        history = self._conversations.get(user_id)
        if history and len(history) > self._max_history:
            self._conversations[user_id] = [history[0], *history[-(self._max_history - 1) :]]

    def reset(self, user_id: int) -> None:
        """Сбросить историю диалога пользователя."""
        self._conversations.pop(user_id, None)
        self._locks.pop(user_id, None)

    # ---- Async API (единый интерфейс с RedisDialogManager) ----

    async def aget_history(self, user_id: int) -> list[Messages]:
        """Async-обёртка get_history (для in-memory — trivially sync)."""
        return self.get_history(user_id)

    async def save_history(
        self,
        user_id: int,
        history: list[Messages],
    ) -> None:
        """Сохранить историю диалога.

        Для in-memory: обновляет ссылку в _conversations (необходимо,
        т.к. trim_list может вернуть новый список).
        """
        self._conversations[user_id] = history

    def trim_list(self, history: list[Messages]) -> list[Messages]:
        """Обрезать историю с суммаризацией старых tool results.

        Принимает и возвращает list — работает одинаково для in-memory
        и Redis-бэкенда.

        Стратегия:
        1. Системный промпт (history[0]) всегда сохраняется.
        2. Последние (max_history - 1) сообщений — без изменений.
        3. Более старые FUNCTION-сообщения заменяются на краткие резюме.
        4. Если после суммаризации длина всё ещё > max_history — обрезаем.
        """
        if len(history) <= self._max_history:
            return history

        system = history[0]
        # Граница: recent — последние (max_history - 1) сообщений
        recent_start = len(history) - (self._max_history - 1)
        old_messages = history[1:recent_start]
        recent_messages = history[recent_start:]

        # Суммаризируем старые FUNCTION-сообщения
        summarized_old: list[Messages] = []
        for msg in old_messages:
            if str(msg.role) == "function" and msg.content:
                summary = _summarize_tool_result(
                    getattr(msg, "name", None),
                    msg.content,
                )
                summarized_old.append(
                    Messages(
                        role=msg.role,
                        content=summary,
                        name=getattr(msg, "name", None),
                    )
                )
            else:
                summarized_old.append(msg)

        result = [system, *summarized_old, *recent_messages]

        # Финальная обрезка если всё ещё слишком длинная
        if len(result) > self._max_history:
            result = [system, *result[-(self._max_history - 1) :]]

        return result

    async def areset(self, user_id: int) -> None:
        """Async-обёртка reset (для in-memory — trivially sync)."""
        self.reset(user_id)

    # ---- Свойства ----

    @property
    def conversations(self) -> OrderedDict[int, list[Messages]]:
        """Доступ к conversations для обратной совместимости."""
        return self._conversations
