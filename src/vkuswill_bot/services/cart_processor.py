"""Расчёт и верификация корзины ВкусВилл."""

import copy
import json
import logging
import math
import re

from vkuswill_bot.services.price_cache import PriceCache

logger = logging.getLogger(__name__)

# Минимум совпадающих слов в названиях для определения дубля
_MIN_NAME_OVERLAP = 2

# Минимальная длина слова для сравнения названий
_MIN_WORD_LEN = 3


class CartProcessor:
    """Расчёт стоимости, округление количеств и верификация корзины.

    Использует PriceCache для расчёта стоимости и валидации товаров.
    """

    # Единицы измерения, для которых q должно быть целым числом
    _DISCRETE_UNITS = frozenset({"шт", "уп", "пач", "бут", "бан", "пак"})

    # Ключевые слова для определения яиц (продаются упаковками по 10)
    _EGG_KEYWORDS = ("яйц", "яйко")

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
        items_schema = params.get("properties", {}).get("products", {}).get("items", {})
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
            items_schema["required"] = [*list(required), "q"]

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
            arguments["products"] = [{"xml_id": xid, "q": merged[xid]} for xid in order]

        return arguments

    # Максимальное q, поддерживаемое VkusVill API
    _MAX_Q_API = 40

    # Макс. разумное q для штучных товаров (для покупок на дом)
    _MAX_Q_DISCRETE = 10

    async def fix_unit_quantities(self, args: dict) -> dict:
        """Округлить q до целого для штучных товаров и ограничить max q.

        GigaChat иногда:
        - Ставит дробное q для штучных товаров (0.68 для банки огурцов)
        - Путает граммы рецепта с количеством штук (60 г сахара → q=60)

        Для штучных товаров: округляем вверх + cap до _MAX_Q_DISCRETE.
        Для весовых товаров: cap до _MAX_Q_API (40 кг).

        Корректировки записываются в ``args["_quantity_adjustments"]``.
        """
        products = args.get("products")
        if not products or not isinstance(products, list):
            return args

        adjustments: list[str] = []
        for item in products:
            if not isinstance(item, dict):
                continue
            xml_id = item.get("xml_id")
            q = item.get("q", 1)
            cached = await self._price_cache.get(xml_id)

            if cached and cached.unit in self._DISCRETE_UNITS:
                # Яйца: 1 шт = 1 упаковка (10 яиц).
                # Если q > 1 и это яйца — скорее всего GigaChat
                # перепутал количество яиц (шт) с количеством упаковок
                name_lower = cached.name.lower()
                is_egg = any(kw in name_lower for kw in self._EGG_KEYWORDS)
                if is_egg and q > 1:
                    old_q = q
                    q = 1
                    logger.info(
                        "Яйца: q=%s → %s (1 упаковка = 10 яиц, хватит для рецепта)",
                        old_q,
                        q,
                    )
                    adjustments.append(
                        f"{cached.name}: {old_q} → {q} {cached.unit} "
                        f"(1 упаковка = 10 яиц, для рецепта достаточно)"
                    )
                    item["q"] = q
                    continue

                # Округление дробного q для штучных товаров
                rounded = math.ceil(q)
                if rounded != q:
                    logger.info(
                        "Округление q: xml_id=%s, unit=%s, %s → %s",
                        xml_id,
                        cached.unit,
                        q,
                        rounded,
                    )
                    adjustments.append(
                        f"{cached.name}: {q} → {rounded} {cached.unit} "
                        f"(товар продаётся поштучно, дробное количество невозможно)"
                    )
                    q = rounded

                # Cap для штучных: q > _MAX_Q_DISCRETE — скорее всего
                # GigaChat перепутал граммы рецепта с количеством штук
                if q > self._MAX_Q_DISCRETE:
                    old_q = q
                    q = min(q, self._MAX_Q_DISCRETE)
                    logger.warning(
                        "Слишком большое q для штучного товара: xml_id=%s (%s), %s → %s %s",
                        xml_id,
                        cached.name,
                        old_q,
                        q,
                        cached.unit,
                    )
                    adjustments.append(
                        f"{cached.name}: {old_q} → {q} {cached.unit} "
                        f"(количество ограничено до {self._MAX_Q_DISCRETE} шт — "
                        f"скорее всего ты перепутал граммы рецепта с количеством "
                        f"штук товара; 1 шт = упаковка товара)"
                    )

                item["q"] = q

            elif q > self._MAX_Q_API:
                # Cap для весовых товаров (API лимит)
                old_q = q
                item["q"] = self._MAX_Q_API
                name = cached.name if cached else f"xml_id={xml_id}"
                unit = cached.unit if cached else "?"
                logger.warning(
                    "q превышает API лимит: xml_id=%s, %s → %s %s",
                    xml_id,
                    old_q,
                    self._MAX_Q_API,
                    unit,
                )
                adjustments.append(
                    f"{name}: {old_q} → {self._MAX_Q_API} {unit} (ограничено до максимума API)"
                )

        if adjustments:
            logger.info("Исправленные аргументы корзины: %s", args)
            args["_quantity_adjustments"] = adjustments

        return args

    async def calc_total(self, args: dict, result_text: str) -> str:
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
            cached = await self._price_cache.get(xml_id)
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
            summary["total_text"] = "Итого: не удалось рассчитать (не все цены известны)"

        data = result_data.get("data")
        if not isinstance(data, dict):
            logger.warning(
                "Результат корзины без поля 'data': %s",
                result_text[:200],
            )
            return result_text
        data["price_summary"] = summary
        return json.dumps(result_data, ensure_ascii=False, indent=4)

    async def verify_cart(
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
            cached = await self._price_cache.get(xml_id)
            name = cached.name if cached else f"xml_id={xml_id}"
            queries = xml_to_queries.get(xml_id, [])
            if queries:
                matched.append(
                    {
                        "query": queries[0],
                        "name": name,
                        "xml_id": xml_id,
                    }
                )
                queries_with_match.update(queries)
            else:
                unmatched_items.append({"name": name, "xml_id": xml_id})

        # Запросы, по которым ничего не попало в корзину
        missing_queries = [q for q in search_log if q not in queries_with_match]

        report: dict = {
            "matched": matched,
            "missing_queries": missing_queries,
            "unmatched_items": unmatched_items,
        }

        if missing_queries or unmatched_items:
            issues = []
            for q in missing_queries:
                issues.append(f'Поиск "{q}" не имеет соответствия в корзине — товар пропущен!')
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

    async def detect_similar_items(
        self,
        args: dict,
    ) -> list[tuple[str, str]]:
        """Обнаружить похожие товары в корзине.

        Сравнивает названия товаров попарно. Если два товара имеют
        >= _MIN_NAME_OVERLAP общих значимых слов — считаем их похожими.

        Пример: «Форель радужная стейк охл.» и «Форель радужная стейк зам.»
        имеют 3 общих слова → дубль.

        Returns:
            Список пар (name1, name2) похожих товаров.
        """
        products = args.get("products", [])
        if len(products) < 2:
            return []

        # Собираем (xml_id, name, значимые_слова)
        items_info: list[tuple[int, str, frozenset[str]]] = []
        for item in products:
            if not isinstance(item, dict):
                continue
            xml_id = item.get("xml_id")
            cached = await self._price_cache.get(xml_id)
            if cached:
                name = cached.name
                words = frozenset(
                    w for w in re.findall(r"\w+", name.lower()) if len(w) >= _MIN_WORD_LEN
                )
                items_info.append((xml_id, name, words))

        # Попарное сравнение
        duplicates: list[tuple[str, str]] = []
        for i in range(len(items_info)):
            for j in range(i + 1, len(items_info)):
                _, name1, words1 = items_info[i]
                _, name2, words2 = items_info[j]
                overlap = words1 & words2
                if len(overlap) >= _MIN_NAME_OVERLAP:
                    duplicates.append((name1, name2))

        return duplicates

    async def add_duplicate_warning(
        self,
        args: dict,
        result: str,
    ) -> str:
        """Добавить предупреждение о похожих товарах в результат корзины.

        Если в корзине обнаружены товары с похожими названиями
        (например, охлаждённый и замороженный стейк форели),
        добавляет поле ``duplicate_warning`` в data.
        """
        duplicates = await self.detect_similar_items(args)
        if not duplicates:
            return result

        try:
            result_data = json.loads(result)
            data = result_data.get("data")
            if isinstance(data, dict):
                pairs = [f"«{n1}» и «{n2}»" for n1, n2 in duplicates]
                data["duplicate_warning"] = (
                    "В корзине обнаружены похожие товары: " + "; ".join(pairs) + ". "
                    "Возможно, это дубли одной позиции. "
                    "Проверь и оставь только ОДИН вариант."
                )
                logger.warning("Дубли в корзине: %s", pairs)
                return json.dumps(
                    result_data,
                    ensure_ascii=False,
                    indent=4,
                )
        except (json.JSONDecodeError, TypeError):
            pass
        return result

    async def add_verification(
        self,
        args: dict,
        result: str,
        search_log: dict[str, set[int]],
    ) -> str:
        """Добавить отчёт верификации корзины в результат.

        Сопоставляет содержимое корзины с поисковыми запросами
        и добавляет поле verification в data.
        """
        verification = await self.verify_cart(args, search_log)
        try:
            result_data = json.loads(result)
            data = result_data.get("data")
            if isinstance(data, dict):
                data["verification"] = verification
                return json.dumps(
                    result_data,
                    ensure_ascii=False,
                    indent=4,
                )
        except (json.JSONDecodeError, TypeError):
            pass
        return result
