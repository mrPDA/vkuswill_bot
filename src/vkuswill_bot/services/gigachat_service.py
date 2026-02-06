"""Сервис GigaChat с поддержкой function calling через MCP-инструменты."""

import asyncio
import copy
import json
import logging
import math
from collections import OrderedDict

from gigachat import GigaChat
from gigachat.models import Chat, Messages, MessagesRole

from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.preferences_store import PreferencesStore

logger = logging.getLogger(__name__)

# Лимит одновременно хранимых диалогов (LRU-вытеснение)
MAX_CONVERSATIONS = 1000

# Лимит кеша цен (кол-во товаров)
MAX_PRICE_CACHE_SIZE = 5000

# Лимит длины входящего сообщения пользователя (символы)
MAX_USER_MESSAGE_LENGTH = 4096

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

Шаг 3. Из результатов поиска выбери ОДИН лучший товар на позицию. \
Создай ОДНУ ссылку на корзину с оптимальным набором (цена/рейтинг). \
Если пользователь явно просит варианты/сравнение — тогда создай 2-3 \
корзины (например: "Выгодно" с sort=price_asc, "Лучшее" с sort=rating).

Итого в корзине 2-5 товаров (по числу позиций), НЕ 20.

## Как вызывать vkusvill_cart_link_create
Параметр products — массив объектов {xml_id, q}. \
НЕ ДУБЛИРУЙ xml_id — используй q для количества!

q — дробное число (0.01–40) в ЕДИНИЦАХ товара (поле "unit" из поиска). \
Примеры: unit="кг" + "1,5 кг" → q=1.5; unit="кг" + "500 г" → q=0.5; \
unit="шт" + "4 штуки" → q=4; unit="л" + "пол-литра" → q=0.5.

Пример: {"products": [{"xml_id": 41728, "q": 1.5}, {"xml_id": 103297, "q": 4}]}

## Правила подбора
- В каждой корзине 2-5 товаров — по одному на каждую позицию.
- Из первых результатов поиска выбирай ОДИН самый подходящий товар.
- Не сваливай все результаты поиска в одну корзину!
- Максимум 20 позиций в одной ссылке.

## Предпочтения пользователя
Перед ПЕРВЫМ поиском товаров — вызови user_preferences_get. \
Если пользователь просит запомнить предпочтение ("запомни", "я люблю", \
"я предпочитаю") — вызови user_preferences_set с категорией и описанием. \
При поиске учитывай предпочтения: используй их как поисковый запрос. \
Для удаления предпочтения — user_preferences_delete.

## Парсинг ответов
Все инструменты возвращают ТЕКСТ с JSON — парси его.

## Формат ответа (СТРОГО следуй)
- Русский язык. Дружелюбный тон.
- Ответ с корзиной ОБЯЗАТЕЛЬНО содержит:
  1. Название (что собрали)
  2. Пронумерованный список КАЖДОГО товара — название, цена × количество = сумма \
(бери строки из price_summary.items результата vkusvill_cart_link_create)
  3. Итог — бери total_text из price_summary. НЕ считай сам!
  4. Ссылка <a href="URL">Открыть корзину</a>
