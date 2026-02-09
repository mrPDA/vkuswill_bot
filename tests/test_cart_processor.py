"""Тесты CartProcessor.

Тестируем:
- Дополнение схемы корзины описаниями параметров
- Округление q до целого для штучных товаров
- Расчёт стоимости корзины
- Верификацию корзины (сопоставление с поисковыми запросами)
"""

import json

import pytest

from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.price_cache import PriceCache


# ============================================================================
# Фикстуры
# ============================================================================


@pytest.fixture
def price_cache() -> PriceCache:
    """Общий кеш цен."""
    return PriceCache()


@pytest.fixture
def processor(price_cache) -> CartProcessor:
    """Экземпляр CartProcessor с кешем цен."""
    return CartProcessor(price_cache)


# ============================================================================
# Дополнение схемы корзины
# ============================================================================


class TestEnhanceCartSchema:
    """Тесты enhance_cart_schema: добавление описаний к параметрам корзины."""

    def test_adds_description_to_q(self):
        """Добавляет description к q и делает его required."""
        schema = {
            "properties": {
                "products": {
                    "type": "array",
                    "items": {
                        "properties": {
                            "xml_id": {"type": "integer"},
                            "q": {"type": "number", "format": "float"},
                        },
                        "required": ["xml_id"],
                    },
                }
            },
            "required": ["products"],
        }
        result = CartProcessor.enhance_cart_schema(schema)
        items = result["properties"]["products"]["items"]
        assert "description" in items["properties"]["q"]
        assert "ДРОБНОЕ" in items["properties"]["q"]["description"]
        assert "q" in items["required"]

    def test_does_not_mutate_original(self):
        """Не мутирует оригинальную схему."""
        schema = {
            "properties": {
                "products": {
                    "type": "array",
                    "items": {
                        "properties": {
                            "q": {"type": "number"},
                        },
                        "required": ["xml_id"],
                    },
                }
            },
        }
        CartProcessor.enhance_cart_schema(schema)
        assert "description" not in schema["properties"]["products"]["items"]["properties"]["q"]

    def test_handles_empty_schema(self):
        """Не падает на пустой схеме."""
        result = CartProcessor.enhance_cart_schema({})
        assert result == {}


# ============================================================================
# Округление q
# ============================================================================


class TestFixUnitQuantities:
    """Тесты fix_unit_quantities: округление q для штучных товаров."""

    async def test_rounds_up_for_sht(self, processor, price_cache):
        """Округляет q вверх для товаров в штуках."""
        price_cache[100] = {"name": "Огурцы", "price": 166, "unit": "шт"}
        args = {"products": [{"xml_id": 100, "q": 0.68}]}
        result = await processor.fix_unit_quantities(args)
        assert result["products"][0]["q"] == 1

    async def test_rounds_up_for_up(self, processor, price_cache):
        """Округляет q вверх для товаров в упаковках."""
        price_cache[200] = {"name": "Паста", "price": 90, "unit": "уп"}
        args = {"products": [{"xml_id": 200, "q": 1.3}]}
        result = await processor.fix_unit_quantities(args)
        assert result["products"][0]["q"] == 2

    async def test_preserves_fractional_for_kg(self, processor, price_cache):
        """НЕ округляет для товаров в кг."""
        price_cache[300] = {"name": "Картофель", "price": 135, "unit": "кг"}
        args = {"products": [{"xml_id": 300, "q": 1.5}]}
        result = await processor.fix_unit_quantities(args)
        assert result["products"][0]["q"] == 1.5

    async def test_preserves_integer_for_sht(self, processor, price_cache):
        """Не изменяет уже целые значения для штучных."""
        price_cache[400] = {"name": "Молоко", "price": 79, "unit": "шт"}
        args = {"products": [{"xml_id": 400, "q": 3}]}
        result = await processor.fix_unit_quantities(args)
        assert result["products"][0]["q"] == 3

    async def test_no_cache_entry(self, processor):
        """Не трогает товары, которых нет в кеше."""
        args = {"products": [{"xml_id": 999, "q": 0.5}]}
        result = await processor.fix_unit_quantities(args)
        assert result["products"][0]["q"] == 0.5

    async def test_empty_products(self, processor):
        """Обрабатывает пустые аргументы."""
        args = {"products": []}
        result = await processor.fix_unit_quantities(args)
        assert result["products"] == []

    async def test_multiple_products_mixed(self, processor, price_cache):
        """Корректно обрабатывает смешанный набор товаров."""
        price_cache[100] = {"name": "Огурцы", "price": 166, "unit": "шт"}
        price_cache[200] = {"name": "Картофель", "price": 135, "unit": "кг"}
        price_cache[300] = {"name": "Сметана", "price": 158, "unit": "шт"}
        args = {
            "products": [
                {"xml_id": 100, "q": 0.68},
                {"xml_id": 200, "q": 1.5},
                {"xml_id": 300, "q": 0.3},
            ]
        }
        result = await processor.fix_unit_quantities(args)
        assert result["products"][0]["q"] == 1    # шт → округлено
        assert result["products"][1]["q"] == 1.5  # кг → не тронуто
        assert result["products"][2]["q"] == 1    # шт → округлено


