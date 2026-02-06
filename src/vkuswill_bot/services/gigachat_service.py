"""Сервис GigaChat с поддержкой function calling через MCP-инструменты."""

import asyncio
import json
import logging
from collections import OrderedDict

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.preferences_store import PreferencesStore
from vkuswill_bot.services.prompts import (
    ERROR_GIGACHAT,
    ERROR_TOO_MANY_STEPS,
    SYSTEM_PROMPT,
)
from vkuswill_bot.services.search_processor import SearchProcessor

logger = logging.getLogger(__name__)

# Лимит одновременно хранимых диалогов (LRU-вытеснение)
MAX_CONVERSATIONS = 1000

# Лимит длины входящего сообщения пользователя (символы)
MAX_USER_MESSAGE_LENGTH = 4096

# Лимит длины результата инструмента для логирования
MAX_RESULT_LOG_LENGTH = 1000

# Лимит длины preview результата для логирования
MAX_RESULT_PREVIEW_LENGTH = 500

# Макс. повторных вызовов одного инструмента с одинаковыми аргументами
MAX_IDENTICAL_TOOL_CALLS = 2


class GigaChatService:
    """Сервис для взаимодействия с GigaChat и MCP-инструментами.

    Управляет историей диалогов пользователей и реализует цикл
    function calling: GigaChat решает, какой инструмент вызвать,
    бот выполняет вызов через MCP, результат возвращается в GigaChat.
    """

    # Описания локальных tool-функций для предпочтений
    _LOCAL_TOOLS: list[dict] = [
        {
            "name": "user_preferences_get",
            "description": (
                "Получить сохранённые предпочтения пользователя. "
                "Вызывай перед поиском товаров, чтобы учесть вкусы."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
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
                            "Категория продукта, например: "
                            "мороженое, молоко, хлеб, сыр"
                        ),
                    },
                    "preference": {
                        "type": "string",
                        "description": (
                            "Конкретное описание предпочтения, например: "
                            "пломбир в шоколаде на палочке"
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

    def __init__(
        self,
        credentials: str,
        model: str,
        scope: str,
        mcp_client: VkusvillMCPClient,
        preferences_store: PreferencesStore | None = None,
        max_tool_calls: int = 15,
        max_history: int = 50,
    ) -> None:
        # TODO: включить SSL-верификацию когда GigaChat SDK
        # будет поддерживать CA-сертификат Минцифры.
        # Отслеживать: https://github.com/ai-forever/gigachat/issues
        # Для включения: verify_ssl_certs=True + ca_bundle_file="path/to/russian_ca.pem"
        self._client = GigaChat(
            credentials=credentials,
            model=model,
            scope=scope,
            verify_ssl_certs=False,
            timeout=60,
        )
        self._mcp_client = mcp_client
        self._prefs_store = preferences_store
        self._max_tool_calls = max_tool_calls
        self._max_history = max_history
        self._conversations: OrderedDict[int, list[Messages]] = OrderedDict()
        self._functions: list[dict] | None = None

        # Процессоры: поиск и корзина
        self._search_processor = SearchProcessor()
        self._cart_processor = CartProcessor(self._search_processor.price_cache)

    # ---- Загрузка описаний функций для GigaChat ----

    async def _get_functions(self) -> list[dict]:
        """Получить описания функций для GigaChat из MCP- и локальных инструментов."""
        if self._functions is not None:
            return self._functions

        tools = await self._mcp_client.get_tools()
        self._functions = []
        for tool in tools:
            params = tool["parameters"]
            # Дополняем схему корзины описаниями для GigaChat
            if tool["name"] == "vkusvill_cart_link_create":
                params = CartProcessor.enhance_cart_schema(params)
            self._functions.append(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": params,
                }
            )

        # Добавляем локальные инструменты (предпочтения)
        if self._prefs_store is not None:
            self._functions.extend(self._LOCAL_TOOLS)

        logger.info("Функции для GigaChat: %s", [f["name"] for f in self._functions])
        return self._functions

    # ---- Управление историей диалогов ----

    def _get_history(self, user_id: int) -> list[Messages]:
        """Получить или создать историю диалога пользователя.

        Использует LRU-вытеснение: при превышении MAX_CONVERSATIONS
        удаляется самый давний неиспользуемый диалог.
        """
        if user_id in self._conversations:
            # Перемещаем в конец (самый свежий)
            self._conversations.move_to_end(user_id)
        else:
            # LRU-вытеснение: удаляем самый старый диалог при переполнении
            if len(self._conversations) >= MAX_CONVERSATIONS:
                evicted_user_id, _ = self._conversations.popitem(last=False)
                logger.info(
                    "LRU-вытеснение: удалён диалог пользователя %d "
                    "(лимит %d диалогов)",
                    evicted_user_id,
                    MAX_CONVERSATIONS,
                )
            self._conversations[user_id] = [
                Messages(role=MessagesRole.SYSTEM, content=SYSTEM_PROMPT)
            ]
        return self._conversations[user_id]

    def _trim_history(self, user_id: int) -> None:
        """Обрезать историю, оставляя системный промпт и последние сообщения."""
        history = self._conversations.get(user_id)
        if history and len(history) > self._max_history:
            self._conversations[user_id] = (
                [history[0]] + history[-(self._max_history - 1) :]
            )

    def reset_conversation(self, user_id: int) -> None:
        """Сбросить историю диалога пользователя."""
        self._conversations.pop(user_id, None)

    # ---- Подстановка предпочтений в поисковые запросы ----

    @staticmethod
    def _parse_preferences(result_text: str) -> dict[str, str]:
        """Извлечь предпочтения из результата user_preferences_get.

        Returns:
            Словарь {категория_lower: preference_text}.
        """
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return {}

        prefs = data.get("preferences", [])
        if not isinstance(prefs, list):
            return {}

        result: dict[str, str] = {}
        for item in prefs:
            if not isinstance(item, dict):
                continue
            cat = item.get("category", "").strip().lower()
            pref = item.get("preference", "").strip()
            if cat and pref:
                result[cat] = pref
        return result

    @staticmethod
    def _apply_preferences_to_query(
        query: str,
        user_prefs: dict[str, str],
    ) -> str:
        """Подставить предпочтения в поисковый запрос.

        Если очищенный запрос совпадает с категорией предпочтения
        (точное вхождение), формируем уточнённый запрос:
          «категория + предпочтение».

        Примеры (при prefs={"вареники": "с картофелем и шкварками"}):
          "вареники" → "вареники с картофелем и шкварками"
          "творог"   → "творог" (нет предпочтения)

        Если предпочтение уже содержит категорию, возвращаем само
        предпочтение без дублирования.

        Args:
            query: очищенный поисковый запрос (после _clean_search_query).
            user_prefs: словарь {категория_lower: preference_text}.

        Returns:
            Уточнённый или оригинальный запрос.
        """
        if not user_prefs or not query:
            return query

        q_lower = query.strip().lower()

        # Точное совпадение: "вареники" == "вареники"
        pref = user_prefs.get(q_lower)

        if pref is None:
            return query

        # Если предпочтение уже содержит исходный запрос — используем как есть
        if q_lower in pref.lower():
            return pref

        # Иначе: "вареники" + "с картофелем и шкварками"
        return f"{query} {pref}"

    # ---- Маршрутизация локальных инструментов ----

    # Имена локальных инструментов (для маршрутизации)
    _LOCAL_TOOL_NAMES = frozenset({
        "user_preferences_get",
        "user_preferences_set",
        "user_preferences_delete",
    })

    async def _call_local_tool(
        self, tool_name: str, args: dict, user_id: int,
    ) -> str:
        """Выполнить локальный инструмент (предпочтения).

        Raises:
            ValueError: если инструмент не найден или store не настроен.
        """
        if self._prefs_store is None:
            return json.dumps(
                {"ok": False, "error": "Хранилище предпочтений не настроено"},
                ensure_ascii=False,
            )

        if tool_name == "user_preferences_get":
            return await self._prefs_store.get_formatted(user_id)
        elif tool_name == "user_preferences_set":
            category = args.get("category", "")
            preference = args.get("preference", "")
            if not category or not preference:
                return json.dumps(
                    {"ok": False, "error": "Не указана категория или предпочтение"},
                    ensure_ascii=False,
                )
            return await self._prefs_store.set(user_id, category, preference)
        elif tool_name == "user_preferences_delete":
            category = args.get("category", "")
            if not category:
                return json.dumps(
                    {"ok": False, "error": "Не указана категория"},
                    ensure_ascii=False,
                )
            return await self._prefs_store.delete(user_id, category)
        else:
            return json.dumps(
                {"ok": False, "error": f"Неизвестный локальный инструмент: {tool_name}"},
                ensure_ascii=False,
            )

    async def close(self) -> None:
        """Закрыть клиент GigaChat."""
        try:
            await asyncio.to_thread(self._client.close)
        except Exception as e:
            logger.debug("Ошибка при закрытии GigaChat клиента: %s", e)

    # ---- Вспомогательные методы для process_message ----

    @staticmethod
    def _parse_tool_arguments(raw_args: str | dict | None) -> dict:
        """Распарсить аргументы вызова инструмента от GigaChat.

        Args:
            raw_args: Аргументы — JSON-строка, dict или None.

        Returns:
            Словарь аргументов (пустой при ошибке парсинга).
        """
        if isinstance(raw_args, str):
            try:
                return json.loads(raw_args)
            except json.JSONDecodeError:
                return {}
        if isinstance(raw_args, dict):
            return raw_args
        return {}

    @staticmethod
    def _append_assistant_message(
        history: list[Messages], msg: object,
    ) -> None:
        """Создать сообщение ассистента и добавить в историю."""
        assistant_msg = Messages(
            role=MessagesRole.ASSISTANT,
            content=msg.content or "",
        )
        if msg.function_call:
            assistant_msg.function_call = msg.function_call
        if hasattr(msg, "functions_state_id") and msg.functions_state_id:
            assistant_msg.functions_state_id = msg.functions_state_id
        history.append(assistant_msg)

    def _preprocess_tool_args(
        self,
        tool_name: str,
        args: dict,
        user_prefs: dict[str, str],
    ) -> dict:
        """Предобработать аргументы инструмента перед вызовом.

        - Округляет дробные q для штучных товаров в корзине.
        - Подставляет предпочтения пользователя в поисковый запрос.
        """
        if tool_name == "vkusvill_cart_link_create":
            args = self._cart_processor.fix_unit_quantities(args)

        if tool_name == "vkusvill_products_search" and user_prefs:
            q = args.get("q", "")
            enhanced_q = self._apply_preferences_to_query(q, user_prefs)
            if enhanced_q != q:
                logger.info(
                    "Подстановка предпочтения: %r → %r", q, enhanced_q,
                )
                args = {**args, "q": enhanced_q}

        return args

    def _is_duplicate_call(
        self,
        tool_name: str,
        args: dict,
        call_counts: dict[str, int],
        history: list[Messages],
    ) -> bool:
        """Проверить, не является ли вызов дублем, и обработать зацикливание.

        Отслеживает повторные вызовы с идентичными аргументами.
        При дубле добавляет подсказку в историю и возвращает True.

        Returns:
            True если вызов дублирован и его нужно пропустить.
        """
        call_key = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
        call_counts[call_key] = call_counts.get(call_key, 0) + 1

        if call_counts[call_key] >= MAX_IDENTICAL_TOOL_CALLS:
            logger.warning(
                "Зацикливание: %s вызван %d раз с одинаковыми "
                "аргументами, пропускаю вызов",
                tool_name,
                call_counts[call_key],
            )
            history.append(Messages(
                role=MessagesRole.FUNCTION,
                content=(
                    "Ты уже вызывал этот инструмент с теми же аргументами. "
                    "Результат тот же. Используй уже полученные данные "
                    "и продолжай — не повторяй этот вызов."
                ),
                name=tool_name,
            ))
            return True
        return False

    async def _execute_tool(
        self,
        tool_name: str,
        args: dict,
        user_id: int,
    ) -> str:
        """Выполнить вызов инструмента (локальный или MCP) с обработкой ошибок.

        Returns:
            Строковый результат вызова (JSON).
        """
        try:
            if tool_name in self._LOCAL_TOOL_NAMES:
                return await self._call_local_tool(tool_name, args, user_id)
            return await self._mcp_client.call_tool(tool_name, args)
        except Exception as e:
            logger.error("Ошибка %s: %s", tool_name, e, exc_info=True)
            return json.dumps(
                {"error": f"Ошибка вызова {tool_name}: {e}"},
                ensure_ascii=False,
            )

    def _postprocess_tool_result(
        self,
        tool_name: str,
        args: dict,
        result: str,
        user_prefs: dict[str, str],
        search_log: dict[str, set[int]],
    ) -> str:
        """Постобработать результат вызова инструмента.

        - Парсит предпочтения из user_preferences_get.
        - Кеширует цены и обрезает результат поиска.
        - Рассчитывает стоимость корзины и верифицирует.

        Мутирует user_prefs и search_log in-place.

        Returns:
            Обработанный результат (строка).
        """
        if tool_name == "user_preferences_get":
            parsed = self._parse_preferences(result)
            if parsed:
                user_prefs.clear()
                user_prefs.update(parsed)
                logger.info(
                    "Загружены предпочтения: %s",
                    {k: v for k, v in user_prefs.items()},
                )

        elif tool_name == "vkusvill_products_search":
            self._search_processor.cache_prices(result)
            query = args.get("q", "")
            found_ids = self._search_processor.extract_xml_ids(result)
            if query and found_ids:
                search_log[query] = found_ids
            result = self._search_processor.trim_search_result(result)

        elif tool_name == "vkusvill_cart_link_create":
            result = self._cart_processor.calc_total(args, result)
            if search_log:
                result = self._cart_processor.add_verification(
                    args, result, search_log,
                )
            logger.info(
                "Расчёт корзины: %s",
                result[:MAX_RESULT_PREVIEW_LENGTH],
            )

        return result

    # ---- Основной метод обработки сообщений ----

    async def process_message(self, user_id: int, text: str) -> str:
        """Обработать сообщение пользователя.

        Реализует цикл function calling:
        1. Отправляет сообщение в GigaChat с описанием инструментов.
        2. Если GigaChat хочет вызвать инструмент — выполняет через MCP.
        3. Результат возвращается в GigaChat для следующего шага.
        4. Цикл продолжается, пока GigaChat не вернёт текстовый ответ.

        Args:
            user_id: ID пользователя Telegram.
            text: Текст сообщения.

        Returns:
            Ответ GigaChat для пользователя.
        """
        # Ограничение длины входящего сообщения
        if len(text) > MAX_USER_MESSAGE_LENGTH:
            logger.warning(
                "Сообщение пользователя %d обрезано: %d -> %d символов",
                user_id,
                len(text),
                MAX_USER_MESSAGE_LENGTH,
            )
            text = text[:MAX_USER_MESSAGE_LENGTH]

        history = self._get_history(user_id)
        history.append(Messages(role=MessagesRole.USER, content=text))

        functions = await self._get_functions()
        call_counts: dict[str, int] = {}
        search_log: dict[str, set[int]] = {}
        user_prefs: dict[str, str] = {}

        for step in range(self._max_tool_calls):
            logger.info("Шаг %d для пользователя %d", step + 1, user_id)

            try:
                response = await asyncio.to_thread(
                    self._client.chat,
                    Chat(
                        messages=history,
                        functions=functions,
                        function_call="auto",
                    ),
                )
            except Exception as e:
                logger.error("Ошибка GigaChat: %s", e, exc_info=True)
                return ERROR_GIGACHAT

            msg = response.choices[0].message
            self._append_assistant_message(history, msg)

            # GigaChat вернул текстовый ответ — конец цикла
            if not msg.function_call:
                self._trim_history(user_id)
                return msg.content or "Не удалось получить ответ."

            tool_name = msg.function_call.name
            args = self._parse_tool_arguments(msg.function_call.arguments)

            logger.info(
                "Вызов инструмента: %s(%s)",
                tool_name,
                json.dumps(args, ensure_ascii=False),
            )

            # Предобработка аргументов
            args = self._preprocess_tool_args(tool_name, args, user_prefs)

            # Проверка зацикливания
            if self._is_duplicate_call(tool_name, args, call_counts, history):
                continue

            # Выполнение инструмента
            result = await self._execute_tool(tool_name, args, user_id)

            logger.info(
                "Результат %s: %s",
                tool_name,
                result[:MAX_RESULT_LOG_LENGTH]
                if len(result) > MAX_RESULT_LOG_LENGTH
                else result,
            )

            # Постобработка результата
            result = self._postprocess_tool_result(
                tool_name, args, result, user_prefs, search_log,
            )

            # Добавляем результат функции в историю
            history.append(Messages(
                role=MessagesRole.FUNCTION,
                content=result,
                name=tool_name,
            ))

        # Достигнут лимит вызовов инструментов
        self._trim_history(user_id)
        return ERROR_TOO_MANY_STEPS
