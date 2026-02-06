"""Сервис GigaChat с поддержкой function calling через MCP-инструменты."""

import asyncio
import json
import logging

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from vkuswill_bot.services.mcp_client import VkusvillMCPClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
Ты — продавец-консультант ВкусВилл в Telegram-боте. \
Помогаешь пользователям подбирать продукты и собирать корзину.

## Понимание запроса
Когда пользователь просит собрать что-то на ужин/обед/завтрак/перекус — \
он хочет ПОЛНОЦЕННЫЙ НАБОР продуктов, а не одну категорию. \
Например, "паста на ужин" = макароны + соус + сыр (пармезан или другой). \
"Завтрак" = яйца + хлеб + масло + сыр/колбаса + напиток. \
Раздели запрос на отдельные позиции и ищи каждую.

## Рабочий процесс (СТРОГО следуй)

Шаг 1. Выясни потребность (не более 1-2 коротких вопросов). \
Если запрос достаточно понятен — не задавай вопросов, сразу ищи.

Шаг 2. Разбей запрос на 2-5 конкретных продуктов. \
Для каждого продукта ищи ОТДЕЛЬНЫМ запросом — коротким ключевым словом. \
Примеры хороших поисковых запросов: "спагетти", "соус песто", "пармезан", \
"сливки", "куриное филе". \
Плохие запросы: "макароны твердых сортов пшеницы" (слишком длинный).

Шаг 3. Для каждой корзины выбери по ОДНОМУ лучшему товару на позицию:
- "Выгодно" — ищи с sort=price_asc, бери самый дешёвый подходящий
- "Любимое" — ищи с sort=rating, бери с лучшим рейтингом
- "Лайт" — бери наименее калорийный \
(проверяй через vkusvill_product_details если нужно)

Итого в каждой корзине 2-5 товаров (по числу позиций), НЕ 20.

Шаг 4. Создай РОВНО ТРИ ссылки на корзину, вызвав \
vkusvill_cart_link_create ТРИ раза — по одной для каждого варианта.

Шаг 5. Покажи пользователю три варианта с ценами и ссылками.

## Как вызывать vkusvill_cart_link_create
Параметр products — массив объектов. Каждый ОБЯЗАТЕЛЬНО содержит:
- xml_id (число) — из результата поиска
- q (число) — количество, по умолчанию 1

Пример: {"products": [{"xml_id": 27370, "q": 1}, {"xml_id": 34249, "q": 1}]}

## Правила подбора
- В каждой корзине 2-5 товаров — по одному на каждую позицию.
- Из первых результатов поиска выбирай ОДИН самый подходящий товар.
- Не сваливай все результаты поиска в одну корзину!
- Максимум 20 позиций в одной ссылке.

## Парсинг ответов
Все инструменты возвращают ТЕКСТ с JSON — парси его.

