"""Маршрутизация и выполнение вызовов инструментов.

Отвечает за:
- Маршрутизацию: local tools vs MCP tools
- Выполнение с обработкой ошибок
- Детекцию зацикливания (дубли вызовов)
- Пре/постпроцессинг аргументов и результатов
- Парсинг предпочтений из результатов
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vkuswill_bot.services.recipe_search import RecipeSearchService
    from vkuswill_bot.services.user_store import UserStore

from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.cart_snapshot_store import CartSnapshotStore
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.nutrition_service import NutritionService
from vkuswill_bot.services.preferences_parser import parse_preferences
from vkuswill_bot.services.preferences_store import PreferencesStore
from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.tool_executor_pipeline import (
    FreemiumCartHook,
    ToolArgsPreprocessor,
    ToolInvoker,
    ToolResultPostprocessor,
)

logger = logging.getLogger(__name__)

# Лимит длины результата инструмента для логирования
MAX_RESULT_LOG_LENGTH = 1000

# Лимит длины preview результата для логирования
MAX_RESULT_PREVIEW_LENGTH = 500

# Имена локальных инструментов (для маршрутизации)
LOCAL_TOOL_NAMES = frozenset(
    {
        "user_preferences_get",
        "user_preferences_set",
        "user_preferences_delete",
        "recipe_ingredients",
        "recipe_search",
        "get_previous_cart",
        "nutrition_lookup",
    }
)


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
        recipe_search_service: RecipeSearchService | None = None,
        user_store: UserStore | None = None,
    ) -> None:
        self._mcp_client = mcp_client
        self._search_processor = search_processor
        self._cart_processor = cart_processor
        self._prefs_store = preferences_store
        self._cart_snapshot_store = cart_snapshot_store
        self._nutrition_service = nutrition_service
        self._recipe_search_service = recipe_search_service
        self._user_store = user_store
        self._args_preprocessor = ToolArgsPreprocessor(
            search_processor=self._search_processor,
            cart_processor=self._cart_processor,
            apply_preferences_to_query=self._apply_preferences_to_query,
            find_unknown_xml_ids=self._find_unknown_xml_ids,
        )
        self._result_postprocessor = ToolResultPostprocessor(
            search_processor=self._search_processor,
            cart_processor=self._cart_processor,
            user_store=self._user_store,
            cart_snapshot_store=self._cart_snapshot_store,
            parse_preferences=self._parse_preferences,
            is_cart_success=self._is_cart_success,
            add_unknown_ids_hint=self._add_unknown_ids_hint,
            add_quantity_adjustments=self._add_quantity_adjustments,
            save_cart_snapshot=self._save_cart_snapshot,
            handle_cart_created_freemium=self._handle_cart_created_freemium,
        )
        self._tool_invoker = ToolInvoker(
            mcp_client=self._mcp_client,
            local_tool_names=LOCAL_TOOL_NAMES,
            prefs_store=self._prefs_store,
            nutrition_service=self._nutrition_service,
            recipe_search_service=self._recipe_search_service,
            user_store=self._user_store,
            get_previous_cart=self._get_previous_cart,
        )
        self._freemium_cart_hook = FreemiumCartHook(user_store=self._user_store)

    # ---- Публичные свойства для доступа к процессорам (DI) ----

    @property
    def search_processor(self) -> SearchProcessor:
        """SearchProcessor (read-only доступ для DI)."""
        return self._search_processor

    @property
    def cart_processor(self) -> CartProcessor:
        """CartProcessor (read-only доступ для DI)."""
        return self._cart_processor

    @property
    def has_recipe_search(self) -> bool:
        """Доступен ли локальный recipe_search."""
        return self._recipe_search_service is not None

    async def get_last_cart_snapshot(self, user_id: int) -> dict[str, Any] | None:
        """Получить последний снимок корзины пользователя."""
        if self._cart_snapshot_store is None:
            return None
        return await self._cart_snapshot_store.get(user_id)

    # ---- Парсинг аргументов ----

    @staticmethod
    def parse_arguments(raw_args: str | dict | None) -> dict:
        """Распарсить аргументы вызова инструмента от LLM.

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

        .. deprecated:: 0.18
           Проксирует в ``_args_preprocessor.preprocess()``.
           Использовать делегат напрямую. Удаление в Phase 4 (ADR-006).
        """
        return await self._args_preprocessor.preprocess(
            tool_name=tool_name,
            args=args,
            user_prefs=user_prefs,
        )

    # ---- Выполнение инструмента ----

    async def execute(
        self,
        tool_name: str,
        args: dict,
        user_id: int,
        on_ingredient_found: (Callable[[], Coroutine[Any, Any, None]] | None) = None,
    ) -> str:
        """Выполнить вызов инструмента (локальный или MCP) с обработкой ошибок.

        Returns:
            Строковый результат вызова (JSON).

        .. deprecated:: 0.18
           Проксирует в ``_tool_invoker.execute()``.
           Использовать делегат напрямую. Удаление в Phase 4 (ADR-006).
        """
        return await self._tool_invoker.execute(
            tool_name=tool_name,
            args=args,
            user_id=user_id,
            on_ingredient_found=on_ingredient_found,
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

        .. deprecated:: 0.18
           Проксирует в ``_result_postprocessor.postprocess()``.
           Использовать делегат напрямую. Удаление в Phase 4 (ADR-006).
        """
        return await self._result_postprocessor.postprocess(
            tool_name=tool_name,
            args=args,
            result=result,
            user_prefs=user_prefs,
            search_log=search_log,
            user_id=user_id,
        )

    async def _handle_cart_created_freemium(
        self,
        user_id: int,
        args: dict,
        result: str,
    ) -> str:
        """Freemium-логика после успешного создания корзины.

        Инкрементирует счётчик, логирует событие cart_created,
        добавляет хинт с остатком корзин для LLM.

        ВАЖНО: хинт встраивается ВНУТРЬ JSON-структуры (поле ``data.freemium``),
        а не дописывается как plain-text — иначе LLM отвергнет невалидный
        JSON с ошибкой 422 ``invalid function result json string``.

        Returns:
            Обновлённый result с хинтом (или оригинальный при ошибке).
        """
        return await self._freemium_cart_hook.apply(
            user_id=user_id,
            args=args,
            result=result,
        )

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

        Когда LLM выдумывает xml_id (не из результатов поиска),
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
        и LLM выдумала его. Такие товары вызовут ошибку API.
        """
        products = args.get("products", [])
        unknown: list[int] = []
        for item in products:
            if not isinstance(item, dict):
                continue
            xml_id = item.get("xml_id")
            if xml_id is not None:
                cached = await self._cart_processor.price_cache.get(xml_id)
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
        чтобы LLM не пыталась «исправить» корзину повторным вызовом.
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
        on_ingredient_found: (Callable[[], Coroutine[Any, Any, None]] | None) = None,
    ) -> str:
        """Выполнить локальный инструмент (предпочтения, корзина).

        Рецепты обрабатываются через RecipeService (вне ToolExecutor).

        .. deprecated:: 0.18
           Проксирует в ``_tool_invoker._call_local_tool()``.
           Удаление в Phase 4 (ADR-006).
        """
        return await self._tool_invoker._call_local_tool(
            tool_name=tool_name,
            args=args,
            user_id=user_id,
            on_ingredient_found=on_ingredient_found,
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
            cached = await self._cart_processor.price_cache.get(xml_id)
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

    _parse_preferences = staticmethod(parse_preferences)

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
