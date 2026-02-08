"""Маршрутизация и выполнение вызовов инструментов.

Отвечает за:
- Маршрутизацию: local tools vs MCP tools
- Выполнение с обработкой ошибок
- Детекцию зацикливания (дубли вызовов)
- Пре/постпроцессинг аргументов и результатов
- Парсинг предпочтений из результатов
"""

import json
import logging

from gigachat.models import Messages, MessagesRole

from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.preferences_store import PreferencesStore
from vkuswill_bot.services.search_processor import SearchProcessor

logger = logging.getLogger(__name__)

# Лимит длины результата инструмента для логирования
MAX_RESULT_LOG_LENGTH = 1000

# Лимит длины preview результата для логирования
MAX_RESULT_PREVIEW_LENGTH = 500

# Макс. повторных вызовов одного инструмента с одинаковыми аргументами
MAX_IDENTICAL_TOOL_CALLS = 2

# Имена локальных инструментов (для маршрутизации)
LOCAL_TOOL_NAMES = frozenset({
    "user_preferences_get",
    "user_preferences_set",
    "user_preferences_delete",
    "recipe_ingredients",
})


class CallTracker:
    """Отслеживание повторных вызовов инструментов.

    Хранит счётчики вызовов и результаты для детекции зацикливания.
    """

    def __init__(self) -> None:
        self.call_counts: dict[str, int] = {}
        self.call_results: dict[str, str] = {}

    def make_key(self, tool_name: str, args: dict) -> str:
        """Создать ключ для отслеживания вызова."""
        return f"{tool_name}:{json.dumps(args, sort_keys=True)}"

    def record_result(self, tool_name: str, args: dict, result: str) -> None:
        """Записать результат вызова."""
        key = self.make_key(tool_name, args)
        self.call_results[key] = result


class ToolExecutor:
    """Маршрутизация и выполнение вызовов инструментов.

    Единая точка входа для выполнения tool-вызовов:
    - Предобработка аргументов (подстановка предпочтений, округление q)
    - Маршрутизация (локальные vs MCP)
    - Выполнение с обработкой ошибок
    - Постобработка результатов (кеш цен, обрезка, расчёт корзины)
    - Детекция зацикливания
    """

    def __init__(
        self,
        mcp_client: VkusvillMCPClient,
        search_processor: SearchProcessor,
        cart_processor: CartProcessor,
        preferences_store: PreferencesStore | None = None,
    ) -> None:
        self._mcp_client = mcp_client
        self._search_processor = search_processor
        self._cart_processor = cart_processor
        self._prefs_store = preferences_store

    # ---- Парсинг аргументов ----

    @staticmethod
    def parse_arguments(raw_args: str | dict | None) -> dict:
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

    # ---- Сборка сообщения ассистента ----

    @staticmethod
    def build_assistant_message(
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

    # ---- Предобработка аргументов ----

    def preprocess_args(
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

    # ---- Детекция зацикливания ----

    def is_duplicate_call(
        self,
        tool_name: str,
        args: dict,
        call_tracker: CallTracker,
        history: list[Messages],
    ) -> bool:
        """Проверить, не является ли вызов дублем, и обработать зацикливание.

        Отслеживает повторные вызовы с идентичными аргументами.
        При дубле возвращает закешированный результат предыдущего вызова.

        Returns:
            True если вызов дублирован и его нужно пропустить.
        """
        call_key = call_tracker.make_key(tool_name, args)
        call_tracker.call_counts[call_key] = (
            call_tracker.call_counts.get(call_key, 0) + 1
        )

        if call_tracker.call_counts[call_key] >= MAX_IDENTICAL_TOOL_CALLS:
            logger.warning(
                "Зацикливание: %s вызван %d раз с одинаковыми "
                "аргументами, возвращаю закешированный результат",
                tool_name,
                call_tracker.call_counts[call_key],
            )
            # Возвращаем реальный результат предыдущего вызова
            cached = call_tracker.call_results.get(call_key, json.dumps(
                {"ok": True, "data": {}}, ensure_ascii=False,
            ))
            history.append(Messages(
                role=MessagesRole.FUNCTION,
                content=cached,
                name=tool_name,
            ))
            return True
        return False

    # ---- Выполнение инструмента ----

    async def execute(
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
            if tool_name in LOCAL_TOOL_NAMES:
                return await self._call_local_tool(tool_name, args, user_id)
            return await self._mcp_client.call_tool(tool_name, args)
        except Exception as e:
            logger.error("Ошибка %s: %s", tool_name, e, exc_info=True)
            return json.dumps(
                {"error": f"Ошибка вызова {tool_name}: {e}"},
                ensure_ascii=False,
            )

    # ---- Постобработка результата ----

    def postprocess_result(
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

    # ---- Маршрутизация локальных инструментов ----

    async def _call_local_tool(
        self, tool_name: str, args: dict, user_id: int,
    ) -> str:
        """Выполнить локальный инструмент (предпочтения).

        Рецепты обрабатываются через RecipeService (вне ToolExecutor).
        """
        if tool_name == "recipe_ingredients":
            # Этот путь используется только когда RecipeService не установлен.
            # В нормальном режиме GigaChatService перенаправляет на RecipeService.
            return json.dumps(
                {"ok": False, "error": "Кеш рецептов не настроен"},
                ensure_ascii=False,
            )

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

    # ---- Вспомогательные статические методы ----

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
        (точное вхождение), формируем уточнённый запрос.
        """
        if not user_prefs or not query:
            return query

        q_lower = query.strip().lower()

        # Точное совпадение
        pref = user_prefs.get(q_lower)

        if pref is None:
            return query

        # Если предпочтение уже содержит исходный запрос — используем как есть
        if q_lower in pref.lower():
            return pref

        # Иначе: "вареники" + "с картофелем и шкварками"
        return f"{query} {pref}"
