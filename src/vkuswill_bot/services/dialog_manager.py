"""Управление историей диалогов пользователей.

Отвечает за:
- LRU-кэш диалогов (OrderedDict)
- Per-user asyncio.Lock для защиты от race condition
- Обрезку истории (trim / trim_list)
- Сброс диалога (reset)
- Async-интерфейс (aget_history, save_history, areset) для совместимости
  с RedisDialogManager
"""

import asyncio
import logging
from collections import OrderedDict

from gigachat.models import Messages, MessagesRole

from vkuswill_bot.services.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# Лимит одновременно хранимых диалогов (LRU-вытеснение)
MAX_CONVERSATIONS = 1000


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
                    "LRU-вытеснение: удалён диалог пользователя %d "
                    "(лимит %d диалогов)",
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
            self._conversations[user_id] = (
                [history[0]] + history[-(self._max_history - 1):]
            )

    def reset(self, user_id: int) -> None:
        """Сбросить историю диалога пользователя."""
        self._conversations.pop(user_id, None)
        self._locks.pop(user_id, None)

    # ---- Async API (единый интерфейс с RedisDialogManager) ----

    async def aget_history(self, user_id: int) -> list[Messages]:
        """Async-обёртка get_history (для in-memory — trivially sync)."""
        return self.get_history(user_id)

    async def save_history(
        self, user_id: int, history: list[Messages],
    ) -> None:
        """Сохранить историю диалога.

        Для in-memory: обновляет ссылку в _conversations (необходимо,
        т.к. trim_list может вернуть новый список).
        """
        self._conversations[user_id] = history

    def trim_list(self, history: list[Messages]) -> list[Messages]:
        """Обрезать историю (чистая функция, не зависит от хранилища).

        Принимает и возвращает list — работает одинаково для in-memory
        и Redis-бэкенда.
        """
        if len(history) > self._max_history:
            return [history[0]] + history[-(self._max_history - 1):]
        return history

    async def areset(self, user_id: int) -> None:
        """Async-обёртка reset (для in-memory — trivially sync)."""
        self.reset(user_id)

    # ---- Свойства ----

    @property
    def conversations(self) -> OrderedDict[int, list[Messages]]:
        """Доступ к conversations для обратной совместимости."""
        return self._conversations