# ============================================================================
# Расчёт стоимости корзины
# ============================================================================


class TestCalcCartTotal:
    """Тесты calc_total: расчёт стоимости корзины."""

    async def test_calculates_total(self, processor, price_cache):
        """Считает стоимость по кешу цен."""
        price_cache[41728] = {"name": "Картофель", "price": 135, "unit": "кг"}
        price_cache[103297] = {"name": "Молоко", "price": 79, "unit": "шт"}
        args = {
            "products": [
                {"xml_id": 41728, "q": 1.5},
                {"xml_id": 103297, "q": 4},
            ]
        }
        result_text = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })

        result = await processor.calc_total(args, result_text)
        parsed = json.loads(result)

        assert "price_summary" in parsed["data"]
        summary = parsed["data"]["price_summary"]
        # 135 * 1.5 + 79 * 4 = 202.5 + 316 = 518.5
        assert summary["total"] == 518.5
        assert "518.50" in summary["total_text"]
        assert len(summary["items"]) == 2

    async def test_fractional_quantity(self, processor, price_cache):
        """Корректно считает дробные количества."""
        price_cache[41728] = {"name": "Картофель", "price": 135, "unit": "кг"}
        args = {"products": [{"xml_id": 41728, "q": 0.5}]}
        result_text = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=456"},
        })

        result = await processor.calc_total(args, result_text)
        parsed = json.loads(result)
        # 135 * 0.5 = 67.5
        assert parsed["data"]["price_summary"]["total"] == 67.5

    async def test_unknown_price(self, processor):
        """Если цена неизвестна — total не вычисляется."""
        args = {"products": [{"xml_id": 999, "q": 1}]}
        result_text = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=789"},
        })

        result = await processor.calc_total(args, result_text)
        parsed = json.loads(result)
        summary = parsed["data"]["price_summary"]
        assert "total" not in summary
        assert "не удалось" in summary["total_text"]

    async def test_partial_unknown_prices(self, processor, price_cache):
        """Если часть цен неизвестна — total не вычисляется."""
        price_cache[41728] = {"name": "Картофель", "price": 135, "unit": "кг"}
        args = {
            "products": [
                {"xml_id": 41728, "q": 1},
                {"xml_id": 999, "q": 1},
            ]
        }
        result_text = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=101"},
        })

        result = await processor.calc_total(args, result_text)
        parsed = json.loads(result)
        assert "total" not in parsed["data"]["price_summary"]

    async def test_error_result_passthrough(self, processor):
        """Если результат — ошибка, возвращаем как есть."""
        args = {"products": [{"xml_id": 1, "q": 1}]}
        result_text = json.dumps({"ok": False, "error": "invalid"})

        result = await processor.calc_total(args, result_text)
        assert result == result_text

    async def test_invalid_json_passthrough(self, processor):
        """Невалидный JSON — возвращаем как есть."""
        result = await processor.calc_total({}, "not json")
        assert result == "not json"

    async def test_empty_products(self, processor):
        """Пустой список продуктов — не модифицируем результат."""
        result_text = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=000"},
        })
        result = await processor.calc_total({"products": []}, result_text)
        assert result == result_text

    async def test_default_q_is_one(self, processor, price_cache):
        """Если q не указан — используется 1."""
        price_cache[50] = {"name": "Товар", "price": 200, "unit": "шт"}
        args = {"products": [{"xml_id": 50}]}
        result_text = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=222"},
        })

        result = await processor.calc_total(args, result_text)
        parsed = json.loads(result)
        assert parsed["data"]["price_summary"]["total"] == 200.0

    async def test_missing_data_key(self, processor, price_cache):
        """Ответ {"ok": true} без "data" — не падает, возвращает оригинал."""
        price_cache[50] = {"name": "Товар", "price": 200, "unit": "шт"}
        args = {"products": [{"xml_id": 50}]}
        result_text = json.dumps({"ok": True})

        result = await processor.calc_total(args, result_text)
        # Возвращён оригинальный текст без изменений
        assert result == result_text

    async def test_data_not_dict(self, processor, price_cache):
        """Если data — не словарь, не падает."""
        price_cache[50] = {"name": "Товар", "price": 200, "unit": "шт"}
        args = {"products": [{"xml_id": 50}]}
        result_text = json.dumps({"ok": True, "data": "just a string"})

        result = await processor.calc_total(args, result_text)
        assert result == result_text


