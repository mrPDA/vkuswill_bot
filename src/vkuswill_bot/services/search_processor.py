"""Обработка результатов поиска и кеш цен."""

import json
import logging
import re

from vkuswill_bot.services.price_cache import PriceCache

logger = logging.getLogger(__name__)

# Максимум товаров в результатах поиска (экономия токенов)
SEARCH_LIMIT = 5

# Минимальная длина слова для проверки релевантности
_MIN_RELEVANCE_WORD_LEN = 3

# Русские стоп-слова (не учитываются при проверке релевантности)
_STOP_WORDS: frozenset[str] = frozenset({
    "без", "более", "бы", "был", "была", "были", "было",
    "быть", "вам", "вас", "весь", "вот", "все", "всё",
    "всех", "вся", "где", "для", "его", "еда", "еды",
    "если", "есть", "ещё", "жир", "или", "ими", "как",
    "кое", "мне", "мой", "моя", "моё", "мои", "над",
    "нам", "нас", "наш", "наша", "наше", "наши",
    "них", "она", "они", "оно", "при", "про",
    "сам", "сама", "само", "сами", "свой", "своя",
    "своё", "свои", "так", "тебе", "тебя", "тем",
    "тех", "что", "чем", "чей", "чья", "чьё",
    "чьи", "эта", "эти", "это", "этих", "этой", "этом",
})


