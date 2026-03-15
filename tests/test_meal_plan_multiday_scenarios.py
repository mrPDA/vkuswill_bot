"""
Тест-планы и тесты: заказ продуктов на несколько дней.

Матрица покрытия:
─────────────────────────────────────────────────
  Ось                  Варианты
─────────────────────────────────────────────────
  Дни                  1, 3, 5, 7, 10, 14, >14
  Люди                 1, 2, 4, 10, 20
  Группы               все-одинаковые, взрослые+дети,
                       сегментированные (веган+обычный),
                       взрослые+дети+сегментированные
  Диеты                без, vegan, vegetarian, halal
  Аллергены            без, один, несколько
  Кухни                без, одна, несколько
  Дети                 без, 1 (возраст <= 3), 1 (возраст > 3),
                       несколько
─────────────────────────────────────────────────
"""

from __future__ import annotations

import pytest

from vkuswill_bot.agents.meal_plan_types import (
    MealPlanDish,
    MealPlanRequest,
    parse_meal_plan_request,
)
from vkuswill_bot.agents.meal_plan_quality import (
    calculate_soft_coverage,
    validate_hard_constraints,
)
from vkuswill_bot.agents.meal_plan_generator import _validate_meal_plan_payload

EMPTY_PROFILE: dict = {}
VEG_PROFILE: dict = {
    "hard_constraints": {"diet": "vegetarian"},
    "soft_preferences": {"cuisines": ["russian"]},
    "operational_preferences": {},
}


# ═══════════════════════════════════════════════════
# TP-01: Парсинг количества дней
# ═══════════════════════════════════════════════════


class TestDaysParsing:
    """TP-01: корректный парсинг количества дней из пользовательского текста."""

    def test_explicit_week(self) -> None:
        r = parse_meal_plan_request("Рацион на неделю для 2 человек", EMPTY_PROFILE)
        assert r.days == 7

    def test_explicit_3_days(self) -> None:
        r = parse_meal_plan_request("Собери меню на 3 дня для 2 человек", EMPTY_PROFILE)
        assert r.days == 3

    def test_explicit_1_day(self) -> None:
        r = parse_meal_plan_request("Собери продукты на 1 день для 2 человек", EMPTY_PROFILE)
        assert r.days == 1

    def test_explicit_5_days(self) -> None:
        r = parse_meal_plan_request("Меню на 5 дней для 2 человек", EMPTY_PROFILE)
        assert r.days == 5

    def test_explicit_10_days(self) -> None:
        r = parse_meal_plan_request("Рацион на 10 дней для 2 человек", EMPTY_PROFILE)
        assert r.days == 10

    def test_explicit_14_days_max(self) -> None:
        r = parse_meal_plan_request("Меню на 14 дней для 2 человек", EMPTY_PROFILE)
        assert r.days == 14

    def test_over_14_clamped(self) -> None:
        r = parse_meal_plan_request("Собери рацион на 30 дней для 2 человек", EMPTY_PROFILE)
        assert r.days == 14

    def test_no_days_defaults_to_7(self) -> None:
        r = parse_meal_plan_request("Собери продукты для 3 человек", EMPTY_PROFILE)
        assert r.days == 7

    def test_zero_days_clamped_to_1(self) -> None:
        r = parse_meal_plan_request("Рацион на 0 дней для 2 человек", EMPTY_PROFILE)
        assert r.days == 1


# ═══════════════════════════════════════════════════
# TP-02: Парсинг количества людей
# ═══════════════════════════════════════════════════


class TestPeopleParsing:
    """TP-02: корректный парсинг количества людей."""

    def test_1_person(self) -> None:
        r = parse_meal_plan_request("Меню на неделю для 1 человека", EMPTY_PROFILE)
        assert r.people_total == 1

    def test_4_people(self) -> None:
        r = parse_meal_plan_request("Меню на неделю для 4 человек", EMPTY_PROFILE)
        assert r.people_total == 4

    def test_20_people_max(self) -> None:
        r = parse_meal_plan_request("Рацион на неделю для 20 человек", EMPTY_PROFILE)
        assert r.people_total == 20

    def test_over_20_clamped(self) -> None:
        r = parse_meal_plan_request("Корзина на неделю для 50 человек", EMPTY_PROFILE)
        assert r.people_total == 20

    def test_no_people_defaults_to_2(self) -> None:
        r = parse_meal_plan_request("Собери рацион на неделю", EMPTY_PROFILE)
        assert r.people_total == 2


# ═══════════════════════════════════════════════════
# TP-03: Группы — только взрослые
# ═══════════════════════════════════════════════════


