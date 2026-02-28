"""Pipeline-компоненты ToolExecutor: preprocessor и postprocessor."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.search_processor import SEARCH_LIMIT, SearchProcessor

if TYPE_CHECKING:
    from vkuswill_bot.services.cart_snapshot_store import CartSnapshotStore
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)

MAX_RESULT_PREVIEW_LENGTH = 500


# ---- Protocols (ADR-006) ----
@runtime_checkable
class ArgsPreprocessorProtocol(Protocol):
    """Контракт предобработки аргументов инструмента."""

    async def preprocess(
        self,
        *,
        tool_name: str,
        args: dict,
        user_prefs: dict[str, str],
    ) -> dict: ...


@runtime_checkable
class ResultPostprocessorProtocol(Protocol):
    """Контракт постобработки результата инструмента."""

    async def postprocess(
        self,
        *,
        tool_name: str,
        args: dict,
        result: str,
        user_prefs: dict[str, str],
        search_log: dict[str, set[int]],
        user_id: int | None,
    ) -> str: ...


@runtime_checkable
class InvokerProtocol(Protocol):
    """Контракт выполнения инструмента (local/MCP routing)."""

    async def execute(
        self,
        *,
        tool_name: str,
        args: dict,
        user_id: int,
        on_ingredient_found: (Callable[[], Awaitable[None]] | None) = None,
    ) -> str: ...


@runtime_checkable
class FreemiumHookProtocol(Protocol):
    """Контракт freemium-обработчика после успешной корзины."""

    async def apply(
        self,
        *,
        user_id: int,
        args: dict,
        result: str,
    ) -> str: ...


# ---- Implementations ----
class ToolArgsPreprocessor:
    """Предобработка аргументов tool-вызовов перед execute."""

    def __init__(
        self,
        *,
        search_processor: SearchProcessor,
        cart_processor: CartProcessor,
        apply_preferences_to_query: Callable[[str, dict[str, str]], str],
        find_unknown_xml_ids: Callable[[dict], Awaitable[list[int]]],
    ) -> None:
        self._search_processor = search_processor
        self._cart_processor = cart_processor
        self._apply_preferences_to_query = apply_preferences_to_query
        self._find_unknown_xml_ids = find_unknown_xml_ids

    async def preprocess(
        self,
        *,
        tool_name: str,
        args: dict,
        user_prefs: dict[str, str],
    ) -> dict:
        if tool_name == "vkusvill_cart_link_create":
            args = self._cart_processor.fix_cart_args(args)
            products = args.get("products")
            if isinstance(products, list):
                args["_requested_products"] = [
                    dict(item) for item in products if isinstance(item, dict)
                ]
            unknown = await self._find_unknown_xml_ids(args)
            if unknown:
                args["_unknown_xml_ids"] = unknown
            args = await self._cart_processor.fix_unit_quantities(args)

        if tool_name == "vkusvill_products_search":
            q = args.get("q", "")
            cleaned_q = self._search_processor.clean_search_query(q)
            if cleaned_q != q:
                logger.info("Очистка запроса: %r → %r", q, cleaned_q)
                args = {**args, "q": cleaned_q}

            if user_prefs:
                q = args.get("q", "")
                enhanced_q = self._apply_preferences_to_query(q, user_prefs)
                if enhanced_q != q:
                    logger.info("Подстановка предпочтения: %r → %r", q, enhanced_q)
                    args = {**args, "q": enhanced_q}

            if "limit" not in args:
                args = {**args, "limit": SEARCH_LIMIT}

        return args


class ToolResultPostprocessor:
    """Постобработка результатов tool-вызовов после execute."""

    def __init__(
        self,
        *,
        search_processor: SearchProcessor,
        cart_processor: CartProcessor,
        user_store: UserStore | None,
        cart_snapshot_store: CartSnapshotStore | None,
        parse_preferences: Callable[[str], dict[str, str]],
        is_cart_success: Callable[[str], bool],
        add_unknown_ids_hint: Callable[[str, list[int]], str],
        add_quantity_adjustments: Callable[[dict, str], str],
        save_cart_snapshot: Callable[[int, dict, str], Awaitable[None]],
        handle_cart_created_freemium: Callable[[int, dict, str], Awaitable[str]],
    ) -> None:
        self._search_processor = search_processor
        self._cart_processor = cart_processor
        self._user_store = user_store
        self._cart_snapshot_store = cart_snapshot_store
        self._parse_preferences = parse_preferences
        self._is_cart_success = is_cart_success
        self._add_unknown_ids_hint = add_unknown_ids_hint
        self._add_quantity_adjustments = add_quantity_adjustments
        self._save_cart_snapshot = save_cart_snapshot
        self._handle_cart_created_freemium = handle_cart_created_freemium

    async def postprocess(
        self,
        *,
        tool_name: str,
        args: dict,
        result: str,
        user_prefs: dict[str, str],
        search_log: dict[str, set[int]],
        user_id: int | None,
    ) -> str:
        if tool_name == "user_preferences_get":
            parsed = self._parse_preferences(result)
            if parsed:
                user_prefs.clear()
                user_prefs.update(parsed)
                logger.info("Загружены предпочтения: %s", dict(user_prefs.items()))
            return result

        if tool_name == "vkusvill_products_search":
            await self._search_processor.cache_prices(result)
            query = args.get("q", "")
            found_ids = self._search_processor.extract_xml_ids(result)
            if query and found_ids:
                search_log[query] = found_ids
            if self._user_store is not None and user_id is not None:
                try:
                    await self._user_store.log_event(
                        user_id,
                        "product_search",
                        {
                            "query": args.get("q", "")[:100],
                            "results_count": len(found_ids),
                            "had_results": bool(found_ids),
                        },
                    )
                except Exception:
                    logger.debug("Ошибка логирования product_search")
            return self._search_processor.trim_search_result(result)

        if tool_name == "recipe_search":
            try:
                parsed = json.loads(result)
                recipe_log = parsed.get("search_log", {}) if isinstance(parsed, dict) else {}
                if isinstance(recipe_log, dict):
                    for query, xml_ids in recipe_log.items():
                        if not query or not isinstance(xml_ids, list):
                            continue
                        valid_ids = {
                            int(x)
                            for x in xml_ids
                            if isinstance(x, int) or (isinstance(x, str) and x.isdigit())
                        }
                        if valid_ids:
                            search_log[query] = valid_ids
            except (json.JSONDecodeError, TypeError, ValueError):
                logger.debug("Не удалось синхронизировать search_log из recipe_search")
            return result

        if tool_name == "vkusvill_cart_link_create":
            unknown_ids = args.pop("_unknown_xml_ids", None)
            if unknown_ids and not self._is_cart_success(result):
                result = self._add_unknown_ids_hint(result, unknown_ids)

            result = await self._cart_processor.calc_total(args, result)
            result = await self._cart_processor.add_duplicate_warning(args, result)
            if search_log:
                result = await self._cart_processor.add_verification(args, result, search_log)
            result = self._add_quantity_adjustments(args, result)

            if self._cart_snapshot_store and user_id is not None:
                if self._is_cart_success(result):
                    await self._save_cart_snapshot(user_id, args, result)
                    result = await self._handle_cart_created_freemium(user_id, args, result)
                else:
                    logger.warning(
                        "Корзина не сохранена (ошибка API): %s",
                        result[:MAX_RESULT_PREVIEW_LENGTH],
                    )
            logger.info("Расчёт корзины: %s", result[:MAX_RESULT_PREVIEW_LENGTH])
            return result

        return result


class ToolInvoker:
    """Выполнение инструмента: local/MCP routing + freemium pre-check."""

    def __init__(
        self,
        *,
        mcp_client: Any,
        local_tool_names: frozenset[str],
        prefs_store: Any | None,
        nutrition_service: Any | None,
        recipe_search_service: Any | None,
        user_store: UserStore | None,
        get_previous_cart: Callable[[int], Awaitable[str]],
    ) -> None:
        self._mcp_client = mcp_client
        self._local_tool_names = local_tool_names
        self._prefs_store = prefs_store
        self._nutrition_service = nutrition_service
        self._recipe_search_service = recipe_search_service
        self._user_store = user_store
        self._get_previous_cart = get_previous_cart

    async def execute(
        self,
        *,
        tool_name: str,
        args: dict,
        user_id: int,
        on_ingredient_found: (Callable[[], Awaitable[None]] | None) = None,
    ) -> str:
        if tool_name == "vkusvill_cart_link_create" and self._user_store is not None:
            try:
                from vkuswill_bot.config import config as app_config

                limit_info = await self._user_store.check_cart_limit(
                    user_id,
                    default_limit=app_config.free_cart_limit,
                    trial_days=app_config.free_trial_days,
                )
                if not limit_info["allowed"]:
                    survey_done = limit_info.get("survey_completed", False)
                    try:
                        await self._user_store.log_event(
                            user_id,
                            "cart_limit_reached",
                            {
                                "carts_used": limit_info["carts_created"],
                                "cart_limit": limit_info["cart_limit"],
                                "survey_completed": survey_done,
                                "trial_active": bool(limit_info.get("trial_active")),
                            },
                        )
                    except Exception:
                        logger.debug("Ошибка логирования cart_limit_reached")

                    survey_text = (
                        "Опрос уже пройден. "
                        if survey_done
                        else (f"/survey — +{app_config.bonus_cart_limit} корзин сразу. ")
                    )
                    limit_message = (
                        "Лимит корзин после пробного периода исчерпан. "
                        f"{survey_text}"
                        "/invite — +"
                        f"{app_config.referral_cart_bonus} корзины за каждого друга "
                        "после его первой успешной корзины. "
                        "Оставьте оценку готовой корзины в кнопках под ответом бота — "
                        f"+{app_config.feedback_cart_bonus} корзины "
                        f"(не чаще 1 раза в {app_config.feedback_bonus_cooldown_days} дней)."
                    )

                    return json.dumps(
                        {
                            "error": "cart_limit_reached",
                            "message": limit_message,
                            "carts_created": limit_info["carts_created"],
                            "cart_limit": limit_info["cart_limit"],
                            "survey_completed": survey_done,
                            "trial_active": bool(limit_info.get("trial_active")),
                        },
                        ensure_ascii=False,
                    )
            except Exception as e:
                logger.warning("Ошибка проверки лимита корзин: %s", e)

        try:
            if tool_name in self._local_tool_names:
                return await self._call_local_tool(
                    tool_name,
                    args,
                    user_id,
                    on_ingredient_found=on_ingredient_found,
                )
            return await self._mcp_client.call_tool(tool_name, args)
        except Exception as e:
            logger.error("Ошибка %s: %s", tool_name, e, exc_info=True)
            return json.dumps(
                {"error": f"Ошибка вызова {tool_name}: {e}"},
                ensure_ascii=False,
            )

    async def _call_local_tool(
        self,
        tool_name: str,
        args: dict,
        user_id: int,
        on_ingredient_found: (Callable[[], Awaitable[None]] | None) = None,
    ) -> str:
        if tool_name == "recipe_ingredients":
            return json.dumps(
                {"ok": False, "error": "Кеш рецептов не настроен"},
                ensure_ascii=False,
            )

        if tool_name == "recipe_search":
            if self._recipe_search_service is None:
                return json.dumps(
                    {"ok": False, "error": "Сервис recipe_search не настроен"},
                    ensure_ascii=False,
                )
            ingredients = args.get("ingredients")
            if not isinstance(ingredients, list):
                return json.dumps(
                    {"ok": False, "error": "Не указан массив ingredients"},
                    ensure_ascii=False,
                )
            return await self._recipe_search_service.search_ingredients(
                ingredients,
                on_found=on_ingredient_found,
            )

        if tool_name == "nutrition_lookup":
            if self._nutrition_service is None:
                return json.dumps(
                    {"ok": False, "error": "Сервис КБЖУ не настроен"},
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
        if tool_name == "user_preferences_set":
            category = args.get("category", "")
            preference = args.get("preference", "")
            if not category or not preference:
                return json.dumps(
                    {"ok": False, "error": "Не указана категория или предпочтение"},
                    ensure_ascii=False,
                )
            return await self._prefs_store.set(user_id, category, preference)
        if tool_name == "user_preferences_delete":
            category = args.get("category", "")
            if not category:
                return json.dumps(
                    {"ok": False, "error": "Не указана категория"},
                    ensure_ascii=False,
                )
            return await self._prefs_store.delete(user_id, category)

        return json.dumps(
            {"ok": False, "error": f"Неизвестный локальный инструмент: {tool_name}"},
            ensure_ascii=False,
        )


class FreemiumCartHook:
    """Freemium-обработчик после успешного создания корзины."""

    def __init__(self, *, user_store: UserStore | None) -> None:
        self._user_store = user_store

    async def apply(
        self,
        *,
        user_id: int,
        args: dict,
        result: str,
    ) -> str:
        if self._user_store is None:
            return result
        try:
            total_sum = None
            try:
                parsed = json.loads(result)
                price_summary = parsed.get("data", {}).get("price_summary", {})
                if isinstance(price_summary, dict):
                    total_sum = price_summary.get("total")
            except (json.JSONDecodeError, TypeError, AttributeError):
                parsed = None

            from vkuswill_bot.config import config as _cfg

            cart_info = await self._user_store.increment_carts(
                user_id,
                trial_days=_cfg.free_trial_days,
            )
            carts = cart_info.get("carts_created", 0)
            limit = cart_info.get("cart_limit", _cfg.free_cart_limit)
            trial_active = bool(cart_info.get("trial_active"))
            trial_days_left = int(cart_info.get("trial_days_left", 0))
            trial_ends_at = cart_info.get("trial_ends_at")
            remaining = max(0, limit - carts) if not trial_active else None
            survey_done = cart_info.get("survey_completed", False)

            referral_bonus = await self._user_store.grant_referral_bonus_for_first_cart(
                user_id,
                _cfg.referral_cart_bonus,
            )
            if isinstance(referral_bonus, dict) and referral_bonus.get("granted"):
                await self._user_store.log_event(
                    int(referral_bonus["referrer_id"]),
                    "referral_bonus_granted",
                    {
                        "referred_user_id": user_id,
                        "bonus": int(referral_bonus["bonus"]),
                        "new_limit": int(referral_bonus["new_limit"]),
                    },
                )

            await self._user_store.log_event(
                user_id,
                "cart_created",
                {
                    "cart_number": carts,
                    "cart_limit": limit,
                    "trial_active": trial_active,
                    "items_count": len(args.get("products", [])),
                    "total_sum": total_sum,
                },
            )

            if trial_active:
                hint_text = (
                    "Пробный период активен: корзины пока без ограничений."
                    f" До конца trial примерно {trial_days_left} дн."
                )
            else:
                hint_text = f"Корзина {carts} из {limit}."
                if remaining == 0:
                    survey_hint = (
                        ""
                        if survey_done
                        else (
                            " Предложи пользователю команду /survey"
                            f" для получения +{_cfg.bonus_cart_limit} корзин."
                        )
                    )
                    hint_text += (
                        " Лимит корзин исчерпан."
                        f"{survey_hint}"
                        " Предложи /invite для бонуса за приглашение друзей"
                        f" (+{_cfg.referral_cart_bonus} за друга после первой корзины)."
                        " Также предложи оценить качество корзины кнопками в сообщении"
                        f" (+{_cfg.feedback_cart_bonus}, раз в"
                        f" {_cfg.feedback_bonus_cooldown_days} дней)."
                    )
                elif remaining <= 2:
                    hint_text += f" Осталось {remaining} корзины."

            if parsed is not None and isinstance(parsed.get("data"), dict):
                freemium_data: dict[str, Any] = {
                    "cart_number": carts,
                    "cart_limit": limit,
                    "remaining": remaining,
                    "trial_active": trial_active,
                    "trial_days_left": trial_days_left if trial_active else 0,
                    "hint": hint_text,
                }
                if trial_active and trial_ends_at is not None:
                    freemium_data["trial_ends_at"] = trial_ends_at.isoformat()
                parsed["data"]["freemium"] = freemium_data
                return json.dumps(parsed, ensure_ascii=False, indent=4)

            return result
        except Exception:
            logger.debug("Ошибка логирования cart_created")
            return result