class SearchProcessor:
    """Обработка результатов поиска ВкусВилл и кеширование цен.

    Владеет кешем цен (xml_id → {name, price, unit}),
    очисткой поисковых запросов, ограничением результатов,
    обрезкой тяжёлых полей и извлечением xml_id.
    """

    # Поля товара, которые передаём в GigaChat (остальные срезаем)
    _SEARCH_ITEM_FIELDS = ("xml_id", "name", "price", "unit", "weight", "rating")

    # Паттерн для очистки поисковых запросов:
    # удаляем числа с единицами ("400 гр", "5%", "2 банки", "450 мл")
    _UNIT_PATTERN = re.compile(
        r"\b\d+[,.]?\d*\s*"
        r"(%|шт\w*|гр\w*|г\b|кг\w*|мл\w*|л\b|литр\w*|"
        r"бутыл\w*|банк\w*|пач\w*|уп\w*|порц\w*)",
        re.IGNORECASE,
    )
    # Отдельные числа ("молоко 4", "мороженое 2")
    _STANDALONE_NUM = re.compile(r"\b\d+\b")

    def __init__(self, price_cache: PriceCache | None = None) -> None:
        self.price_cache: PriceCache = price_cache if price_cache is not None else PriceCache()

    @classmethod
    def clean_search_query(cls, query: str) -> str:
        """Очистить поисковый запрос от количеств и единиц измерения.

        GigaChat часто передаёт в поиск полный текст пользователя
        вместо ключевых слов, например "Творог 5% 400 гр" или "молоко 4".
        Числа и единицы мусорят поисковую выдачу MCP API.

        Примеры:
            "Творог 5% 400 гр" → "Творог"
            "тунец 2 банки" → "тунец"
            "молоко 4" → "молоко"
            "мороженое 2" → "мороженое"
            "темный хлеб" → "темный хлеб" (без изменений)
        """
        cleaned = cls._UNIT_PATTERN.sub("", query)
        cleaned = cls._STANDALONE_NUM.sub("", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned or query

    @staticmethod
    def parse_search_items(result_text: str) -> tuple[dict, list[dict]] | None:
        """Распарсить JSON-ответ поиска и извлечь (data, items).

        Returns:
            Кортеж (полный dict ответа, список items) или None,
            если парсинг не удался или items пуст.
        """
        try:
            data = json.loads(result_text)
        except (json.JSONDecodeError, TypeError):
            return None
        data_field = data.get("data") if isinstance(data, dict) else None
        if not isinstance(data_field, dict):
            return None
        items = data_field.get("items")
        if not items or not isinstance(items, list):
            return None
        return data, items

    @staticmethod
    def check_relevance(query: str, items: list[dict]) -> list[str]:
        """Проверить, содержат ли результаты поиска все слова запроса.

        Токенизирует запрос на значимые слова (длиннее 2 символов, не стоп-слова)
        и проверяет, встречается ли каждое слово хотя бы в одном названии товара.

        Args:
            query: Поисковый запрос пользователя.
            items: Список товаров (dict с ключом ``name``).

        Returns:
            Список слов из запроса, которые НЕ найдены ни в одном названии товара.
            Пустой список означает полное соответствие.
        """
        if not query or not items:
            return []

        # Токенизация: значимые слова (>= _MIN_RELEVANCE_WORD_LEN, не стоп-слова)
        words = [
            w.lower()
            for w in query.split()
            if len(w) >= _MIN_RELEVANCE_WORD_LEN and w.lower() not in _STOP_WORDS
        ]
        if not words:
            return []

        # Собираем все названия товаров в одну строку (lower)
        all_names = " ".join(
            item.get("name", "").lower()
            for item in items
            if isinstance(item, dict)
        )

        # Проверяем каждое слово: есть ли оно (как подстрока) хотя бы в одном названии
        return [word for word in words if word not in all_names]

    def trim_search_result(self, result_text: str) -> str:
        """Обрезать результат поиска, оставив только нужные поля.

        Убирает description, images, slug и другие тяжёлые поля,
        чтобы не раздувать контекстное окно GigaChat.
        Кеширование цен делается ДО обрезки (в cache_prices).

        Если значимые слова из запроса не найдены ни в одном товаре,
        добавляет ``relevance_warning`` — сигнал для GigaChat, что товар
        может отсутствовать в каталоге.
        """
        parsed = self.parse_search_items(result_text)
        if parsed is None:
            return result_text

        data, items = parsed
        data_field = data["data"]

        # Обрезаем количество товаров до SEARCH_LIMIT
        # (MCP API игнорирует параметр limit и всегда возвращает 10)
        max_items = SEARCH_LIMIT

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

        # ---- Проверка релевантности ----
        meta = data_field.get("meta", {})
        query = meta.get("q", "") if isinstance(meta, dict) else ""
        if query and trimmed_items:
            missing = self.check_relevance(query, trimmed_items)
            if missing:
                terms_str = ", ".join(f"«{t}»" for t in missing)
                data_field["relevance_warning"] = (
                    f"По запросу «{query}» не найдено товаров, содержащих: "
                    f"{terms_str}. Возможно, этот товар отсутствует в каталоге "
                    f"ВкусВилл. Сообщи пользователю и спроси, "
                    f"подойдёт ли альтернатива из результатов."
                )
                logger.info(
                    "Релевантность: запрос %r, не найдены термины: %s",
                    query,
                    missing,
                )

        return json.dumps(data, ensure_ascii=False)

    async def cache_prices(self, result_text: str) -> None:
        """Извлечь цены из результата vkusvill_products_search и закешировать."""
        parsed = self.parse_search_items(result_text)
        if parsed is None:
            return
        _, items = parsed
        for item in items:
            xml_id = item.get("xml_id")
            price_info = item.get("price", {})
            if not isinstance(price_info, dict):
                continue
            price = price_info.get("current")
            if xml_id is not None and price is not None:
                await self.price_cache.set(
                    xml_id,
                    name=item.get("name", ""),
                    price=price,
                    unit=item.get("unit", "шт"),
                )

    def extract_xml_ids(self, result_text: str) -> set[int]:
        """Извлечь xml_id из результата поиска."""
        parsed = self.parse_search_items(result_text)
        if parsed is None:
            return set()
        _, items = parsed
        return {
            item["xml_id"]
            for item in items
            if isinstance(item, dict) and "xml_id" in item
        }