- НИКОГДА не пропускай список товаров! Покупатель должен видеть что именно в корзине.
- Дисклеймер: после каждого ответа с корзиной добавляй: \
"Наличие и точное количество товаров будет проверено при открытии \
ссылки на корзину. ВкусВилл может скорректировать заказ в зависимости \
от наличия. Цены и состав уточняйте на сайте."
"""


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
        # Кеш цен: xml_id -> {name, price, unit}
        self._price_cache: dict[int, dict] = {}

    @staticmethod
    def _enhance_cart_schema(params: dict) -> dict:
        """Дополнить схему vkusvill_cart_link_create описаниями параметров.

        GigaChat плохо работает с дробными значениями, если у параметра
        нет текстового description. Добавляем description к xml_id и q,
        чтобы GigaChat корректно генерировал аргументы.
        """
        params = copy.deepcopy(params)
        items_schema = (
            params.get("properties", {})
            .get("products", {})
            .get("items", {})
        )
        if not items_schema:
            return params

        props = items_schema.get("properties", {})
        if "xml_id" in props:
            props["xml_id"]["description"] = "ID товара из результата поиска"
        if "q" in props:
            props["q"]["description"] = (
                "Количество в единицах товара (поле unit). "
                "ДРОБНОЕ число, например 1.5 для полутора кг. "
                "Всегда указывай явно!"
            )
        # Сделать q обязательным
        required = items_schema.get("required", [])
        if "q" not in required:
            items_schema["required"] = list(required) + ["q"]

        return params

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
                params = self._enhance_cart_schema(params)
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

    # Поля товара, которые передаём в GigaChat (остальные срезаем)
    _SEARCH_ITEM_FIELDS = ("xml_id", "name", "price", "unit", "weight", "rating")

    def _trim_search_result(self, result_text: str) -> str:
        """Обрезать результат поиска, оставив только нужные поля.

        Убирает description, images, slug и другие тяжёлые поля,
        чтобы не раздувать контекстное окно GigaChat.
        Кеширование цен делается ДО обрезки (в _cache_prices_from_search).
        """
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return result_text

        data_field = data.get("data") if isinstance(data, dict) else None
        if not isinstance(data_field, dict):
            return result_text

        items = data_field.get("items")
        if not items or not isinstance(items, list):
            return result_text

        # Обрезаем количество товаров до SEARCH_LIMIT
        # (MCP API игнорирует параметр limit и всегда возвращает 10)
        max_items = VkusvillMCPClient.SEARCH_LIMIT

        trimmed_items = []
        for item in items[:max_items]:
            if not isinstance(item, dict):
                continue
            trimmed = {k: item[k] for k in self._SEARCH_ITEM_FIELDS if k in item}
            # Упрощаем price — оставляем только current
            price = trimmed.get("price")
            if isinstance(price, dict):
                trimmed["price"] = price.get("current")
            trimmed_items.append(trimmed)

        data_field["items"] = trimmed_items
        return json.dumps(data, ensure_ascii=False)

    def _cache_prices_from_search(self, result_text: str) -> None:
        """Извлечь цены из результата vkusvill_products_search и закешировать."""
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return
        data_field = data.get("data") if isinstance(data, dict) else None
        if not isinstance(data_field, dict):
            return
        items = data_field.get("items", [])
        for item in items:
            xml_id = item.get("xml_id")
            price_info = item.get("price", {})
            price = price_info.get("current")
            if xml_id is not None and price is not None:
                self._price_cache[xml_id] = {
                    "name": item.get("name", ""),
                    "price": price,
                    "unit": item.get("unit", "шт"),
                }

        # Ограничиваем рост кеша — удаляем старые записи (FIFO)
        if len(self._price_cache) > MAX_PRICE_CACHE_SIZE:
            keys_to_remove = list(self._price_cache.keys())[
                : MAX_PRICE_CACHE_SIZE // 2
            ]
            for k in keys_to_remove:
                del self._price_cache[k]
            logger.info(
                "Очищен кеш цен: удалено %d записей, осталось %d",
                len(keys_to_remove),
                len(self._price_cache),
            )

    # Единицы измерения, для которых q должно быть целым числом
    _DISCRETE_UNITS = frozenset({"шт", "уп", "пач", "бут", "бан", "пак"})

    def _fix_unit_quantities(self, args: dict) -> dict:
        """Округлить q до целого для штучных товаров.

        GigaChat иногда ставит дробное q для товаров в штуках
        (например, 0.68 для банки огурцов). Для товаров с unit='шт'
        округляем q вверх до ближайшего целого.
        """
        products = args.get("products")
        if not products or not isinstance(products, list):
            return args

        changed = False
        for item in products:
            if not isinstance(item, dict):
                continue
            xml_id = item.get("xml_id")
            q = item.get("q", 1)
            cached = self._price_cache.get(xml_id)
            if cached and cached.get("unit", "шт") in self._DISCRETE_UNITS:
                rounded = math.ceil(q)
                if rounded != q:
                    logger.info(
                        "Округление q: xml_id=%s, unit=%s, %s → %s",
                        xml_id, cached["unit"], q, rounded,
                    )
                    item["q"] = rounded
                    changed = True

        if changed:
            logger.info("Исправленные аргументы корзины: %s", args)

        return args

    def _calc_cart_total(self, args: dict, result_text: str) -> str:
        """Рассчитать стоимость корзины и дополнить результат.

        Берёт xml_id и q из аргументов, цены из кеша.
        Добавляет к результату текстовую разбивку по позициям и итог.
        """
        try:
            result_data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return result_text

        if not result_data.get("ok"):
            return result_text

        products = args.get("products", [])
        if not products:
            return result_text

        lines = []
        total = 0.0
        all_found = True

        for item in products:
            xml_id = item.get("xml_id")
            q = item.get("q", 1)
            cached = self._price_cache.get(xml_id)
            if cached:
                subtotal = cached["price"] * q
                total += subtotal
                lines.append(
                    f"  - {cached['name']}: {cached['price']} руб/{cached['unit']}"
                    f" × {q} = {subtotal:.2f} руб"
                )
            else:
                all_found = False
                lines.append(f"  - xml_id={xml_id}: цена неизвестна")

        # Добавляем расчёт в JSON-результат
        summary: dict = {"items": lines}
        if all_found:
            summary["total"] = round(total, 2)
            summary["total_text"] = f"Итого: {total:.2f} руб"
        else:
            summary["total_text"] = (
                "Итого: не удалось рассчитать (не все цены известны)"
            )

        data = result_data.get("data")
        if not isinstance(data, dict):
            logger.warning(
                "Результат корзины без поля 'data': %s", result_text[:200],
            )
            return result_text
        data["price_summary"] = summary
        return json.dumps(result_data, ensure_ascii=False, indent=4)

    @staticmethod
    def _extract_xml_ids_from_search(result_text: str) -> set[int]:
        """Извлечь xml_id из результата поиска."""
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return set()
        data_field = data.get("data") if isinstance(data, dict) else None
        if not isinstance(data_field, dict):
            return set()
        items = data_field.get("items", [])
        return {
            item["xml_id"]
            for item in items
            if isinstance(item, dict) and "xml_id" in item
        }

    def _verify_cart(
        self,
        cart_args: dict,
        search_log: dict[str, set[int]],
    ) -> dict:
        """Сопоставить содержимое корзины с поисковыми запросами.

        Возвращает отчёт:
        - matched: [{query, name, xml_id}] — товар найден по этому запросу
        - missing_queries: [query] — запрос был, но товара в корзине нет
        - unmatched_items: [{name, xml_id}] — товар не из поиска

        Это позволяет GigaChat увидеть ошибки и скорректировать корзину.
        """
        cart_xml_ids: set[int] = set()
        for item in cart_args.get("products", []):
            xml_id = item.get("xml_id")
            if xml_id is not None:
                cart_xml_ids.add(xml_id)

        # Обратный индекс: xml_id → список запросов, которые его нашли
        xml_to_queries: dict[int, list[str]] = {}
        for query, xml_ids in search_log.items():
            for xml_id in xml_ids:
                xml_to_queries.setdefault(xml_id, []).append(query)

        matched: list[dict] = []
        unmatched_items: list[dict] = []
        queries_with_match: set[str] = set()

        for xml_id in cart_xml_ids:
            cached = self._price_cache.get(xml_id)
            name = cached["name"] if cached else f"xml_id={xml_id}"
            queries = xml_to_queries.get(xml_id, [])
            if queries:
                matched.append({
                    "query": queries[0],
                    "name": name,
                    "xml_id": xml_id,
                })
                queries_with_match.update(queries)
            else:
                unmatched_items.append({"name": name, "xml_id": xml_id})

        # Запросы, по которым ничего не попало в корзину
        missing_queries = [
            q for q in search_log if q not in queries_with_match
        ]

        report: dict = {
            "matched": matched,
            "missing_queries": missing_queries,
            "unmatched_items": unmatched_items,
        }

        if missing_queries or unmatched_items:
            issues = []
            for q in missing_queries:
                issues.append(
                    f'Поиск "{q}" не имеет соответствия в корзине — '
                    "товар пропущен!"
                )
            for item in unmatched_items:
                issues.append(
                    f'Товар "{item["name"]}" в корзине не соответствует '
                    "ни одному поисковому запросу."
                )
            report["issues"] = issues
            report["action_required"] = (
                "ВНИМАНИЕ: корзина не соответствует запросу. "
                "Пропущенные товары нужно найти и добавить. "
                "Пересобери корзину, включив ВСЕ запрошенные позиции."
            )
            logger.warning("Верификация корзины: %s", issues)
        else:
            report["ok"] = True

        return report

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
        call_counts: dict[str, int] = {}  # "name:args" -> кол-во вызовов
        search_log: dict[str, set[int]] = {}  # query -> {xml_ids}
        user_prefs: dict[str, str] = {}  # категория -> предпочтение

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

                # Округляем дробные q для штучных товаров в корзине
                if tool_name == "vkusvill_cart_link_create":
                    args = self._fix_unit_quantities(args)

                # Подстановка предпочтений в поисковый запрос
                if tool_name == "vkusvill_products_search" and user_prefs:
                    q = args.get("q", "")
                    enhanced_q = self._apply_preferences_to_query(q, user_prefs)
                    if enhanced_q != q:
                        logger.info(
                            "Подстановка предпочтения: %r → %r", q, enhanced_q,
                        )
                        args = {**args, "q": enhanced_q}

                # Проверяем, не зацикливается ли вызов
                # Отслеживаем ВСЕ повторные вызовы ДО выполнения
                call_key = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
                call_counts[call_key] = call_counts.get(call_key, 0) + 1

                if call_counts[call_key] >= 2:
                    logger.warning(
                        "Зацикливание: %s вызван %d раз с одинаковыми "
                        "аргументами, пропускаю вызов",
                        tool_name,
                        call_counts[call_key],
                    )
                    # Подсказываем GigaChat не повторять вызов
                    hint = Messages(
                        role=MessagesRole.FUNCTION,
                        content=(
                            "Ты уже вызывал этот инструмент с теми же аргументами. "
                            "Результат тот же. Используй уже полученные данные "
                            "и продолжай — не повторяй этот вызов."
                        ),
                        name=tool_name,
                    )
                    history.append(hint)
                    continue

                # Выполняем вызов: локальный или через MCP
                try:
                    if tool_name in self._LOCAL_TOOL_NAMES:
                        result = await self._call_local_tool(
                            tool_name, args, user_id,
                        )
                    else:
                        result = await self._mcp_client.call_tool(tool_name, args)
                except Exception as e:
                    logger.error("Ошибка %s: %s", tool_name, e, exc_info=True)
                    result = json.dumps(
                        {"error": f"Ошибка вызова {tool_name}: {e}"},
                        ensure_ascii=False,
                    )

                logger.info(
                    "Результат %s: %s",
                    tool_name,
                    result[:1000] if len(result) > 1000 else result,
                )

                # Парсим предпочтения для программной подстановки
                if tool_name == "user_preferences_get":
                    parsed = self._parse_preferences(result)
                    if parsed:
                        user_prefs = parsed
                        logger.info(
                            "Загружены предпочтения: %s",
                            {k: v for k, v in user_prefs.items()},
                        )

                # Кешируем цены из поиска, трекаем xml_ids, обрезаем результат
                if tool_name == "vkusvill_products_search":
                    self._cache_prices_from_search(result)
                    query = args.get("q", "")
                    found_ids = self._extract_xml_ids_from_search(result)
                    if query and found_ids:
                        search_log[query] = found_ids
                    result = self._trim_search_result(result)

                # Рассчитываем стоимость корзины и верифицируем
                if tool_name == "vkusvill_cart_link_create":
                    result = self._calc_cart_total(args, result)
                    # Верификация: сопоставляем корзину с поисковыми запросами
                    if search_log:
                        verification = self._verify_cart(args, search_log)
                        try:
                            result_data = json.loads(result)
                            data = result_data.get("data")
                            if isinstance(data, dict):
                                data["verification"] = verification
                                result = json.dumps(
                                    result_data, ensure_ascii=False, indent=4,
                                )
                        except (json.JSONDecodeError, TypeError):
                            pass
                    logger.info("Расчёт корзины: %s", result[:500])

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
