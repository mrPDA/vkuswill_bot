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
from vkuswill_bot.services.cart_snapshot_store import CartSnapshotStore
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.nutrition_service import NutritionService
from vkuswill_bot.services.preferences_store import PreferencesStore
from vkuswill_bot.services.search_processor import SearchProcessor

logger = logging.getLogger(__name__)

# Лимит длины результата инструмента для логирования
MAX_RESULT_LOG_LENGTH = 1000

# Лимит длины preview результата для логирования
MAX_RESULT_PREVIEW_LENGTH = 500

# Макс. повторных вызовов одного инструмента с одинаковыми аргументами
MAX_IDENTICAL_TOOL_CALLS = 2

# Макс. последовательных ошибок от одного инструмента (с любыми аргументами)
MAX_CONSECUTIVE_ERRORS_PER_TOOL = 2

# Имена локальных инструментов (для маршрутизации)
LOCAL_TOOL_NAMES = frozenset(
    {
        "user_preferences_get",
        "user_preferences_set",
        "user_preferences_delete",
        "recipe_ingredients",
        "get_previous_cart",
        "nutrition_lookup",
    }
)


class CallTracker:
    """Отслеживание повторных вызовов инструментов.

    Хранит счётчики вызовов и результаты для детекции зацикливания.
    """

    def __init__(self) -> None:
        self.call_counts: dict[str, int] = {}
        self.call_results: dict[str, str] = {}
        # Счётчик последовательных ошибок по имени инструмента
        self.error_counts: dict[str, int] = {}

    def make_key(self, tool_name: str, args: dict) -> str:
        """Создать ключ для отслеживания вызова."""
        return f"{tool_name}:{json.dumps(args, sort_keys=True)}"

    def record_result(self, tool_name: str, args: dict, result: str) -> None:
        """Записать результат вызова."""
        key = self.make_key(tool_name, args)
        self.call_results[key] = result
        # Отслеживаем последовательные ошибки по имени инструмента
        if '"error"' in result:
            self.error_counts[tool_name] = self.error_counts.get(tool_name, 0) + 1
        else:
            # Сбрасываем счётчик при успешном вызове
            self.error_counts[tool_name] = 0

    def is_tool_failing(self, tool_name: str) -> bool:
        """Проверить, превышен ли лимит ошибок для инструмента."""
        return self.error_counts.get(tool_name, 0) >= MAX_CONSECUTIVE_ERRORS_PER_TOOL


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
        cart_snapshot_store: CartSnapshotStore | None = None,
        nutrition_service: NutritionService | None = None,
    ) -> None:
        self._mcp_client = mcp_client
        self._search_processor = search_processor
        self._cart_processor = cart_processor
        self._prefs_store = preferences_store
        self._cart_snapshot_store = cart_snapshot_store
        self._nutrition_service = nutrition_service

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
        history: list[Messages],
        msg: object,
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

    async def preprocess_args(
        self,
        tool_name: str,
        args: dict,
        user_prefs: dict[str, str],
    ) -> dict:
        """Предобработать аргументы инструмента перед вызовом.

        - Корзина: исправляет дубли xml_id, добавляет q=1, округляет q для шт.
        - Поиск: очищает запрос от цифр/единиц, подставляет предпочтения, добавляет limit.
        """
        if tool_name == "vkusvill_cart_link_create":
            args = self._cart_processor.fix_cart_args(args)
            # Проверяем, что xml_id были получены через поиск (есть в price_cache)
            unknown = await self._find_unknown_xml_ids(args)
            if unknown:
                args["_unknown_xml_ids"] = unknown
            args = await self._cart_processor.fix_unit_quantities(args)

        if tool_name == "vkusvill_products_search":
            # Очистка запроса от чисел и единиц измерения
            q = args.get("q", "")
            cleaned_q = self._search_processor.clean_search_query(q)
            if cleaned_q != q:
                logger.info("Очистка запроса: %r → %r", q, cleaned_q)
                args = {**args, "q": cleaned_q}

            # Подстановка предпочтений
            if user_prefs:
                q = args.get("q", "")
                enhanced_q = self._apply_preferences_to_query(q, user_prefs)
                if enhanced_q != q:
                    logger.info("Подстановка предпочтения: %r → %r", q, enhanced_q)
                    args = {**args, "q": enhanced_q}

            # Ограничение результатов поиска
            from vkuswill_bot.services.search_processor import SEARCH_LIMIT

            if "limit" not in args:
                args = {**args, "limit": SEARCH_LIMIT}

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

        Отслеживает:
        - Повторные вызовы с идентичными аргументами (дубли).
        - Инструменты, стабильно возвращающие ошибки (разные аргументы).

        Returns:
            True если вызов нужно пропустить.
        """
        # ── Проверка: инструмент стабильно возвращает ошибки ──
        if call_tracker.is_tool_failing(tool_name):
            logger.warning(
                "Инструмент %s вернул %d последовательных ошибок, "
                "пропускаю вызов и уведомляю модель",
                tool_name,
                call_tracker.error_counts[tool_name],
            )
            error_msg = json.dumps(
                {
                    "error": f"Инструмент {tool_name} временно недоступен. "
                    "Не пытайся вызывать его снова. Продолжи без него.",
                },
                ensure_ascii=False,
            )
            history.append(
                Messages(
                    role=MessagesRole.FUNCTION,
                    content=error_msg,
                    name=tool_name,
                )
            )
            return True

        # ── Проверка: идентичный вызов (те же аргументы) ──
        call_key = call_tracker.make_key(tool_name, args)
        call_tracker.call_counts[call_key] = call_tracker.call_counts.get(call_key, 0) + 1

        if call_tracker.call_counts[call_key] >= MAX_IDENTICAL_TOOL_CALLS:
            logger.warning(
                "Зацикливание: %s вызван %d раз с одинаковыми "
                "аргументами, возвращаю закешированный результат",
                tool_name,
                call_tracker.call_counts[call_key],
            )
            # Возвращаем реальный результат предыдущего вызова
            cached = call_tracker.call_results.get(
                call_key,
                json.dumps(
                    {"ok": True, "data": {}},
                    ensure_ascii=False,
                ),
            )
            history.append(
                Messages(
                    role=MessagesRole.FUNCTION,
                    content=cached,
                    name=tool_name,
                )
            )
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

    async def postprocess_result(
        self,
        tool_name: str,
        args: dict,
        result: str,
        user_prefs: dict[str, str],
        search_log: dict[str, set[int]],
        user_id: int | None = None,
    ) -> str:
        """Постобработать результат вызова инструмента.

        - Парсит предпочтения из user_preferences_get.
        - Кеширует цены и обрезает результат поиска.
        - Рассчитывает стоимость корзины, верифицирует и сохраняет снимок.

        Мутирует user_prefs и search_log in-place.

        Args:
            user_id: ID пользователя Telegram (для снимка корзины).
                     Опционален для обратной совместимости с тестами.

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
                    dict(user_prefs.items()),
                )

        elif tool_name == "vkusvill_products_search":
            await self._search_processor.cache_prices(result)
            query = args.get("q", "")
            found_ids = self._search_processor.extract_xml_ids(result)
            if query and found_ids:
                search_log[query] = found_ids
            result = self._search_processor.trim_search_result(result)

        elif tool_name == "vkusvill_cart_link_create":
            # Если были неизвестные xml_id — добавляем подсказку в ошибку
            unknown_ids = args.pop("_unknown_xml_ids", None)
            if unknown_ids and not self._is_cart_success(result):
                result = self._add_unknown_ids_hint(result, unknown_ids)

            result = await self._cart_processor.calc_total(args, result)
            result = await self._cart_processor.add_duplicate_warning(
                args,
                result,
            )
            if search_log:
                result = await self._cart_processor.add_verification(
                    args,
                    result,
                    search_log,
                )
            # Добавляем информацию о скорректированных количествах
            result = self._add_quantity_adjustments(args, result)
            # Сохраняем снимок корзины ТОЛЬКО при успехе (ok: true),
            # чтобы get_previous_cart не возвращал невалидные xml_id
            if self._cart_snapshot_store and user_id is not None:
                if self._is_cart_success(result):
                    await self._save_cart_snapshot(user_id, args, result)
                else:
                    logger.warning(
                        "Корзина не сохранена (ошибка API): %s",
                        result[:MAX_RESULT_PREVIEW_LENGTH],
                    )
            logger.info(
                "Расчёт корзины: %s",
                result[:MAX_RESULT_PREVIEW_LENGTH],
            )

        return result

    async def _save_cart_snapshot(
        self,
        user_id: int,
        args: dict,
        result: str,
    ) -> None:
        """Извлечь данные из результата корзины и сохранить снимок."""
        products = args.get("products", [])
        link = ""
        total: float | None = None
        try:
            result_data = json.loads(result)
            data = result_data.get("data", {})
            if isinstance(data, dict):
                link = data.get("link", "")
                summary = data.get("price_summary", {})
                if isinstance(summary, dict):
                    total = summary.get("total")
        except (json.JSONDecodeError, TypeError):
            pass
        await self._cart_snapshot_store.save(  # type: ignore[union-attr]
            user_id=user_id,
            products=products,
            link=link,
            total=total,
        )

    @staticmethod
    def _add_unknown_ids_hint(result: str, unknown_ids: list[int]) -> str:
        """Добавить подсказку о невалидных xml_id в результат ошибки корзины.

        Когда GigaChat выдумывает xml_id (не из результатов поиска),
        API возвращает ошибку. Добавляем явную инструкцию: искать через поиск.
        """
        try:
            result_data = json.loads(result)
            result_data["_fix_instruction"] = (
                f"Ошибка: xml_id {unknown_ids} не существуют. "
                "Ты НЕ МОЖЕШЬ знать xml_id товаров — их можно получить "
                "ТОЛЬКО через vkusvill_products_search. "
                "Найди каждый ингредиент через поиск, затем используй "
                "xml_id из результатов для создания корзины."
            )
            return json.dumps(result_data, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            return result

    async def _find_unknown_xml_ids(self, args: dict) -> list[int]:
        """Найти xml_id в аргументах корзины, которых нет в price_cache.

        Если xml_id нет в кеше — значит товар не был найден через поиск,
        и GigaChat выдумал его. Такие товары вызовут ошибку API.
        """
        products = args.get("products", [])
        unknown: list[int] = []
        for item in products:
            if not isinstance(item, dict):
                continue
            xml_id = item.get("xml_id")
            if xml_id is not None:
                cached = await self._cart_processor._price_cache.get(xml_id)
                if cached is None:
                    unknown.append(xml_id)
        return unknown

    @staticmethod
    def _is_cart_success(result: str) -> bool:
        """Проверить, успешно ли создана корзина (ok: true в результате).

        Не сохраняем снимок при ошибке (невалидные xml_id и пр.),
        чтобы get_previous_cart не возвращал мусорные данные.
        """
        try:
            data = json.loads(result)
            return bool(data.get("ok"))
        except (json.JSONDecodeError, TypeError):
            return False

    @staticmethod
    def _add_quantity_adjustments(args: dict, result: str) -> str:
        """Добавить информацию о скорректированных количествах в результат корзины.

        Если preprocess_args скорректировал количества штучных товаров
        (дробное → целое), эта информация добавляется в результат,
        чтобы GigaChat не пытался «исправить» корзину повторным вызовом.
        """
        adjustments = args.pop("_quantity_adjustments", None)
        if not adjustments:
            return result

        try:
            result_data = json.loads(result)
            data = result_data.get("data")
            if isinstance(data, dict):
                data["quantity_adjustments"] = {
                    "note": (
                        "Количества некоторых товаров были автоматически скорректированы, "
                        "потому что эти товары продаются поштучно и не могут иметь "
                        "дробное количество. НЕ пытайся пересоздать корзину с дробными "
                        "количествами — они будут снова округлены. Корзина уже корректна."
                    ),
                    "items": adjustments,
                }
                return json.dumps(result_data, ensure_ascii=False, indent=4)
        except (json.JSONDecodeError, TypeError):
            pass
        return result

    # ---- Маршрутизация локальных инструментов ----

    async def _call_local_tool(
        self,
        tool_name: str,
        args: dict,
        user_id: int,
    ) -> str:
        """Выполнить локальный инструмент (предпочтения, корзина).

        Рецепты обрабатываются через RecipeService (вне ToolExecutor).
        """
        if tool_name == "recipe_ingredients":
            # Этот путь используется только когда RecipeService не установлен.
            # В нормальном режиме GigaChatService перенаправляет на RecipeService.
            return json.dumps(
                {"ok": False, "error": "Кеш рецептов не настроен"},
                ensure_ascii=False,
            )

        if tool_name == "nutrition_lookup":
            if self._nutrition_service is None:
                return json.dumps(
                    {"ok": False, "error": "Сервис КБЖУ не настроен (нет USDA API key)"},
                    ensure_ascii=False,
                )
            return await self._nutrition_service.lookup(args)

        if tool_name == "get_previous_cart":
            return await self._get_previous_cart(user_id)

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

    async def _get_previous_cart(self, user_id: int) -> str:
        """Получить содержимое предыдущей корзины пользователя.

        Возвращает JSON с products, link, total — или сообщение
        что корзины нет.
        """
        if self._cart_snapshot_store is None:
            return json.dumps(
                {"ok": False, "message": "Предыдущая корзина недоступна"},
                ensure_ascii=False,
            )
        snapshot = await self._cart_snapshot_store.get(user_id)
        if snapshot is None:
            return json.dumps(
                {"ok": True, "message": "У пользователя нет предыдущей корзины"},
                ensure_ascii=False,
            )
        # Обогащаем снимок именами товаров из кеша цен
        products = snapshot.get("products", [])
        enriched_products = []
        for item in products:
            xml_id = item.get("xml_id")
            q = item.get("q", 1)
            cached = await self._cart_processor._price_cache.get(xml_id)
            product_info: dict = {"xml_id": xml_id, "q": q}
            if cached:
                product_info["name"] = cached.name
                product_info["price"] = cached.price
                product_info["unit"] = cached.unit
            enriched_products.append(product_info)
        result: dict = {
            "ok": True,
            "products": enriched_products,
            "link": snapshot.get("link", ""),
            "total": snapshot.get("total"),
            "created_at": snapshot.get("created_at", ""),
        }
        return json.dumps(result, ensure_ascii=False)

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