# ============================================================================
# Верификация корзины
# ============================================================================


class TestVerifyCart:
    """Тесты verify_cart: сопоставление корзины с поисковыми запросами."""

    async def test_all_matched(self, processor, price_cache):
        """Все товары в корзине соответствуют поискам — всё ok."""
        price_cache[100] = {"name": "Молоко", "price": 79, "unit": "шт"}
        price_cache[200] = {"name": "Хлеб", "price": 50, "unit": "шт"}
        search_log = {
            "молоко": {100, 101},
            "хлеб": {200, 201},
        }
        args = {"products": [{"xml_id": 100, "q": 4}, {"xml_id": 200, "q": 1}]}

        report = await processor.verify_cart(args, search_log)

        assert report.get("ok") is True
        assert len(report["matched"]) == 2
        assert report["missing_queries"] == []
        assert report["unmatched_items"] == []

    async def test_missing_query(self, processor, price_cache):
        """Поиск \"вареники\" выполнен, но в корзине нет товара из этого поиска."""
        price_cache[100] = {"name": "Молоко", "price": 79, "unit": "шт"}
        search_log = {
            "молоко": {100},
            "вареники": {300, 301},
        }
        args = {"products": [{"xml_id": 100, "q": 4}]}

        report = await processor.verify_cart(args, search_log)

        assert "ok" not in report
        assert "вареники" in report["missing_queries"]
        assert "issues" in report
        assert "action_required" in report
        assert any("вареники" in issue for issue in report["issues"])

    async def test_unmatched_item(self, processor, price_cache):
        """Товар в корзине не найден ни в одном поиске."""
        price_cache[100] = {"name": "Молоко", "price": 79, "unit": "шт"}
        price_cache[999] = {"name": "Непонятный товар", "price": 50, "unit": "шт"}
        search_log = {
            "молоко": {100},
        }
        args = {
            "products": [
                {"xml_id": 100, "q": 4},
                {"xml_id": 999, "q": 1},
            ]
        }

        report = await processor.verify_cart(args, search_log)

        assert "ok" not in report
        assert len(report["unmatched_items"]) == 1
        assert report["unmatched_items"][0]["name"] == "Непонятный товар"

    async def test_real_case_milk_vs_icecream(self, processor, price_cache):
        """Реальный кейс: молоко заменено мороженым, вареники пропущены."""
        price_cache[100] = {"name": "Творог 5%", "price": 198, "unit": "шт"}
        price_cache[200] = {"name": "Хлеб дворянский", "price": 117, "unit": "шт"}
        price_cache[300] = {"name": "Тунец филе", "price": 367, "unit": "шт"}
        price_cache[400] = {"name": "Эскимо пломбир", "price": 122, "unit": "шт"}
        price_cache[500] = {"name": "Хлеб Стройный рецепт", "price": 79, "unit": "шт"}
        search_log = {
            "творог": {100},
            "хлеб темный": {200, 500},
            "тунец": {300},
            "молоко": {600, 601},       # молоко НЕ в корзине!
            "мороженое": {400, 402},     # мороженое присвоено неверно
            "вареники": {700, 701},      # вареники НЕ в корзине!
        }
        args = {
            "products": [
                {"xml_id": 100, "q": 1},  # творог — ок
                {"xml_id": 200, "q": 1},  # хлеб — ок
                {"xml_id": 300, "q": 2},  # тунец — ок
                {"xml_id": 400, "q": 4},  # эскимо — из поиска "мороженое"
                {"xml_id": 500, "q": 2},  # второй хлеб — из поиска "хлеб"
            ]
        }

        report = await processor.verify_cart(args, search_log)

        # Молоко и вареники пропущены
        assert "молоко" in report["missing_queries"]
        assert "вареники" in report["missing_queries"]
        assert "action_required" in report
        assert len(report["issues"]) >= 2

    async def test_empty_search_log(self, processor, price_cache):
        """Пустой лог поисков — все товары не опознаны."""
        price_cache[100] = {"name": "Товар", "price": 50, "unit": "шт"}
        args = {"products": [{"xml_id": 100, "q": 1}]}

        report = await processor.verify_cart(args, {})

        assert len(report["unmatched_items"]) == 1

    async def test_empty_cart(self, processor):
        """Пустая корзина — все запросы пропущены."""
        search_log = {"молоко": {100}, "хлеб": {200}}
        args = {"products": []}

        report = await processor.verify_cart(args, search_log)

        assert "молоко" in report["missing_queries"]
        assert "хлеб" in report["missing_queries"]

    async def test_item_not_in_cache(self, processor):
        """xml_id без записи в кеше — показывает xml_id в имени."""
        search_log: dict[str, set[int]] = {}
        args = {"products": [{"xml_id": 777, "q": 1}]}

        report = await processor.verify_cart(args, search_log)

        assert len(report["unmatched_items"]) == 1
        assert "xml_id=777" in report["unmatched_items"][0]["name"]

    async def test_none_xml_id_skipped(self, processor):
        """xml_id=None пропускается при сборе id корзины."""
        args = {"products": [{"xml_id": None, "q": 1}]}
        report = await processor.verify_cart(args, {})
        assert len(report["matched"]) == 0
        assert len(report["unmatched_items"]) == 0