class TestAdultsOnlyGroups:
    """TP-03: запрос только для взрослых формирует одну группу adults."""

    def test_simple_adults(self) -> None:
        r = parse_meal_plan_request("Меню на неделю для 3 человек", EMPTY_PROFILE)
        assert len(r.groups) == 1
        assert r.groups[0].id == "adults"
        assert r.groups[0].count == 3

    def test_1_person_is_adults(self) -> None:
        r = parse_meal_plan_request("Рацион на 5 дней для 1 человека", EMPTY_PROFILE)
        assert len(r.groups) == 1
        assert r.groups[0].id == "adults"
        assert r.groups[0].count == 1


# ═══════════════════════════════════════════════════
# TP-04: Группы — взрослые + дети
# ═══════════════════════════════════════════════════


class TestAdultsAndChildren:
    """TP-04: запрос с детьми создаёт отдельную группу."""

    def test_child_without_age(self) -> None:
        r = parse_meal_plan_request("Меню на 7 дней для 3 человек, один ребенок", EMPTY_PROFILE)
        groups = {g.id: g for g in r.groups}
        assert "child" in groups
        assert groups["child"].count == 1
        assert groups["adults"].count == 2

    def test_child_with_age_under_3(self) -> None:
        r = parse_meal_plan_request("Рацион на неделю для 3 человек, ребенок 2 года", EMPTY_PROFILE)
        groups = {g.id: g for g in r.groups}
        assert "child_2y" in groups
        assert groups["child_2y"].count == 1
        assert r.operational_preferences.get("meal_slots_child", 0) >= 5

    def test_child_with_age_over_3(self) -> None:
        r = parse_meal_plan_request(
            "Меню на 5 дней для 4 человек, один ребенок 7 лет", EMPTY_PROFILE
        )
        groups = {g.id: g for g in r.groups}
        assert "child_7y" in groups
        assert groups["child_7y"].count == 1
        assert groups["adults"].count == 3

    def test_multiple_children(self) -> None:
        r = parse_meal_plan_request("Корзина на неделю для 5 человек, 2 детей", EMPTY_PROFILE)
        groups = {g.id: g for g in r.groups}
        child_groups = [g for g in r.groups if "child" in g.id]
        assert len(child_groups) == 1
        assert child_groups[0].count == 2
        assert groups["adults"].count == 3


# ═══════════════════════════════════════════════════
# TP-05: Сегментированные взрослые (один..другой)
# ═══════════════════════════════════════════════════


class TestSegmentedAdults:
    """TP-05: «один веган, другой предпочитает итальянскую» -> раздельные группы."""

    def test_vegan_plus_italian(self) -> None:
        r = parse_meal_plan_request(
            "Меню на 7 дней для 2 человек: один веган, другой предпочитает итальянскую кухню",
            EMPTY_PROFILE,
        )
        groups = {g.id: g for g in r.groups}
        assert "vegan_user" in groups
        assert groups["vegan_user"].hard_constraints.get("diet") == "vegan"
        assert "italian_user" in groups
        assert "italian" in groups["italian_user"].soft_preferences.get("cuisines", [])

    def test_vegetarian_in_4_people(self) -> None:
        r = parse_meal_plan_request(
            "Корзина на неделю для 4 человек. один из них вегетарианец",
            EMPTY_PROFILE,
        )
        groups = {g.id: g for g in r.groups}
        assert groups["vegetarian_user"].count == 1
        assert groups["vegetarian_user"].hard_constraints["diet"] == "vegetarian"
        assert groups["adults"].count == 3

    def test_segmented_plus_child(self) -> None:
        r = parse_meal_plan_request(
            "Меню на 7 дней для 4 человек: один веган, другой обычный, 1 ребенок 3 года",
            EMPTY_PROFILE,
        )
        groups = {g.id: g for g in r.groups}
        assert "vegan_user" in groups
        child_groups = [g for g in r.groups if "child" in g.id]
        assert len(child_groups) >= 1


# ═══════════════════════════════════════════════════
# TP-06: Диеты (hard constraints)
# ═══════════════════════════════════════════════════


class TestDietHardConstraints:
    """TP-06: диетические ограничения корректно применяются ко всем группам."""

    def test_vegan_all(self) -> None:
        r = parse_meal_plan_request("Веганское меню на 3 дня для 2 человек", EMPTY_PROFILE)
        assert all(g.hard_constraints.get("diet") == "vegan" for g in r.groups)

    def test_vegetarian_all(self) -> None:
        r = parse_meal_plan_request("Вегетарианский рацион на неделю для 2 человек", EMPTY_PROFILE)
        assert all(g.hard_constraints.get("diet") == "vegetarian" for g in r.groups)

    def test_halal_all(self) -> None:
        r = parse_meal_plan_request("Халяль меню на 5 дней для 3 человек", EMPTY_PROFILE)
        assert all(g.hard_constraints.get("diet") == "halal" for g in r.groups)

    def test_stored_diet_applied(self) -> None:
        r = parse_meal_plan_request("Рацион на 3 дня для 2 человек", VEG_PROFILE)
        assert all(g.hard_constraints.get("diet") == "vegetarian" for g in r.groups)

    def test_explicit_overrides_stored(self) -> None:
        r = parse_meal_plan_request("Веганское меню на 3 дня для 2 человек", VEG_PROFILE)
        assert all(g.hard_constraints.get("diet") == "vegan" for g in r.groups)


