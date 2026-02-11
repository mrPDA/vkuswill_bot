"""Тесты SearchProcessor.

Тестируем:
- Кеширование цен из результатов поиска
- Обрезку тяжёлых полей из результатов поиска
- Извлечение xml_id из результатов поиска
- Проверку релевантности результатов поиска запросу
"""

import json

import pytest

from vkuswill_bot.services.price_cache import MAX_PRICE_CACHE_SIZE
from vkuswill_bot.services.search_processor import (
    SEARCH_LIMIT,
    SearchProcessor,
    _MIN_RELEVANCE_WORD_LEN,
    _STOP_WORDS,
)


# ============================================================================
# Фикстуры
# ============================================================================


@pytest.fixture
def processor() -> SearchProcessor:
    """Экземпляр SearchProcessor."""
    return SearchProcessor()


# ============================================================================
# Кеш цен
# ============================================================================


class TestCachePrices:
    """Тесты cache_prices: кеширование цен из результатов поиска."""

    async def test_caches_prices(self, processor):
        """Извлекает цены из результата поиска."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "xml_id": 41728,
                            "name": "Картофель молодой Египет",
                            "price": {"current": 135, "currency": "RUB", "old": None},
                            "unit": "кг",
                        },
                        {
                            "xml_id": 103297,
                            "name": "Молоко 3,2%",
                            "price": {"current": 79, "currency": "RUB", "old": 99},
                            "unit": "шт",
                        },
                    ]
                },
            }
        )

        await processor.cache_prices(search_result)

        assert 41728 in processor.price_cache
        assert processor.price_cache[41728]["price"] == 135
        assert processor.price_cache[41728]["unit"] == "кг"
        assert processor.price_cache[41728]["name"] == "Картофель молодой Египет"

        assert 103297 in processor.price_cache
        assert processor.price_cache[103297]["price"] == 79
        assert processor.price_cache[103297]["unit"] == "шт"

    async def test_handles_invalid_json(self, processor):
        """Не падает на невалидном JSON."""
        await processor.cache_prices("not json")
        assert len(processor.price_cache) == 0

    async def test_handles_empty_items(self, processor):
        """Не падает на пустом списке товаров."""
        await processor.cache_prices(json.dumps({"ok": True, "data": {"items": []}}))
        assert len(processor.price_cache) == 0

    async def test_handles_missing_price(self, processor):
        """Пропускает товары без цены."""
        await processor.cache_prices(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {"xml_id": 100, "name": "Без цены", "price": {}, "unit": "шт"},
                            {
                                "xml_id": 200,
                                "name": "С ценой",
                                "price": {"current": 50},
                                "unit": "шт",
                            },
                        ]
                    },
                }
            )
        )
        assert 100 not in processor.price_cache
        assert 200 in processor.price_cache

    async def test_updates_existing_cache(self, processor):
        """Перезаписывает цены при повторном поиске."""
        processor.price_cache[41728] = {"name": "Старое", "price": 100, "unit": "кг"}

        await processor.cache_prices(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {
                                "xml_id": 41728,
                                "name": "Новое",
                                "price": {"current": 135},
                                "unit": "кг",
                            },
                        ]
                    },
                }
            )
        )

        assert processor.price_cache[41728]["name"] == "Новое"
        assert processor.price_cache[41728]["price"] == 135

    async def test_evicts_when_exceeds_max_size(self, processor):
        """Старые записи удаляются при превышении MAX_PRICE_CACHE_SIZE."""
        # Заполняем кеш до лимита
        for i in range(MAX_PRICE_CACHE_SIZE):
            processor.price_cache[i] = {"name": f"item_{i}", "price": 10, "unit": "шт"}

        assert len(processor.price_cache) == MAX_PRICE_CACHE_SIZE

        # Добавляем ещё товар через cache_prices
        await processor.cache_prices(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {
                                "xml_id": 99999,
                                "name": "Новый",
                                "price": {"current": 100},
                                "unit": "шт",
                            },
                        ]
                    },
                }
            )
        )

        # Кеш уменьшился (половина удалена)
        assert len(processor.price_cache) <= MAX_PRICE_CACHE_SIZE
        # Новый товар сохранён
        assert 99999 in processor.price_cache
        # Старые (первые) удалены
        assert 0 not in processor.price_cache


# ============================================================================
# Обрезка результатов поиска
# ============================================================================


class TestTrimSearchResult:
    """Тесты trim_search_result: обрезка тяжёлых полей из результатов поиска."""

    def test_trims_fields(self, processor):
        """Убирает description, images и прочие лишние поля."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "xml_id": 41728,
                            "name": "Картофель",
                            "price": {"current": 135, "currency": "RUB", "old": 150},
                            "unit": "кг",
                            "weight": "1 кг",
                            "rating": 4.8,
                            "description": "Очень длинное описание товара...",
                            "images": ["https://example.com/img1.jpg"],
                            "slug": "kartoshka",
                            "category": "Овощи",
                        }
                    ]
                },
            }
        )
        result = json.loads(processor.trim_search_result(search_result))
        item = result["data"]["items"][0]

        # Нужные поля остались
        assert item["xml_id"] == 41728
        assert item["name"] == "Картофель"
        assert item["price"] == 135  # price упрощён до current
        assert item["unit"] == "кг"
        assert item["weight"] == "1 кг"
        assert item["rating"] == 4.8

        # Лишние поля удалены
        assert "description" not in item
        assert "images" not in item
        assert "slug" not in item
        assert "category" not in item

    def test_simplifies_price(self, processor):
        """Упрощает price dict до числа (current)."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "xml_id": 1,
                            "name": "Товар",
                            "price": {"current": 99.5, "currency": "RUB", "old": 120},
                            "unit": "шт",
                        }
                    ]
                },
            }
        )
        result = json.loads(processor.trim_search_result(search_result))
        assert result["data"]["items"][0]["price"] == 99.5

    def test_handles_invalid_json(self, processor):
        """Возвращает исходный текст при невалидном JSON."""
        assert processor.trim_search_result("not json") == "not json"

    def test_handles_missing_data(self, processor):
        """Возвращает исходный текст если data — не dict."""
        raw = json.dumps({"ok": True, "data": []})
        assert processor.trim_search_result(raw) == raw

    def test_handles_empty_items(self, processor):
        """Возвращает результат с пустым списком items."""
        raw = json.dumps({"ok": True, "data": {"items": []}})
        result = json.loads(processor.trim_search_result(raw))
        assert result["data"]["items"] == []

    def test_preserves_other_data_fields(self, processor):
        """Сохраняет другие поля в data (total, page и т.д.)."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "total": 42,
                    "page": 1,
                    "items": [
                        {
                            "xml_id": 1,
                            "name": "Товар",
                            "price": {"current": 50},
                            "unit": "шт",
                        }
                    ],
                },
            }
        )
        result = json.loads(processor.trim_search_result(search_result))
        assert result["data"]["total"] == 42
        assert result["data"]["page"] == 1

    def test_multiple_items_trimmed(self, processor):
        """Обрезает все товары, а не только первый."""
        items = [
            {
                "xml_id": i,
                "name": f"Товар {i}",
                "price": {"current": i * 10},
                "unit": "шт",
                "description": f"Длинное описание {i}",
                "images": [f"img{i}.jpg"],
            }
            for i in range(5)
        ]
        search_result = json.dumps({"ok": True, "data": {"items": items}})
        result = json.loads(processor.trim_search_result(search_result))
        assert len(result["data"]["items"]) == 5
        for item in result["data"]["items"]:
            assert "description" not in item
            assert "images" not in item

    def test_removes_non_dict_items(self, processor):
        """trim_search_result пропускает не-dict элементы в items."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        "not-a-dict",
                        42,
                        {"xml_id": 1, "name": "Товар", "price": {"current": 50}, "unit": "шт"},
                        None,
                    ]
                },
            }
        )
        result = json.loads(processor.trim_search_result(search_result))
        # Только dict-элемент остался
        assert len(result["data"]["items"]) == 1
        assert result["data"]["items"][0]["xml_id"] == 1


# ============================================================================
# Извлечение xml_id
# ============================================================================


class TestExtractXmlIds:
    """Тесты extract_xml_ids."""

    def test_extracts_ids(self, processor):
        """Извлекает xml_id из нормального результата."""
        result = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {"xml_id": 100, "name": "Товар 1"},
                        {"xml_id": 200, "name": "Товар 2"},
                    ]
                },
            }
        )
        ids = processor.extract_xml_ids(result)
        assert ids == {100, 200}

    def test_handles_invalid_json(self, processor):
        """Возвращает пустой set при невалидном JSON."""
        assert processor.extract_xml_ids("not json") == set()

    def test_handles_empty_items(self, processor):
        """Возвращает пустой set при пустом списке."""
        result = json.dumps({"ok": True, "data": {"items": []}})
        assert processor.extract_xml_ids(result) == set()

    def test_skips_non_dict_items(self, processor):
        """Пропускает не-dict элементы."""
        result = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        "string_item",
                        42,
                        None,
                        {"xml_id": 100, "name": "Товар"},
                    ]
                },
            }
        )
        assert processor.extract_xml_ids(result) == {100}

    def test_skips_items_without_xml_id(self, processor):
        """Пропускает dict-элементы без xml_id."""
        result = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {"name": "Без ID"},
                        {"xml_id": 200, "name": "С ID"},
                    ]
                },
            }
        )
        assert processor.extract_xml_ids(result) == {200}


# ============================================================================
# parse_search_items (прямые юнит-тесты)
# ============================================================================


class TestParseSearchItems:
    """Тесты parse_search_items: парсинг JSON-ответа поиска."""

    def test_valid_result(self, processor):
        """Корректный результат парсится в (data, items)."""
        raw = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {"xml_id": 1, "name": "Товар 1"},
                        {"xml_id": 2, "name": "Товар 2"},
                    ]
                },
            }
        )
        parsed = processor.parse_search_items(raw)
        assert parsed is not None
        data, items = parsed
        assert isinstance(data, dict)
        assert len(items) == 2
        assert items[0]["xml_id"] == 1

    def test_invalid_json_returns_none(self, processor):
        """Невалидный JSON → None."""
        assert processor.parse_search_items("not json") is None

    def test_none_input_returns_none(self, processor):
        """None на входе → None."""
        assert processor.parse_search_items(None) is None

    def test_no_data_key_returns_none(self, processor):
        """Нет ключа 'data' → None."""
        raw = json.dumps({"ok": True})
        assert processor.parse_search_items(raw) is None

    def test_data_not_dict_returns_none(self, processor):
        """data — не словарь → None."""
        raw = json.dumps({"ok": True, "data": "string"})
        assert processor.parse_search_items(raw) is None

    def test_data_is_list_returns_none(self, processor):
        """data — список → None."""
        raw = json.dumps({"ok": True, "data": [1, 2, 3]})
        assert processor.parse_search_items(raw) is None

    def test_no_items_key_returns_none(self, processor):
        """Нет ключа 'items' в data → None."""
        raw = json.dumps({"ok": True, "data": {"total": 0}})
        assert processor.parse_search_items(raw) is None

    def test_empty_items_returns_none(self, processor):
        """Пустой список items → None."""
        raw = json.dumps({"ok": True, "data": {"items": []}})
        assert processor.parse_search_items(raw) is None

    def test_items_not_list_returns_none(self, processor):
        """items — не список → None."""
        raw = json.dumps({"ok": True, "data": {"items": "not-list"}})
        assert processor.parse_search_items(raw) is None


# ============================================================================
# Обрезка до SEARCH_LIMIT
# ============================================================================


class TestTrimSearchLimit:
    """Тесты обрезки результатов поиска до SEARCH_LIMIT."""

    def test_truncates_to_search_limit(self, processor):
        """Если товаров больше SEARCH_LIMIT — обрезаем."""
        from vkuswill_bot.services.mcp_client import VkusvillMCPClient

        items = [
            {
                "xml_id": i,
                "name": f"Товар {i}",
                "price": {"current": 10 * i},
                "unit": "шт",
            }
            for i in range(20)  # 20 >> SEARCH_LIMIT (5)
        ]
        raw = json.dumps({"ok": True, "data": {"items": items}})
        result = json.loads(processor.trim_search_result(raw))
        assert len(result["data"]["items"]) == VkusvillMCPClient.SEARCH_LIMIT

    def test_fewer_items_than_limit(self, processor):
        """Если товаров меньше SEARCH_LIMIT — все остаются."""
        items = [
            {
                "xml_id": i,
                "name": f"Товар {i}",
                "price": {"current": 10},
                "unit": "шт",
            }
            for i in range(3)
        ]
        raw = json.dumps({"ok": True, "data": {"items": items}})
        result = json.loads(processor.trim_search_result(raw))
        assert len(result["data"]["items"]) == 3


# ============================================================================
# cache_prices: дополнительные edge-cases
# ============================================================================


class TestCachePricesEdgeCases:
    """Дополнительные тесты cache_prices."""

    async def test_missing_xml_id(self, processor):
        """Товар без xml_id не кешируется."""
        await processor.cache_prices(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {"name": "Без ID", "price": {"current": 100}, "unit": "шт"},
                        ]
                    },
                }
            )
        )
        assert len(processor.price_cache) == 0

    async def test_default_unit_sht(self, processor):
        """Если unit не указан — по умолчанию 'шт'."""
        await processor.cache_prices(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {"xml_id": 1, "name": "Товар", "price": {"current": 50}},
                        ]
                    },
                }
            )
        )
        assert processor.price_cache[1]["unit"] == "шт"

    async def test_price_not_dict_skipped(self, processor):
        """Если price — не dict, товар пропускается."""
        await processor.cache_prices(
            json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": [
                            {"xml_id": 1, "name": "Товар", "price": 100, "unit": "шт"},
                        ]
                    },
                }
            )
        )
        # price не dict → get("current") вернёт AttributeError → пропуск
        assert len(processor.price_cache) == 0

    async def test_data_not_dict_ignored(self, processor):
        """JSON без dict-data не крашит cache_prices."""
        await processor.cache_prices(json.dumps({"ok": True, "data": "string"}))
        assert len(processor.price_cache) == 0

    async def test_none_input(self, processor):
        """None на входе не крашит."""
        await processor.cache_prices(None)
        assert len(processor.price_cache) == 0


# ============================================================================
# Очистка поисковых запросов (clean_search_query)
# ============================================================================


class TestCleanSearchQuery:
    """Тесты SearchProcessor.clean_search_query."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Творог 5% 400 гр", "Творог"),
            ("молоко 3,2% 450 мл", "молоко"),
            ("тунец 2 банки", "тунец"),
            ("молоко 4", "молоко"),
            ("мороженое 2", "мороженое"),
            ("темный хлеб", "темный хлеб"),
            ("сок 1 литр", "сок"),
            ("яйца 10 шт", "яйца"),
            ("масло 200 гр сливочное", "масло сливочное"),
        ],
    )
    def test_clean_search_query(self, raw, expected):
        assert SearchProcessor.clean_search_query(raw) == expected

    def test_empty_query_returns_original(self):
        assert SearchProcessor.clean_search_query("") == ""

    def test_query_without_numbers_unchanged(self):
        assert SearchProcessor.clean_search_query("пармезан") == "пармезан"

    def test_search_limit_constant(self):
        """SEARCH_LIMIT экспортируется и равен 5."""
        assert SEARCH_LIMIT == 5


