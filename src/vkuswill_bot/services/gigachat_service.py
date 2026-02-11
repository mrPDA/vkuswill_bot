"""Сервис GigaChat с поддержкой function calling через MCP-инструменты."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from gigachat import GigaChat
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
GIGACHAT_MAX_RETRIES = 3

# Лимит количества поисковых запросов в search_log на пользователя
MAX_SEARCH_LOG_QUERIES = 100


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
    ) -> None:
        # TODO: verify_ssl_certs=True + ca_bundle_file когда SDK поддержит CA Минцифры
        self._client = GigaChat(
            credentials=credentials,
            model=model,
            scope=scope,
            verify_ssl_certs=False,
            timeout=60,
        )
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
        self._search_logs: dict[int, dict[str, set[int]]] = {}
        self._search_processor = SearchProcessor()  # создаёт PriceCache внутри
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
        """
        return self._search_logs.get(user_id, {})

    def _save_search_log(
        self,
        user_id: int,
        search_log: dict[str, set[int]],
    ) -> None:
        """Сохранить search_log для пользователя с лимитом размера."""
        if len(search_log) > MAX_SEARCH_LOG_QUERIES:
            keys = list(search_log.keys())
            for key in keys[: len(keys) - MAX_SEARCH_LOG_QUERIES]:
                del search_log[key]
        self._search_logs[user_id] = search_log

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
        for attempt in range(GIGACHAT_MAX_RETRIES):
            try:
                async with self._api_semaphore:
                    return await asyncio.to_thread(
                        self._client.chat,
                        Chat(
                            messages=history,
                            functions=functions,
                            function_call="auto",
                        ),
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

        while real_calls < self._max_tool_calls and total_steps < max_total_steps:
            total_steps += 1
            logger.info("Шаг %d для user %d (вызовов: %d)", total_steps, user_id, real_calls)

            # ── Langfuse: generation для каждого вызова LLM ──
            generation_idx += 1
            gen = trace.generation(
                name=f"gigachat-{generation_idx}",
                model=self._model_name,
                input=_messages_to_langfuse(history),
                model_parameters={"function_call": "auto"},
                metadata={"step": total_steps, "real_calls": real_calls},
            )

            try:
                response = await self._call_gigachat(history, functions)
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
            usage = self._extract_usage(response)
            gen.end(output=gen_output, usage=usage)

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
                    },
                )
                return final_text

            tool_name = msg.function_call.name
            args = te.parse_arguments(msg.function_call.arguments)
            logger.info("Вызов: %s(%s)", tool_name, json.dumps(args, ensure_ascii=False))

            args = await te.preprocess_args(tool_name, args, user_prefs)
            if te.is_duplicate_call(tool_name, args, call_tracker, history):
                continue
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

    @staticmethod
    def _extract_usage(response: ChatCompletion) -> dict[str, int] | None:
        """Извлечь usage (токены) из ответа GigaChat, если доступно."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        result: dict[str, int] = {}
        if hasattr(usage, "prompt_tokens") and usage.prompt_tokens is not None:
            result["input"] = usage.prompt_tokens
        if hasattr(usage, "completion_tokens") and usage.completion_tokens is not None:
            result["output"] = usage.completion_tokens
        if hasattr(usage, "total_tokens") and usage.total_tokens is not None:
            result["total"] = usage.total_tokens
        return result or None