# ═══════════════════════════════════════════════════
# TP-07: Аллергены
# ═══════════════════════════════════════════════════


class TestAllergenConstraints:
    """TP-07: аллергены из текста и профиля объединяются."""

    def test_explicit_allergen(self) -> None:
        r = parse_meal_plan_request(
            "Меню на 5 дней для 2 человек с аллергией на орехи", EMPTY_PROFILE
        )
        assert "орехи" in r.groups[0].hard_constraints.get("allergens_excluded", [])

    def test_stored_allergen_preserved(self) -> None:
        profile = {"hard_constraints": {"allergens_excluded": ["лактоза"]}}
        r = parse_meal_plan_request("Рацион на 3 дня для 2 человек", profile)
        assert "лактоза" in r.groups[0].hard_constraints.get("allergens_excluded", [])

    def test_explicit_plus_stored_merged(self) -> None:
        profile = {"hard_constraints": {"allergens_excluded": ["лактоза"]}}
        r = parse_meal_plan_request("Меню на 7 дней для 2 человек с аллергией на орехи", profile)
        allergens = r.groups[0].hard_constraints.get("allergens_excluded", [])
        assert "орехи" in allergens
        assert "лактоза" in allergens


# ═══════════════════════════════════════════════════
# TP-08: Кухни (soft constraints)
# ═══════════════════════════════════════════════════


class TestCuisinePreferences:
    """TP-08: предпочтения по кухне парсятся корректно."""

    def test_italian_cuisine(self) -> None:
        r = parse_meal_plan_request("Итальянское меню на 5 дней для 2 человек", EMPTY_PROFILE)
        assert "italian" in r.groups[0].soft_preferences.get("cuisines", [])

    def test_asian_cuisine(self) -> None:
        r = parse_meal_plan_request("Азиатская кухня на 7 дней для 2 человек", EMPTY_PROFILE)
        assert "asian" in r.groups[0].soft_preferences.get("cuisines", [])

    def test_stored_cuisine(self) -> None:
        profile = {"soft_preferences": {"cuisines": ["georgian"]}}
        r = parse_meal_plan_request("Рацион на 3 дня для 2 человек", profile)
        assert "georgian" in r.groups[0].soft_preferences.get("cuisines", [])


# ═══════════════════════════════════════════════════
# TP-09: Валидация meal-plan payload (дни)
# ═══════════════════════════════════════════════════


class TestMealPlanDayValidation:
    """TP-09: валидация dish.day внутри допустимого диапазона 1..request.days."""

    @staticmethod
    def _make_request(days: int, people: int = 2) -> MealPlanRequest:
        return parse_meal_plan_request(f"Рацион на {days} дней для {people} человек", EMPTY_PROFILE)

    @staticmethod
    def _make_dishes(days: list[int], group_ids: list[str]) -> list[dict]:
        names = [
            "Овсяная каша",
            "Борщ",
            "Паста",
            "Салат Цезарь",
            "Тыквенный суп",
            "Рис с овощами",
            "Гречка",
            "Запеканка",
            "Омлет",
            "Рагу",
        ]
        dishes = []
        meals = ["breakfast", "lunch", "dinner"]
        for i, day in enumerate(days):
            dishes.append(
                {
                    "name": names[i % len(names)] + (f" #{i}" if i >= len(names) else ""),
                    "day": day,
                    "meal_type": meals[i % len(meals)],
                    "servings_total": 2,
                    "audience_groups": group_ids,
                    "cuisine_tags": ["russian"],
                }
            )
        return dishes

    def test_all_dishes_in_range_3_days(self) -> None:
        request = self._make_request(3)
        dishes = self._make_dishes([1, 1, 1, 2, 2, 2, 3, 3, 3], ["adults"])
        payload = {"schema_version": 1, "dishes": dishes}
        meal_plan, error = _validate_meal_plan_payload(payload, request)
        assert meal_plan is not None, f"Unexpected error: {error}"
        assert len(meal_plan.dishes) == 9

    def test_dish_day_out_of_range_rejected(self) -> None:
        request = self._make_request(3)
        dishes = self._make_dishes([1, 1, 1, 2, 2, 2, 3, 3, 4], ["adults"])
        payload = {"schema_version": 1, "dishes": dishes}
        meal_plan, error = _validate_meal_plan_payload(payload, request)
        assert meal_plan is None
        assert "day вне диапазона" in error

    def test_day_0_rejected(self) -> None:
        request = self._make_request(7)
        days = [d for d in range(1, 8) for _ in range(3)]
        days[0] = 0
        dishes = self._make_dishes(days, ["adults"])
        payload = {"schema_version": 1, "dishes": dishes}
        meal_plan, error = _validate_meal_plan_payload(payload, request)
        assert meal_plan is None
        assert "day вне диапазона" in error

    def test_single_day_plan(self) -> None:
        request = self._make_request(1)
        dishes = self._make_dishes([1, 1, 1, 1, 1], ["adults"])
        payload = {"schema_version": 1, "dishes": dishes}
        meal_plan, error = _validate_meal_plan_payload(payload, request)
        assert meal_plan is not None, f"Unexpected error: {error}"
        assert all(d.day == 1 for d in meal_plan.dishes)

    def test_14_day_plan_with_spread(self) -> None:
        request = self._make_request(14)
        days = [d for d in range(1, 15) for _ in range(2)]
        dishes = self._make_dishes(days, ["adults"])
        payload = {"schema_version": 1, "dishes": dishes}
        meal_plan, error = _validate_meal_plan_payload(payload, request)
        assert meal_plan is not None, f"Unexpected error: {error}"


