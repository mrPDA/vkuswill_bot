"""Сервис GigaChat с поддержкой function calling через MCP-инструменты."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from gigachat import GigaChat
from gigachat.context import session_id_cvar
from gigachat.models import Chat, ChatCompletion, Messages, MessagesRole

from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.dialog_manager import MAX_CONVERSATIONS, DialogManager
from vkuswill_bot.services.langfuse_tracing import (
    LangfuseService,
    _messages_to_langfuse,
)
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.preferences_store import PreferencesStore
from vkuswill_bot.services.prompts import (
    CART_PREVIOUS_TOOL,
    ERROR_GIGACHAT,
    ERROR_TOO_MANY_STEPS,
    LOCAL_TOOLS,
    NUTRITION_TOOL,
    RECIPE_TOOL,
)
from vkuswill_bot.services.recipe_service import RecipeService
from vkuswill_bot.services.recipe_store import RecipeStore
from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.tool_executor import CallTracker, ToolExecutor

if TYPE_CHECKING:
    from vkuswill_bot.services.redis_dialog_manager import RedisDialogManager

logger = logging.getLogger(__name__)

# Лимит длины входящего сообщения пользователя (символы)
MAX_USER_MESSAGE_LENGTH = 4096

# Лимит длины результата инструмента для логирования
MAX_RESULT_LOG_LENGTH = 1000

# Макс. параллельных запросов к GigaChat API (семафор)
DEFAULT_GIGACHAT_MAX_CONCURRENT = 15

# Макс. количество retry при 429 от GigaChat
GIGACHAT_MAX_RETRIES = 5

# Лимит количества поисковых запросов в search_log на пользователя
MAX_SEARCH_LOG_QUERIES = 100

# Лимит количества пользователей в _search_logs (LRU-вытеснение)
MAX_SEARCH_LOGS = 1000

# Тарифы GigaChat API (₽ за 1 токен)
# Источник: https://developers.sber.ru/docs/ru/gigachat/tariffs/legal-tariffs
# Обновлено: февраль 2026
GIGACHAT_TOKEN_PRICES: dict[str, float] = {
    "GigaChat-2-Max": 650 / 1_000_000,  # 650 ₽ / 1M токенов
    "GigaChat-2-Pro": 500 / 1_000_000,  # 500 ₽ / 1M токенов
    "GigaChat-2-Lite": 65 / 1_000_000,  # 65 ₽ / 1M токенов
    "GigaChat": 65 / 1_000_000,  # GigaChat (без версии) = Lite
}


class GigaChatService:
    """Сервис для взаимодействия с GigaChat и MCP-инструментами.

    Управляет историей диалогов пользователей и реализует цикл
    function calling: GigaChat решает, какой инструмент вызвать,
    бот выполняет вызов через MCP, результат возвращается в GigaChat.
    """

    # Описания инструментов (определены в prompts.py)
    _RECIPE_TOOL = RECIPE_TOOL
    _LOCAL_TOOLS = LOCAL_TOOLS
    _CART_PREVIOUS_TOOL = CART_PREVIOUS_TOOL
    _NUTRITION_TOOL = NUTRITION_TOOL

    def __init__(
        self,
        credentials: str,
        model: str,
        scope: str,
        mcp_client: VkusvillMCPClient,
        preferences_store: PreferencesStore | None = None,
        recipe_store: RecipeStore | None = None,
        max_tool_calls: int = 20,
        max_history: int = 50,
        dialog_manager: DialogManager | RedisDialogManager | None = None,
        tool_executor: ToolExecutor | None = None,
        recipe_service: RecipeService | None = None,
        gigachat_max_concurrent: int = DEFAULT_GIGACHAT_MAX_CONCURRENT,
        langfuse_service: LangfuseService | None = None,
        ca_bundle_file: str | None = None,
    ) -> None:
        # SSL-верификация с сертификатами НУЦ Минцифры (ca_bundle_file).
        # Если ca_bundle_file указан и файл существует — verify=True.
        # Иначе — fallback на verify=False с предупреждением.
        import pathlib

        verify_ssl = False
        effective_ca_bundle: str | None = None
        if ca_bundle_file:
            ca_path = pathlib.Path(ca_bundle_file)
            if ca_path.exists():
                verify_ssl = True
                effective_ca_bundle = str(ca_path)
                logger.info("GigaChat SSL: verify=True, ca_bundle=%s", ca_path)
            else:
                logger.warning(
                    "GigaChat SSL: ca_bundle не найден (%s), verify=False (НЕБЕЗОПАСНО!)",
                    ca_bundle_file,
                )
        else:
            logger.warning("GigaChat SSL: ca_bundle не указан, verify=False (НЕБЕЗОПАСНО!)")

        gigachat_kwargs: dict[str, Any] = {
            "credentials": credentials,
            "model": model,
            "scope": scope,
            "verify_ssl_certs": verify_ssl,
            "timeout": 60,
        }
        if effective_ca_bundle:
            gigachat_kwargs["ca_bundle_file"] = effective_ca_bundle

        self._client = GigaChat(**gigachat_kwargs)
        self._model_name = model
        self._langfuse = langfuse_service or LangfuseService(enabled=False)
        self._mcp_client = mcp_client
        self._prefs_store = preferences_store
        self._recipe_store = recipe_store
        self._max_tool_calls = max_tool_calls
        self._max_history = max_history

        # Семафор для ограничения параллельных запросов к GigaChat API
        self._api_semaphore = asyncio.Semaphore(gigachat_max_concurrent)

        self._dialog_manager = dialog_manager or DialogManager(
            max_conversations=MAX_CONVERSATIONS,
            max_history=max_history,
        )
        # обратная совместимость
        self._conversations = getattr(self._dialog_manager, "conversations", {})

        self._functions: list[dict] | None = None
        self._search_logs: OrderedDict[int, dict[str, set[int]]] = OrderedDict()

        # Процессоры: извлекаем из tool_executor (если передан через DI),
        # иначе создаём новые (fallback для тестов).
        if tool_executor is not None:
            self._search_processor = tool_executor.search_processor
            self._cart_processor = tool_executor.cart_processor
        else:
            self._search_processor = SearchProcessor()
            self._cart_processor = CartProcessor(self._search_processor.price_cache)

        self._tool_executor = tool_executor or ToolExecutor(
            mcp_client=mcp_client,
            search_processor=self._search_processor,
            cart_processor=self._cart_processor,
            preferences_store=preferences_store,
        )

        if recipe_service is not None:
            self._recipe_service = recipe_service
        elif recipe_store is not None:
            self._recipe_service = RecipeService(
                gigachat_client=self._client,
                recipe_store=recipe_store,
            )
        else:
            self._recipe_service = None

    async def _get_functions(self) -> list[dict]:
        """Получить описания функций для GigaChat (MCP + локальные)."""
        if self._functions is not None:
            return self._functions
        tools = await self._mcp_client.get_tools()
        self._functions = []
        for tool in tools:
            params = tool["parameters"]
            if tool["name"] == "vkusvill_cart_link_create":
                params = CartProcessor.enhance_cart_schema(params)
            self._functions.append(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": params,
                }
            )
        if self._prefs_store is not None:
            self._functions.extend(self._LOCAL_TOOLS)
        if self._recipe_store is not None:
            self._functions.append(self._RECIPE_TOOL)
        # Инструмент получения предыдущей корзины (всегда доступен)
        self._functions.append(self._CART_PREVIOUS_TOOL)
        # КБЖУ через Open Food Facts (всегда доступен, без API key)
        self._functions.append(self._NUTRITION_TOOL)
        logger.info("Функции для GigaChat: %s", [f["name"] for f in self._functions])
        return self._functions

    async def reset_conversation(self, user_id: int) -> None:
        """Сбросить историю диалога и search_log (async, работает с любым бэкендом)."""
        await self._dialog_manager.areset(user_id)
        self._search_logs.pop(user_id, None)

    # ---- Session-level search_log ----

    def _get_search_log(self, user_id: int) -> dict[str, set[int]]:
        """Получить search_log для пользователя (накопленный за сессию).

        search_log хранит маппинг: поисковый запрос → множество xml_id,
        найденных по этому запросу. Используется верификацией корзины.
        При доступе — перемещаем в конец LRU.
        """
        if user_id in self._search_logs:
            self._search_logs.move_to_end(user_id)
        return self._search_logs.get(user_id, {})

    def _save_search_log(
        self,
        user_id: int,
        search_log: dict[str, set[int]],
    ) -> None:
        """Сохранить search_log для пользователя с лимитом размера.

        Два уровня ограничений:
        1. Внутри одного user_id: не более MAX_SEARCH_LOG_QUERIES запросов.
        2. По количеству user_id: LRU-вытеснение при MAX_SEARCH_LOGS.
        """
        if len(search_log) > MAX_SEARCH_LOG_QUERIES:
            keys = list(search_log.keys())
            for key in keys[: len(keys) - MAX_SEARCH_LOG_QUERIES]:
                del search_log[key]
        self._search_logs[user_id] = search_log
        self._search_logs.move_to_end(user_id)
        # LRU-вытеснение: удаляем самый давний user_id
        while len(self._search_logs) > MAX_SEARCH_LOGS:
            evicted_uid, _ = self._search_logs.popitem(last=False)
            logger.debug("search_logs LRU: вытеснен user %d", evicted_uid)

    async def _handle_recipe_ingredients(self, args: dict) -> str:
        if self._recipe_service is not None:
            return await self._recipe_service.get_ingredients(args)
        return json.dumps({"ok": False, "error": "Кеш рецептов не настроен"}, ensure_ascii=False)

    async def close(self) -> None:
        """Закрыть клиент GigaChat."""
        try:
            await asyncio.to_thread(self._client.close)
        except Exception as e:
            logger.debug("Ошибка при закрытии GigaChat клиента: %s", e)

    async def process_message(self, user_id: int, text: str) -> str:
        """Обработать сообщение пользователя (цикл function calling)."""
        if len(text) > MAX_USER_MESSAGE_LENGTH:
            logger.warning(
                "Сообщение пользователя %d обрезано: %d -> %d символов",
                user_id,
                len(text),
                MAX_USER_MESSAGE_LENGTH,
            )
            text = text[:MAX_USER_MESSAGE_LENGTH]
        async with self._dialog_manager.get_lock(user_id):
            return await self._process_message_locked(user_id, text)

    async def _call_gigachat(
        self,
        history: list[Messages],
        functions: list[dict],
    ) -> ChatCompletion:
        """Вызвать GigaChat API с семафором и retry при 429.

        Ограничивает параллельные запросы через asyncio.Semaphore.
        При получении rate limit (429) — retry с exponential backoff.

        Returns:
            Ответ GigaChat (ChatCompletion-подобный объект).

        Raises:
            Exception: Если все retry исчерпаны или ошибка не связана с rate limit.
        """
        return await self._call_gigachat_with_fc(history, functions, function_call="auto")

    async def _call_gigachat_with_fc(
        self,
        history: list[Messages],
        functions: list[dict],
        *,
        function_call: str = "auto",
    ) -> ChatCompletion:
        """Вызвать GigaChat API с указанным режимом function_call.

        Args:
            function_call: "auto" — модель решает сама, "none" — только текст.

        Returns:
            Ответ GigaChat (ChatCompletion-подобный объект).

        Raises:
            Exception: Если все retry исчерпаны или ошибка не связана с rate limit.
        """
        chat_kwargs: dict[str, Any] = {
            "messages": history,
            "function_call": function_call,
        }
        # Если function_call="none", не передаём functions
        # (иначе некоторые модели всё равно пытаются вызвать)
        if function_call != "none":
            chat_kwargs["functions"] = functions

        for attempt in range(GIGACHAT_MAX_RETRIES):
            try:
                async with self._api_semaphore:
                    return await asyncio.to_thread(
                        self._client.chat,
                        Chat(**chat_kwargs),
                    )
            except Exception as e:
                if attempt < GIGACHAT_MAX_RETRIES - 1 and self._is_rate_limit_error(e):
                    delay = 2**attempt  # 1s, 2s
                    logger.warning(
                        "GigaChat rate limit, retry %d/%d через %ds: %s",
                        attempt + 1,
                        GIGACHAT_MAX_RETRIES,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

        # Unreachable, но для безопасности типов
        msg = "Все retry исчерпаны"
        raise RuntimeError(msg)  # pragma: no cover

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        """Определить, является ли ошибка rate limit (429).

        Проверяет тип исключения (если SDK пробрасывает httpx),
        иначе fallback на строковую эвристику.
        """
        # Если SDK пробрасывает httpx.HTTPStatusError
        if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
            return exc.response.status_code == 429

        # Fallback: строковая эвристика
        exc_str = str(exc).lower()
        return "429" in exc_str or "rate limit" in exc_str or "too many" in exc_str

    async def _process_message_locked(self, user_id: int, text: str) -> str:
        """Цикл function calling (под per-user lock)."""
        # ── X-Session-ID: кеширование prefix-токенов между вызовами ──
        # Все API-вызовы в рамках одного сообщения используют общий
        # session_id → system prompt + tools кешируются и НЕ тарифицируются.
        session_id_cvar.set(f"user-{user_id}")

        dm = self._dialog_manager
        history = await dm.aget_history(user_id)
        history.append(Messages(role=MessagesRole.USER, content=text))
        functions = await self._get_functions()
        call_tracker = CallTracker()
        # Загружаем накопленный search_log из сессии (не пустой dict!)
        search_log = self._get_search_log(user_id)
        user_prefs: dict[str, str] = {}
        te = self._tool_executor

        # ── Langfuse: создаём trace для всего сообщения ──
        trace = self._langfuse.trace(
            name="chat",
            user_id=str(user_id),
            session_id=str(user_id),
            input=text,
            tags=["gigachat", "telegram"],
        )

        real_calls = 0
        total_steps = 0
        max_total_steps = self._max_tool_calls * 2
        generation_idx = 0
        consecutive_skips = 0  # подряд пропущенных дубликатов
        max_consecutive_skips = 3  # порог для принудительного текстового ответа
        cart_hint_injected = False  # флаг: подсказка о корзине уже вставлена
        cart_created = False  # флаг: корзина успешно создана → следующий шаг текстовый

        while real_calls < self._max_tool_calls and total_steps < max_total_steps:
            total_steps += 1

            # ── Если подряд слишком много дубликатов — направляем к корзине ──
            if consecutive_skips >= max_consecutive_skips and not cart_hint_injected:
                # Первое срабатывание: вставляем подсказку, даём ещё один шанс.
                # Сброс до (порог − 1), чтобы уже следующий дубликат
                # поднял счётчик обратно до порога и включил force_text.
                cart_hint_injected = True
                consecutive_skips = max_consecutive_skips - 1
                history.append(
                    Messages(
                        role=MessagesRole.USER,
                        content=(
                            "[Системная подсказка] Все товары уже найдены. "
                            "Не повторяй поиск. "
                            "Создай корзину через vkusvill_cart_link_create "
                            "с найденными xml_id и покажи результат пользователю."
                        ),
                    )
                )
                logger.info("User %d: вставлена подсказка о создании корзины", user_id)

            force_text = consecutive_skips >= max_consecutive_skips and cart_hint_injected

            # После успешного создания корзины — принудительный текстовый ответ,
            # чтобы модель не продолжала цикл с товарами из предыдущих запросов.
            if cart_created:
                force_text = True

            if force_text:
                logger.warning(
                    "User %d: принудительный текстовый ответ (cart_created=%s, дубликатов=%d)",
                    user_id,
                    cart_created,
                    consecutive_skips,
                )

            fc_mode = "none" if force_text else "auto"
            logger.info(
                "Шаг %d для user %d (вызовов: %d, дубликатов подряд: %d, fc=%s)",
                total_steps,
                user_id,
                real_calls,
                consecutive_skips,
                fc_mode,
            )

            # ── Langfuse: generation для каждого вызова LLM ──
            generation_idx += 1
            gen = trace.generation(
                name=f"gigachat-{generation_idx}",
                model=self._model_name,
                input=_messages_to_langfuse(history),
                model_parameters={"function_call": fc_mode},
                metadata={
                    "step": total_steps,
                    "real_calls": real_calls,
                    "consecutive_skips": consecutive_skips,
                    "force_text": force_text,
                },
            )

            try:
                response = await self._call_gigachat_with_fc(
                    history,
                    functions,
                    function_call=fc_mode,
                )
            except Exception as e:
                logger.error("Ошибка GigaChat: %s", e, exc_info=True)
                gen.end(
                    output=str(e),
                    level="ERROR",
                    status_message="GigaChat API error",
                )
                trace.update(output=ERROR_GIGACHAT, metadata={"error": str(e)})
                return ERROR_GIGACHAT

            msg = response.choices[0].message

            # ── Langfuse: фиксируем output и usage generation ──
            gen_output: dict[str, Any] = {}
            if msg.content:
                gen_output["content"] = msg.content
            if msg.function_call:
                gen_output["function_call"] = {
                    "name": msg.function_call.name,
                    "arguments": msg.function_call.arguments,
                }
            usage_details, cost_details = self._extract_usage(response)
            gen.end(
                output=gen_output,
                usage_details=usage_details,
                cost_details=cost_details,
            )

            te.build_assistant_message(history, msg)

            if not msg.function_call:
                final_text = msg.content or "Не удалось получить ответ."
                self._save_search_log(user_id, search_log)
                history = dm.trim_list(history)
                await dm.save_history(user_id, history)
                trace.update(
                    output=final_text,
                    metadata={
                        "total_steps": total_steps,
                        "tool_calls": real_calls,
                        "consecutive_skips_at_end": consecutive_skips,
                    },
                )
                return final_text

            tool_name = msg.function_call.name
            args = te.parse_arguments(msg.function_call.arguments)
            logger.info("Вызов: %s(%s)", tool_name, json.dumps(args, ensure_ascii=False))

            args = await te.preprocess_args(tool_name, args, user_prefs)
            if te.is_duplicate_call(tool_name, args, call_tracker, history):
                consecutive_skips += 1
                continue
            consecutive_skips = 0  # сброс при реальном вызове
            real_calls += 1

            # ── Langfuse: span для tool call ──
            tool_span = trace.span(
                name=f"tool:{tool_name}",
                input=args,
                metadata={"call_number": real_calls},
            )

            if tool_name == "recipe_ingredients" and self._recipe_service is not None:
                result = await self._recipe_service.get_ingredients(args)
            else:
                result = await te.execute(tool_name, args, user_id)
            logger.info("Результат %s: %s", tool_name, result[:MAX_RESULT_LOG_LENGTH])

            result = await te.postprocess_result(
                tool_name,
                args,
                result,
                user_prefs,
                search_log,
                user_id=user_id,
            )

            tool_span.end(
                output=result[:MAX_RESULT_LOG_LENGTH],
                metadata={"full_length": len(result)},
            )

            call_tracker.record_result(tool_name, args, result)
            history.append(Messages(role=MessagesRole.FUNCTION, content=result, name=tool_name))

            # После успешного создания корзины — принудительно завершаем текстом,
            # чтобы модель не продолжала собирать товары из старых запросов в истории.
            if tool_name == "vkusvill_cart_link_create":
                try:
                    cart_data = json.loads(result)
                    if cart_data.get("ok"):
                        cart_created = True
                        logger.info(
                            "User %d: корзина создана, следующий шаг — текстовый ответ",
                            user_id,
                        )
                except (json.JSONDecodeError, TypeError):
                    pass

        self._save_search_log(user_id, search_log)
        history = dm.trim_list(history)
        await dm.save_history(user_id, history)

        trace.update(
            output=ERROR_TOO_MANY_STEPS,
            metadata={
                "total_steps": total_steps,
                "tool_calls": real_calls,
                "error": "too_many_steps",
            },
        )
        return ERROR_TOO_MANY_STEPS

    def _extract_usage(
        self, response: ChatCompletion
    ) -> tuple[dict[str, int] | None, dict[str, float] | None]:
        """Извлечь usage и cost из ответа GigaChat.

        Returns:
            Кортеж (usage_details, cost_details):
            - usage_details: токены по типам (input, output, total,
              precached_tokens, billable_tokens)
            - cost_details: стоимость в ₽ по типам (input, output, total)
              с учётом вычета precached_tokens
        """
        usage = getattr(response, "usage", None)
        if usage is None:
            return None, None

        result: dict[str, int] = {}
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        total = getattr(usage, "total_tokens", None)
        if isinstance(prompt, int):
            result["input"] = prompt
        if isinstance(completion, int):
            result["output"] = completion
        if isinstance(total, int):
            result["total"] = total

        # X-Session-ID кеширование: precached_prompt_tokens
        precached = getattr(usage, "precached_prompt_tokens", None)
        if isinstance(precached, int):
            result["precached_tokens"] = precached
            if isinstance(total, int):
                result["billable_tokens"] = total - precached

        if not result:
            return None, None

        # ── Cost details (₽) ──
        # Тарифы GigaChat: единая цена за токен (input = output).
        # Precached токены бесплатны → вычитаем из input.
        price = GIGACHAT_TOKEN_PRICES.get(self._model_name)
        cost: dict[str, float] | None = None
        if price is not None:
            billable_input = result.get("input", 0)
            if isinstance(precached, int):
                billable_input = max(0, billable_input - precached)
            output_tokens = result.get("output", 0)
            cost = {
                "input": billable_input * price,
                "output": output_tokens * price,
                "total": (billable_input + output_tokens) * price,
            }

        # Structured logging
        log_data: dict[str, Any] = {"event": "llm_usage", **result}
        if cost is not None:
            log_data["cost_rub"] = round(cost["total"], 6)
        logger.info(
            "Кеш: precached=%d, prompt=%d, completion=%d, total=%d, billable=%d",
            result.get("precached_tokens", 0),
            result.get("input", 0),
            result.get("output", 0),
            result.get("total", 0),
            result.get("billable_tokens", result.get("total", 0)),
        )
        logger.info("LLM usage: %s", json.dumps(log_data, ensure_ascii=False))
        return result, cost