# ============================================================================
# Проверка релевантности (check_relevance)
# ============================================================================


class TestCheckRelevance:
    """Тесты SearchProcessor.check_relevance: проверка соответствия запроса результатам."""

    def test_all_terms_found(self):
        """Все слова запроса найдены в названиях — пустой список."""
        items = [
            {"name": "Стейк говяжий охл."},
            {"name": "Стейк из говядины"},
        ]
        assert SearchProcessor.check_relevance("стейк говяжий", items) == []

    def test_missing_term_detected(self):
        """Слово «вагю» отсутствует в результатах — возвращается в списке."""
        items = [
            {"name": "Форель стейк охл., вес"},
            {"name": "Стейк Мачете из мраморной говядины"},
        ]
        missing = SearchProcessor.check_relevance("стейк вагю", items)
        assert missing == ["вагю"]

    def test_multiple_missing_terms(self):
        """Несколько пропущенных слов."""
        items = [{"name": "Молоко 3,2%"}]
        missing = SearchProcessor.check_relevance("фуа-гра утиная", items)
        assert "фуа-гра" in missing
        assert "утиная" in missing

    def test_case_insensitive(self):
        """Проверка нечувствительна к регистру."""
        items = [{"name": "СТЕЙК ВАГЮ премиум"}]
        assert SearchProcessor.check_relevance("Стейк Вагю", items) == []

    def test_empty_query_returns_empty(self):
        """Пустой запрос → пустой список."""
        items = [{"name": "Товар"}]
        assert SearchProcessor.check_relevance("", items) == []

    def test_empty_items_returns_empty(self):
        """Пустой список товаров → пустой список."""
        assert SearchProcessor.check_relevance("стейк вагю", []) == []

    def test_short_words_ignored(self):
        """Слова короче _MIN_RELEVANCE_WORD_LEN (3) не проверяются."""
        items = [{"name": "Чай зелёный"}]
        # "на" — 2 символа, должно игнорироваться
        assert SearchProcessor.check_relevance("чай на травах", items) == ["травах"]

    def test_stop_words_ignored(self):
        """Русские стоп-слова не проверяются."""
        items = [{"name": "Молоко пастеризованное"}]
        # "без", "для" — стоп-слова
        assert SearchProcessor.check_relevance("молоко без лактозы", items) == ["лактозы"]

    def test_partial_match_in_name(self):
        """Подстрока «форел» найдена в «Форель» — считается совпадением."""
        items = [{"name": "Форель стейк охл."}]
        assert SearchProcessor.check_relevance("форель", items) == []

    def test_non_dict_items_skipped(self):
        """Не-dict элементы в items не ломают проверку."""
        items = ["not_dict", 42, {"name": "Стейк из говядины"}]
        assert SearchProcessor.check_relevance("стейк говядины", items) == []

    def test_items_without_name_skipped(self):
        """Элементы без ключа name обрабатываются корректно."""
        items = [{"xml_id": 1}, {"name": "Товар вагю"}]
        assert SearchProcessor.check_relevance("вагю", items) == []

    def test_real_wagyu_case(self):
        """Реальный кейс: запрос «стейк вагю», результат — форель."""
        items = [
            {"name": "Форель стейк охл., вес"},
            {"name": "Кета стейк охл., вес"},
            {"name": "Стейк Мачете из мраморной говядины"},
            {"name": "Стейк из говядины «Денвер»"},
            {"name": "Стейк из свинины"},
        ]
        missing = SearchProcessor.check_relevance("стейк вагю", items)
        assert missing == ["вагю"]

    def test_all_words_present_in_different_items(self):
        """Каждое слово найдено хотя бы в одном товаре (но в разных)."""
        items = [
            {"name": "Соус устричный"},
            {"name": "Масло оливковое"},
        ]
        # "соус" в первом, "оливковое" во втором
        assert SearchProcessor.check_relevance("соус оливковое", items) == []

    def test_constants_exported(self):
        """Константы _MIN_RELEVANCE_WORD_LEN и _STOP_WORDS экспортируются."""
        assert _MIN_RELEVANCE_WORD_LEN == 3
        assert isinstance(_STOP_WORDS, frozenset)
        assert "без" in _STOP_WORDS
        assert "для" in _STOP_WORDS