# ═══════════════════════════════════════════════════
# TP-10: Hard constraints validation на сгенерированном плане
# ═══════════════════════════════════════════════════


class TestHardConstraintValidation:
    """TP-10: блюда с нарушениями диеты отклоняются."""

    @staticmethod
    def _veg_request() -> MealPlanRequest:
        return parse_meal_plan_request("Вегетарианское меню на 3 дня для 2 человек", EMPTY_PROFILE)

    def test_clean_vegetarian_passes(self) -> None:
        request = self._veg_request()
        dishes = [
            MealPlanDish("Овощной суп", 1, "lunch", 2, ["adults"], ["russian"]),
            MealPlanDish("Паста с грибами", 1, "dinner", 2, ["adults"], ["italian"]),
            MealPlanDish("Каша овсяная", 2, "breakfast", 2, ["adults"], ["russian"]),
            MealPlanDish("Салат греческий", 2, "lunch", 2, ["adults"], ["mediterranean"]),
            MealPlanDish("Рис с овощами", 2, "dinner", 2, ["adults"], ["asian"]),
            MealPlanDish("Блины", 3, "breakfast", 2, ["adults"], ["russian"]),
            MealPlanDish("Борщ без мяса", 3, "lunch", 2, ["adults"], ["russian"]),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert violations == []

    def test_meat_dish_in_veg_plan_fails(self) -> None:
        request = self._veg_request()
        dishes = [
            MealPlanDish("Овощной суп", 1, "lunch", 2, ["adults"], []),
            MealPlanDish("Куриные котлеты", 1, "dinner", 2, ["adults"], []),
            MealPlanDish("Каша", 2, "breakfast", 2, ["adults"], []),
            MealPlanDish("Салат", 2, "lunch", 2, ["adults"], []),
            MealPlanDish("Рис с тофу", 2, "dinner", 2, ["adults"], []),
            MealPlanDish("Блины", 3, "breakfast", 2, ["adults"], []),
            MealPlanDish("Гречка", 3, "lunch", 2, ["adults"], []),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert any("Куриные котлеты" in v for v in violations)

    def test_allergen_in_dish_name_fails(self) -> None:
        request = parse_meal_plan_request(
            "Меню на 3 дня для 2 человек с аллергией на орехи", EMPTY_PROFILE
        )
        dishes = [
            MealPlanDish("Салат с орехами", 1, "lunch", 2, ["adults"], []),
            MealPlanDish("Каша", 1, "breakfast", 2, ["adults"], []),
            MealPlanDish("Суп", 2, "lunch", 2, ["adults"], []),
            MealPlanDish("Рис", 2, "dinner", 2, ["adults"], []),
            MealPlanDish("Гречка", 3, "breakfast", 2, ["adults"], []),
            MealPlanDish("Тыквенный суп", 3, "lunch", 2, ["adults"], []),
            MealPlanDish("Рагу", 3, "dinner", 2, ["adults"], []),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert any("орех" in v.lower() for v in violations)

    def test_gluten_allergen_rejects_oat_dish(self) -> None:
        request = parse_meal_plan_request(
            "Меню на 3 дня для 2 человек с аллергией на глютен", EMPTY_PROFILE
        )
        dishes = [
            MealPlanDish("Овсяная каша", 1, "breakfast", 2, ["adults"], []),
            MealPlanDish("Салат", 1, "lunch", 2, ["adults"], []),
            MealPlanDish("Рис", 1, "dinner", 2, ["adults"], []),
            MealPlanDish("Гречка", 2, "breakfast", 2, ["adults"], []),
            MealPlanDish("Суп", 2, "lunch", 2, ["adults"], []),
            MealPlanDish("Рагу", 2, "dinner", 2, ["adults"], []),
            MealPlanDish("Каша рисовая", 3, "breakfast", 2, ["adults"], []),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert any("глютен" in v.lower() and "овсяная" in v.lower() for v in violations)

    def test_gluten_allergen_rejects_pasta(self) -> None:
        request = parse_meal_plan_request(
            "Меню на 3 дня для 2 человек с аллергией на глютен", EMPTY_PROFILE
        )
        dishes = [
            MealPlanDish("Макароны по-флотски", 1, "lunch", 2, ["adults"], []),
            MealPlanDish("Рис", 1, "dinner", 2, ["adults"], []),
            MealPlanDish("Гречка", 2, "breakfast", 2, ["adults"], []),
            MealPlanDish("Суп", 2, "lunch", 2, ["adults"], []),
            MealPlanDish("Салат", 2, "dinner", 2, ["adults"], []),
            MealPlanDish("Рагу", 3, "lunch", 2, ["adults"], []),
            MealPlanDish("Каша рисовая", 3, "breakfast", 2, ["adults"], []),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert any("глютен" in v.lower() and "макарон" in v.lower() for v in violations)

    def test_compound_allergens_split_correctly(self) -> None:
        request = parse_meal_plan_request(
            "Меню на 3 дня для 2 человек с аллергией на молоко и глютен", EMPTY_PROFILE
        )
        allergens = request.groups[0].hard_constraints.get("allergens_excluded", [])
        assert "молоко" in allergens
        assert "глютен" in allergens

    def test_dairy_allergen_rejects_cheese(self) -> None:
        request = parse_meal_plan_request(
            "Меню на 3 дня для 2 человек с аллергией на молоко", EMPTY_PROFILE
        )
        dishes = [
            MealPlanDish("Сырники", 1, "breakfast", 2, ["adults"], []),
            MealPlanDish("Рис", 1, "lunch", 2, ["adults"], []),
            MealPlanDish("Салат", 1, "dinner", 2, ["adults"], []),
            MealPlanDish("Гречка", 2, "breakfast", 2, ["adults"], []),
            MealPlanDish("Суп", 2, "lunch", 2, ["adults"], []),
            MealPlanDish("Рагу", 2, "dinner", 2, ["adults"], []),
            MealPlanDish("Каша рисовая", 3, "breakfast", 2, ["adults"], []),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert any("молоко" in v.lower() for v in violations)

    def test_bez_glutena_in_dish_name_no_false_positive(self) -> None:
        """'без глютена' in dish name must NOT trigger gluten allergen violation."""
        request = parse_meal_plan_request(
            "Меню на 3 дня для 2 человек с аллергией на глютен", EMPTY_PROFILE
        )
        dishes = [
            MealPlanDish(
                "Овощной суп на курином бульоне без глютена",
                1, "lunch", 2, ["adults"], ["русская"],
            ),
            MealPlanDish("Гречневая каша на воде", 1, "breakfast", 2, ["adults"], []),
            MealPlanDish("Запечённая курица с овощами", 2, "dinner", 2, ["adults"], []),
            MealPlanDish("Рис с тушеной индейкой", 3, "lunch", 2, ["adults"], []),
            MealPlanDish("Картофельное пюре", 3, "dinner", 2, ["adults"], []),
            MealPlanDish("Фруктовый салат", 2, "snack_1", 2, ["adults"], []),
            MealPlanDish("Овощное рагу", 3, "breakfast", 2, ["adults"], []),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert not any("без глютена" in v.lower() for v in violations)

    def test_mannaya_kasha_correctly_flagged_for_gluten(self) -> None:
        """Манная каша contains wheat → must be flagged for gluten allergen."""
        request = parse_meal_plan_request(
            "Меню на 3 дня для 2 человек с аллергией на глютен", EMPTY_PROFILE
        )
        dishes = [
            MealPlanDish("Манная каша на воде", 1, "breakfast", 2, ["adults"], []),
            MealPlanDish("Рис с овощами", 1, "lunch", 2, ["adults"], []),
            MealPlanDish("Гречневая каша", 2, "breakfast", 2, ["adults"], []),
            MealPlanDish("Суп овощной", 2, "lunch", 2, ["adults"], []),
            MealPlanDish("Картофель", 3, "dinner", 2, ["adults"], []),
            MealPlanDish("Фрукты", 2, "snack_1", 2, ["adults"], []),
            MealPlanDish("Запечённая рыба", 3, "lunch", 2, ["adults"], []),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert any("манная" in v.lower() for v in violations)

    def test_bezglyutenovye_compound_no_false_positive(self) -> None:
        """Compound 'безглютеновые' must NOT trigger gluten violation."""
        request = parse_meal_plan_request(
            "Меню на 3 дня для 2 человек с аллергией на глютен", EMPTY_PROFILE
        )
        dishes = [
            MealPlanDish(
                "Безглютеновые оладьи из гречневой крупы", 1, "breakfast", 2, ["adults"], [],
            ),
            MealPlanDish("Рис с овощами", 1, "lunch", 2, ["adults"], []),
            MealPlanDish("Гречка", 2, "breakfast", 2, ["adults"], []),
            MealPlanDish("Суп", 2, "lunch", 2, ["adults"], []),
            MealPlanDish("Картофель", 3, "dinner", 2, ["adults"], []),
            MealPlanDish("Запечённая рыба", 3, "lunch", 2, ["adults"], []),
            MealPlanDish("Фруктовый салат", 2, "snack_1", 2, ["adults"], []),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert not any("безглютенов" in v.lower() for v in violations)

    def test_gluten_zapekanka_no_false_positive(self) -> None:
        """Запеканка не должна ложно срабатывать на ореховый аллерген."""
        request = parse_meal_plan_request(
            "Меню на 3 дня для 2 человек с аллергией на орехи", EMPTY_PROFILE
        )
        dishes = [
            MealPlanDish("Запеканка", 1, "dinner", 2, ["adults"], []),
            MealPlanDish("Каша", 1, "breakfast", 2, ["adults"], []),
            MealPlanDish("Суп", 2, "lunch", 2, ["adults"], []),
            MealPlanDish("Рис", 2, "dinner", 2, ["adults"], []),
            MealPlanDish("Гречка", 3, "breakfast", 2, ["adults"], []),
            MealPlanDish("Тыквенный суп", 3, "lunch", 2, ["adults"], []),
            MealPlanDish("Рагу", 3, "dinner", 2, ["adults"], []),
        ]
        violations = validate_hard_constraints(request=request, dishes=dishes)
        assert not any("запеканка" in v.lower() for v in violations)


# ═══════════════════════════════════════════════════
# TP-11: Soft coverage (кухни)
# ═══════════════════════════════════════════════════


class TestSoftCoverageCuisines:
    """TP-11: soft coverage >= 70% для запрошенных кухонь."""

    def test_high_coverage_passes(self) -> None:
        request = parse_meal_plan_request("Итальянское меню на 3 дня для 2 человек", EMPTY_PROFILE)
        dishes = [
            MealPlanDish("Паста", 1, "lunch", 2, ["adults"], ["italian"]),
            MealPlanDish("Пицца", 1, "dinner", 2, ["adults"], ["italian"]),
            MealPlanDish("Ризотто", 2, "lunch", 2, ["adults"], ["italian"]),
            MealPlanDish("Минестроне", 2, "dinner", 2, ["adults"], ["italian"]),
            MealPlanDish("Лазанья", 3, "lunch", 2, ["adults"], ["italian"]),
            MealPlanDish("Каша", 3, "breakfast", 2, ["adults"], ["russian"]),
            MealPlanDish("Тирамису", 3, "dinner", 2, ["adults"], ["italian"]),
        ]
        coverage = calculate_soft_coverage(request=request, dishes=dishes)
        assert coverage["adults"] >= 0.70

    def test_low_coverage_detected(self) -> None:
        request = parse_meal_plan_request("Итальянское меню на 3 дня для 2 человек", EMPTY_PROFILE)
        dishes = [
            MealPlanDish("Борщ", 1, "lunch", 2, ["adults"], ["russian"]),
            MealPlanDish("Щи", 1, "dinner", 2, ["adults"], ["russian"]),
            MealPlanDish("Каша", 2, "breakfast", 2, ["adults"], ["russian"]),
            MealPlanDish("Пельмени", 2, "lunch", 2, ["adults"], ["russian"]),
            MealPlanDish("Суп", 2, "dinner", 2, ["adults"], ["russian"]),
            MealPlanDish("Гречка", 3, "lunch", 2, ["adults"], ["russian"]),
            MealPlanDish("Паста", 3, "dinner", 2, ["adults"], ["italian"]),
        ]
        coverage = calculate_soft_coverage(request=request, dishes=dishes)
        assert coverage["adults"] < 0.70


# ═══════════════════════════════════════════════════
# TP-12: Количество блюд (7..10)
# ═══════════════════════════════════════════════════


class TestDishCountLimits:
    """TP-12: валидация допускает 14..21 блюд (7 дней × 3 приёма)."""

    @staticmethod
    def _make_payload(count: int) -> dict:
        names = [f"Блюдо #{i}" for i in range(count)]
        dishes = []
        for i, name in enumerate(names):
            dishes.append(
                {
                    "name": name,
                    "day": (i % 7) + 1,
                    "meal_type": ["breakfast", "lunch", "dinner"][i % 3],
                    "servings_total": 2,
                    "audience_groups": ["adults"],
                    "cuisine_tags": ["russian"],
                }
            )
        return {"schema_version": 1, "dishes": dishes}

    @staticmethod
    def _request() -> MealPlanRequest:
        return parse_meal_plan_request("Рацион на неделю для 2 человек", EMPTY_PROFILE)

    def test_13_dishes_rejected(self) -> None:
        payload = self._make_payload(13)
        meal_plan, error = _validate_meal_plan_payload(payload, self._request())
        assert meal_plan is None
        assert "14..21" in error

    def test_14_dishes_accepted(self) -> None:
        payload = self._make_payload(14)
        meal_plan, error = _validate_meal_plan_payload(payload, self._request())
        assert meal_plan is not None, f"Unexpected: {error}"

    def test_21_dishes_accepted(self) -> None:
        payload = self._make_payload(21)
        meal_plan, error = _validate_meal_plan_payload(payload, self._request())
        assert meal_plan is not None, f"Unexpected: {error}"

    def test_22_dishes_rejected(self) -> None:
        payload = self._make_payload(22)
        meal_plan, error = _validate_meal_plan_payload(payload, self._request())
        assert meal_plan is None
        assert "14..21" in error


# ═══════════════════════════════════════════════════
# TP-13: Комбинированные сценарии (реалистичные кейсы)
# ═══════════════════════════════════════════════════


class TestCombinedScenarios:
    """TP-13: сквозные сценарии, приближённые к реальным запросам."""

    def test_family_week_veg_plus_child_with_allergy(self) -> None:
        """Семья 4 чел: 3 взрослых + ребёнок 2 года, аллергия на орехи, вегетарианство."""
        r = parse_meal_plan_request(
            "Вегетарианское меню на неделю для 4 человек, один ребенок 2 года с аллергией на орехи",
            EMPTY_PROFILE,
        )
        assert r.days == 7
        assert r.people_total == 4
        groups = {g.id: g for g in r.groups}
        assert groups["adults"].count == 3
        assert "child_2y" in groups
        for g in r.groups:
            assert g.hard_constraints.get("diet") == "vegetarian"
            assert "орехи" in g.hard_constraints.get("allergens_excluded", [])

    def test_couple_3_days_halal(self) -> None:
        """Пара, 3 дня, халяль."""
        r = parse_meal_plan_request("Халяль меню на 3 дня для 2 человек", EMPTY_PROFILE)
        assert r.days == 3
        assert r.people_total == 2
        assert r.groups[0].hard_constraints["diet"] == "halal"

    def test_solo_1_day(self) -> None:
        """Один человек, 1 день."""
        r = parse_meal_plan_request("Собери продукты на 1 день для 1 человека", EMPTY_PROFILE)
        assert r.days == 1
        assert r.people_total == 1
        assert r.groups[0].count == 1

    def test_large_group_10_days(self) -> None:
        """Большая группа 10 человек, 10 дней."""
        r = parse_meal_plan_request("Рацион на 10 дней для 10 человек", EMPTY_PROFILE)
        assert r.days == 10
        assert r.people_total == 10

    def test_segmented_vegan_regular_plus_child_14_days(self) -> None:
        """14 дней, один веган, другой обычный, + ребёнок."""
        r = parse_meal_plan_request(
            "Меню на 14 дней для 4 человек: один веган, другой обычный, 1 ребенок 5 лет",
            EMPTY_PROFILE,
        )
        assert r.days == 14
        groups = {g.id: g for g in r.groups}
        assert "vegan_user" in groups
        child_groups = [g for g in r.groups if "child" in g.id]
        assert len(child_groups) >= 1

    def test_stored_profile_merged_with_explicit(self) -> None:
        """Профиль из БД + явные ограничения в тексте = merge."""
        profile = {
            "hard_constraints": {"allergens_excluded": ["лактоза"], "diet": "vegetarian"},
            "soft_preferences": {"cuisines": ["italian"]},
            "operational_preferences": {},
        }
        r = parse_meal_plan_request("Рацион на 5 дней для 3 человек с аллергией на орехи", profile)
        allergens = r.groups[0].hard_constraints.get("allergens_excluded", [])
        assert "лактоза" in allergens
        assert "орехи" in allergens
        assert r.groups[0].hard_constraints.get("diet") == "vegetarian"
        assert "italian" in r.groups[0].soft_preferences.get("cuisines", [])

    def test_week_no_constraints_default_group(self) -> None:
        """Минимальный запрос без ограничений."""
        r = parse_meal_plan_request("Рацион на неделю", EMPTY_PROFILE)
        assert r.days == 7
        assert r.people_total == 2
        assert len(r.groups) == 1
        assert r.groups[0].id == "adults"

    def test_5_days_workweek_family(self) -> None:
        """Будни для семьи из 4 человек."""
        r = parse_meal_plan_request("Меню на 5 дней для 4 человек, 1 ребенок", EMPTY_PROFILE)
        assert r.days == 5
        assert r.people_total == 4
        groups = {g.id: g for g in r.groups}
        assert groups["adults"].count == 3


# ═══════════════════════════════════════════════════
# TP-14: Дублирование блюд запрещено
# ═══════════════════════════════════════════════════


class TestNoDuplicateDishes:
    """TP-14: повторяющиеся названия блюд отклоняются валидатором."""

    def test_duplicate_dish_rejected(self) -> None:
        request = parse_meal_plan_request("Меню на 3 дня для 2 человек", EMPTY_PROFILE)
        dishes = [
            {
                "name": "Каша",
                "day": 1,
                "meal_type": "breakfast",
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": [],
            },
            {
                "name": "Каша",
                "day": 2,
                "meal_type": "breakfast",
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": [],
            },
            {
                "name": "Суп",
                "day": 1,
                "meal_type": "lunch",
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": [],
            },
            {
                "name": "Рис",
                "day": 2,
                "meal_type": "lunch",
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": [],
            },
            {
                "name": "Гречка",
                "day": 3,
                "meal_type": "lunch",
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": [],
            },
            {
                "name": "Салат",
                "day": 3,
                "meal_type": "dinner",
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": [],
            },
            {
                "name": "Блины",
                "day": 3,
                "meal_type": "breakfast",
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": [],
            },
        ]
        payload = {"schema_version": 1, "dishes": dishes}
        meal_plan, error = _validate_meal_plan_payload(payload, request)
        assert meal_plan is None
        assert "повтор" in error.lower()


# ═══════════════════════════════════════════════════
# TP-15: audience_groups валидация
# ═══════════════════════════════════════════════════


class TestAudienceGroupsValidation:
    """TP-15: audience_groups должны совпадать с group_ids запроса."""

    def test_unknown_group_rejected(self) -> None:
        request = parse_meal_plan_request("Меню на 3 дня для 2 человек", EMPTY_PROFILE)
        dishes = []
        for i in range(7):
            dishes.append(
                {
                    "name": f"Блюдо {i}",
                    "day": (i % 3) + 1,
                    "meal_type": ["breakfast", "lunch", "dinner"][i % 3],
                    "servings_total": 2,
                    "audience_groups": ["nonexistent_group"],
                    "cuisine_tags": [],
                }
            )
        payload = {"schema_version": 1, "dishes": dishes}
        meal_plan, error = _validate_meal_plan_payload(payload, request)
        assert meal_plan is None
        assert "неизвестные audience_groups" in error


# ═══════════════════════════════════════════════════
# TP-16: Множественные meal_type
# ═══════════════════════════════════════════════════


class TestMealTypes:
    """TP-16: все допустимые meal_type принимаются, недопустимые — нет."""

    @staticmethod
    def _build_payload(meal_type: str) -> dict:
        dishes = [
            {
                "name": f"Блюдо {i}",
                "day": (i % 2) + 1,
                "meal_type": meal_type if i == 0 else ["breakfast", "lunch"][i % 2],
                "servings_total": 2,
                "audience_groups": ["adults"],
                "cuisine_tags": [],
            }
            for i in range(4)
        ]
        return {"schema_version": 1, "dishes": dishes}

    @staticmethod
    def _request() -> MealPlanRequest:
        return parse_meal_plan_request("Меню на 2 дня для 2 человек", EMPTY_PROFILE)

    @pytest.mark.parametrize(
        "meal_type",
        ["breakfast", "lunch", "dinner", "snack_1", "snack_2", "snack_3"],
    )
    def test_valid_meal_types(self, meal_type: str) -> None:
        payload = self._build_payload(meal_type)
        meal_plan, error = _validate_meal_plan_payload(payload, self._request())
        assert meal_plan is not None, f"Unexpected error for {meal_type}: {error}"

    def test_invalid_meal_type_rejected(self) -> None:
        payload = self._build_payload("brunch")
        meal_plan, error = _validate_meal_plan_payload(payload, self._request())
        assert meal_plan is None
        assert "meal_type" in error
