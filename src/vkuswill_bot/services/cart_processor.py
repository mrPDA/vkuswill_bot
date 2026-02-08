"""Расчёт и верификация корзины ВкусВилл."""

import copy
import json
import logging
import math

from vkuswill_bot.services.price_cache import PriceCache

logger = logging.getLogger(__name__)


class CartProcessor:
    """Расчёт стоимости, округление количеств и верификация корзины.

    Использует PriceCache для расчёта стоимости и валидации товаров.
    """

    # Единицы измерения, для которых q должно быть целым числом
    _DISCRETE_UNITS = frozenset({"шт", "уп", "пач", "бут", "бан", "пак"})

    def __init__(self, price_cache: PriceCache) -> None:
        self._price_cache = price_cache

    @staticmethod
    def enhance_cart_schema(params: dict) -> dict:
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

    @staticmethod
    def fix_cart_args(arguments: dict) -> dict:
        """Исправить аргументы корзины.

        1. Добавить q=1, если GigaChat забыл указать количество.
        2. Объединить дубли xml_id (суммировать q).

        GigaChat иногда дублирует xml_id вместо использования q,
        например [{xml_id:1},{xml_id:1},{xml_id:1}] вместо [{xml_id:1,q:3}].
        VkusVill API дедуплицирует по xml_id и берёт q=1,
        поэтому объединяем на нашей стороне.
        """
        products = arguments.get("products")
        if not products or not isinstance(products, list):
            return arguments

        # Шаг 1: добавить q=1 где отсутствует
        for item in products:
            if isinstance(item, dict) and "q" not in item:
                item["q"] = 1

        # Шаг 2: объединить дубли xml_id
        merged: dict[int, float] = {}
        order: list[int] = []
        for item in products:
            if not isinstance(item, dict):
                continue
            xml_id = item.get("xml_id")
            if xml_id is None:
                continue
            q = item.get("q", 1)
            if xml_id in merged:
                merged[xml_id] += q
            else:
                merged[xml_id] = q
                order.append(xml_id)

        if merged:
            arguments["products"] = [
                {"xml_id": xid, "q": merged[xid]} for xid in order
            ]

        return arguments

    def fix_unit_quantities(self, args: dict) -> dict:
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
            if cached and cached.unit in self._DISCRETE_UNITS:
                rounded = math.ceil(q)
                if rounded != q:
                    logger.info(
                        "Округление q: xml_id=%s, unit=%s, %s → %s",
                        xml_id, cached.unit, q, rounded,
                    )
                    item["q"] = rounded
                    changed = True

        if changed:
            logger.info("Исправленные аргументы корзины: %s", args)

        return args

    def calc_total(self, args: dict, result_text: str) -> str:
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
                subtotal = cached.price * q
                total += subtotal
                lines.append(
                    f"  - {cached.name}: {cached.price} руб/{cached.unit}"
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

    def verify_cart(
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
            name = cached.name if cached else f"xml_id={xml_id}"
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

    def add_verification(
        self,
        args: dict,
        result: str,
        search_log: dict[str, set[int]],
    ) -> str:
        """Добавить отчёт верификации корзины в результат.

        Сопоставляет содержимое корзины с поисковыми запросами
        и добавляет поле verification в data.
        """
        verification = self.verify_cart(args, search_log)
        try:
            result_data = json.loads(result)
            data = result_data.get("data")
            if isinstance(data, dict):
                data["verification"] = verification
                return json.dumps(
                    result_data, ensure_ascii=False, indent=4,
                )
        except (json.JSONDecodeError, TypeError):
            pass
        return result