# ============================================================================
# Интеграция: relevance_warning в trim_search_result
# ============================================================================


class TestTrimSearchResultRelevance:
    """Тесты: trim_search_result добавляет relevance_warning при несоответствии."""

    def test_adds_warning_when_term_missing(self, processor):
        """Добавляет relevance_warning если слово не найдено в товарах."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "meta": {"q": "стейк вагю", "total": 53},
                    "items": [
                        {
                            "xml_id": 32976,
                            "name": "Форель стейк охл., вес",
                            "price": {"current": 2235},
                            "unit": "кг",
                        },
                    ],
                },
            }
        )
        result = json.loads(processor.trim_search_result(search_result))
        warning = result["data"].get("relevance_warning", "")
        assert "вагю" in warning
        assert "не найдено" in warning

    def test_no_warning_when_all_terms_match(self, processor):
        """Не добавляет warning если все слова запроса найдены."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "meta": {"q": "молоко", "total": 10},
                    "items": [
                        {
                            "xml_id": 1,
                            "name": "Молоко 3,2%",
                            "price": {"current": 79},
                            "unit": "шт",
                        },
                    ],
                },
            }
        )
        result = json.loads(processor.trim_search_result(search_result))
        assert "relevance_warning" not in result["data"]

    def test_no_warning_without_meta(self, processor):
        """Без meta.q — warning не добавляется."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "items": [
                        {
                            "xml_id": 1,
                            "name": "Товар",
                            "price": {"current": 50},
                            "unit": "шт",
                        },
                    ],
                },
            }
        )
        result = json.loads(processor.trim_search_result(search_result))
        assert "relevance_warning" not in result["data"]

    def test_warning_contains_query(self, processor):
        """Warning содержит оригинальный запрос."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "meta": {"q": "фуа-гра", "total": 5},
                    "items": [
                        {
                            "xml_id": 1,
                            "name": "Паштет куриный",
                            "price": {"current": 200},
                            "unit": "шт",
                        },
                    ],
                },
            }
        )
        result = json.loads(processor.trim_search_result(search_result))
        warning = result["data"]["relevance_warning"]
        assert "фуа-гра" in warning
        assert "альтернатив" in warning

    def test_no_warning_on_empty_items(self, processor):
        """Нет warning если items пуст (trim возвращает оригинал)."""
        raw = json.dumps({"ok": True, "data": {"meta": {"q": "вагю"}, "items": []}})
        # parse_search_items вернёт None → trim вернёт оригинал без warning
        result = processor.trim_search_result(raw)
        assert result == raw

    def test_warning_on_exotic_product(self, processor):
        """Проверка: экзотический товар «трюфель белый» → warning на «белый»."""
        search_result = json.dumps(
            {
                "ok": True,
                "data": {
                    "meta": {"q": "трюфель белый", "total": 20},
                    "items": [
                        {
                            "xml_id": 1,
                            "name": "Масло трюфельное",
                            "price": {"current": 500},
                            "unit": "шт",
                        },
                        {
                            "xml_id": 2,
                            "name": "Чипсы со вкусом трюфеля",
                            "price": {"current": 150},
                            "unit": "шт",
                        },
                    ],
                },
            }
        )
        result = json.loads(processor.trim_search_result(search_result))
        warning = result["data"].get("relevance_warning", "")
        assert "белый" in warning