# ============================================================================
# fix_unit_quantities: дополнительные edge-cases
# ============================================================================


class TestFixUnitQuantitiesEdgeCases:
    """Дополнительные тесты fix_unit_quantities."""

    async def test_non_dict_items_skipped(self, processor, price_cache):
        """Не-dict элементы в products пропускаются (не крашат)."""
        price_cache[100] = {"name": "Товар", "price": 50, "unit": "шт"}
        args = {
            "products": [
                "not-a-dict",
                42,
                None,
                {"xml_id": 100, "q": 0.5},
            ]
        }
        result = await processor.fix_unit_quantities(args)
        # Последний элемент округлён
        assert result["products"][3]["q"] == 1

    async def test_no_products_key(self, processor):
        """Без ключа 'products' — возвращаем args как есть."""
        args = {"something": "else"}
        result = await processor.fix_unit_quantities(args)
        assert result == args

    async def test_products_not_list(self, processor):
        """products — не список → возвращаем без изменений."""
        args = {"products": "not-a-list"}
        result = await processor.fix_unit_quantities(args)
        assert result["products"] == "not-a-list"

    @pytest.mark.parametrize("unit", ["шт", "уп", "пач", "бут", "бан", "пак"])
    async def test_all_discrete_units(self, processor, price_cache, unit):
        """Все дискретные единицы округляются вверх."""
        price_cache[100] = {"name": "Товар", "price": 50, "unit": unit}
        args = {"products": [{"xml_id": 100, "q": 0.3}]}
        result = await processor.fix_unit_quantities(args)
        assert result["products"][0]["q"] == 1

    async def test_default_unit_sht_rounds(self, processor, price_cache):
        """Если unit не указан в кеше — по умолчанию 'шт', округляется."""
        price_cache[100] = {"name": "Товар", "price": 50, "unit": "шт"}
        args = {"products": [{"xml_id": 100, "q": 1.1}]}
        result = await processor.fix_unit_quantities(args)
        assert result["products"][0]["q"] == 2

    async def test_missing_q_defaults_to_one(self, processor, price_cache):
        """Если q отсутствует — по умолчанию 1, целое, не округляется."""
        price_cache[100] = {"name": "Товар", "price": 50, "unit": "шт"}
        args = {"products": [{"xml_id": 100}]}
        result = await processor.fix_unit_quantities(args)
        assert result["products"][0].get("q", 1) == 1


# ============================================================================
# add_verification
# ============================================================================


class TestAddVerification:
    """Тесты add_verification: добавление отчёта верификации в результат."""

    async def test_adds_verification_to_result(self, processor, price_cache):
        """Добавляет verification в data результата."""
        price_cache[100] = {"name": "Молоко", "price": 79, "unit": "шт"}
        args = {"products": [{"xml_id": 100, "q": 1}]}
        search_log = {"молоко": {100}}
        result_text = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })

        result = await processor.add_verification(args, result_text, search_log)
        parsed = json.loads(result)

        assert "verification" in parsed["data"]
        assert parsed["data"]["verification"]["ok"] is True

    async def test_verification_with_missing_query(self, processor, price_cache):
        """Verification показывает missing_queries при пропущенном товаре."""
        price_cache[100] = {"name": "Молоко", "price": 79, "unit": "шт"}
        args = {"products": [{"xml_id": 100, "q": 1}]}
        search_log = {"молоко": {100}, "хлеб": {200}}
        result_text = json.dumps({
            "ok": True,
            "data": {"link": "https://vkusvill.ru/?share_basket=123"},
        })

        result = await processor.add_verification(args, result_text, search_log)
        parsed = json.loads(result)

        assert "хлеб" in parsed["data"]["verification"]["missing_queries"]

    async def test_invalid_json_passthrough(self, processor):
        """Невалидный JSON — возвращаем как есть."""
        result = await processor.add_verification(
            {"products": []}, "not json", {}
        )
        assert result == "not json"

    async def test_data_not_dict_passthrough(self, processor):
        """data — не dict → возвращаем оригинал."""
        result_text = json.dumps({"ok": True, "data": "string"})
        result = await processor.add_verification(
            {"products": []}, result_text, {}
        )
        assert result == result_text

    async def test_none_result_text(self, processor):
        """None вместо текста — не крашит."""
        result = await processor.add_verification(
            {"products": []}, None, {}
        )
        assert result is None
