"""Обработка результатов поиска и кеш цен."""

import json
import logging

from vkuswill_bot.services.mcp_client import VkusvillMCPClient

logger = logging.getLogger(__name__)

# Лимит кеша цен (кол-во товаров)
MAX_PRICE_CACHE_SIZE = 5000


class SearchProcessor:
    """Обработка результатов поиска ВкусВилл и кеширование цен.

    Владеет кешем цен (xml_id → {name, price, unit}),
    обрезает тяжёлые поля из результатов поиска,
    извлекает xml_id для верификации корзины.
    """

    # Поля товара, которые передаём в GigaChat (остальные срезаем)
    _SEARCH_ITEM_FIELDS = ("xml_id", "name", "price", "unit", "weight", "rating")

    def __init__(self) -> None:
        self.price_cache: dict[int, dict] = {}

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

    def trim_search_result(self, result_text: str) -> str:
        """Обрезать результат поиска, оставив только нужные поля.

        Убирает description, images, slug и другие тяжёлые поля,
        чтобы не раздувать контекстное окно GigaChat.
        Кеширование цен делается ДО обрезки (в cache_prices).
        """
        parsed = self.parse_search_items(result_text)
        if parsed is None:
            return result_text

        data, items = parsed
        data_field = data["data"]

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

    def cache_prices(self, result_text: str) -> None:
        """Извлечь цены из результата vkusvill_products_search и закешировать."""
        parsed = self.parse_search_items(result_text)
        if parsed is None:
            return
        _, items = parsed
        for item in items:
            xml_id = item.get("xml_id")
            price_info = item.get("price", {})
            price = price_info.get("current")
            if xml_id is not None and price is not None:
                self.price_cache[xml_id] = {
                    "name": item.get("name", ""),
                    "price": price,
                    "unit": item.get("unit", "шт"),
                }

        # Ограничиваем рост кеша — удаляем старые записи (FIFO)
        if len(self.price_cache) > MAX_PRICE_CACHE_SIZE:
            keys_to_remove = list(self.price_cache.keys())[
                : MAX_PRICE_CACHE_SIZE // 2
            ]
            for k in keys_to_remove:
                del self.price_cache[k]
            logger.info(
                "Очищен кеш цен: удалено %d записей, осталось %d",
                len(keys_to_remove),
                len(self.price_cache),
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