## Формат ответа
- Русский язык. Дружелюбный тон.
- Для каждой корзины: название, список товаров с ценами, \
итог, ссылка <a href="URL">Открыть корзину</a>.
- Дисклеймер: цены, наличие и состав уточняй на карточках товаров \
на сайте ВкусВилл.
"""


class GigaChatService:
    """Сервис для взаимодействия с GigaChat и MCP-инструментами.

    Управляет историей диалогов пользователей и реализует цикл
    function calling: GigaChat решает, какой инструмент вызвать,
    бот выполняет вызов через MCP, результат возвращается в GigaChat.
    """

    def __init__(
        self,
        credentials: str,
        model: str,
        scope: str,
        mcp_client: VkusvillMCPClient,
        max_tool_calls: int = 15,
        max_history: int = 50,
    ) -> None:
        self._client = GigaChat(
            credentials=credentials,
            model=model,
            scope=scope,
            verify_ssl_certs=False,
        )
        self._mcp_client = mcp_client
        self._max_tool_calls = max_tool_calls
        self._max_history = max_history
        self._conversations: dict[int, list[Messages]] = {}
        self._functions: list[dict] | None = None

    async def _get_functions(self) -> list[dict]:
        """Получить описания функций для GigaChat из MCP-инструментов."""
        if self._functions is not None:
            return self._functions

        tools = await self._mcp_client.get_tools()
        self._functions = []
        for tool in tools:
            self._functions.append(
                {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                }
            )
        logger.info("Функции для GigaChat: %s", [f["name"] for f in self._functions])
        return self._functions

    def _get_history(self, user_id: int) -> list[Messages]:
        """Получить или создать историю диалога пользователя."""
        if user_id not in self._conversations:
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

    async def close(self) -> None:
        """Закрыть клиент GigaChat."""
        try:
            await asyncio.to_thread(self._client.close)
        except Exception:
            pass

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
        history = self._get_history(user_id)
        history.append(Messages(role=MessagesRole.USER, content=text))

        functions = await self._get_functions()
        failed_calls: dict[str, int] = {}  # "name:args" -> кол-во неудач

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
                return (
                    "Произошла ошибка при обращении к GigaChat. "
                    "Попробуйте позже или начните новый диалог: /reset"
                )

            choice = response.choices[0]
            msg = choice.message

            # Создаём сообщение ассистента для истории
            assistant_msg = Messages(
                role=MessagesRole.ASSISTANT,
                content=msg.content or "",
            )
            if msg.function_call:
                assistant_msg.function_call = msg.function_call
            if hasattr(msg, "functions_state_id") and msg.functions_state_id:
                assistant_msg.functions_state_id = msg.functions_state_id
            history.append(assistant_msg)

            # Если GigaChat хочет вызвать инструмент
            if msg.function_call:
                tool_name = msg.function_call.name
                raw_args = msg.function_call.arguments

                # Парсим аргументы (могут быть строкой или dict)
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}

                logger.info(
                    "Вызов инструмента: %s(%s)",
                    tool_name,
                    json.dumps(args, ensure_ascii=False),
                )

                # Выполняем вызов через MCP
                try:
                    result = await self._mcp_client.call_tool(tool_name, args)
                except Exception as e:
                    logger.error("Ошибка MCP %s: %s", tool_name, e, exc_info=True)
                    result = json.dumps(
                        {"error": f"Ошибка вызова {tool_name}: {e}"},
                        ensure_ascii=False,
                    )

                logger.info(
                    "Результат %s: %s",
                    tool_name,
                    result[:1000] if len(result) > 1000 else result,
                )

                # Проверяем, не зацикливается ли вызов
                call_key = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
                try:
                    parsed = json.loads(result)
                    is_error = isinstance(parsed, dict) and parsed.get("ok") is False
                except (json.JSONDecodeError, TypeError):
                    is_error = False

                if is_error:
                    failed_calls[call_key] = failed_calls.get(call_key, 0) + 1
                    if failed_calls[call_key] >= 2:
                        logger.warning(
                            "Зацикливание: %s вернул ошибку %d раз, прерываю",
                            tool_name,
                            failed_calls[call_key],
                        )
                        # Сообщаем GigaChat, что нужно ответить текстом
                        error_hint = Messages(
                            role=MessagesRole.FUNCTION,
                            content=(
                                "ОШИБКА: вызов провалился повторно. "
                                "Не повторяй этот вызов. "
                                "Ответь пользователю текстом, извинись "
                                "и предложи товары без ссылки на корзину."
                            ),
                            name=tool_name,
                        )
                        history.append(error_hint)
                        continue

                # Добавляем результат функции в историю
                func_msg = Messages(
                    role=MessagesRole.FUNCTION,
                    content=result,
                    name=tool_name,
                )
                history.append(func_msg)
            else:
                # GigaChat вернул текстовый ответ — конец цикла
                self._trim_history(user_id)
                return msg.content or "Не удалось получить ответ."

        # Достигнут лимит вызовов инструментов
        self._trim_history(user_id)
        return (
            "Обработка заняла слишком много шагов. "
            "Попробуйте упростить запрос или начните заново: /reset"
        )
