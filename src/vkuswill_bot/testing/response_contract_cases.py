"""Executable response-contract scenarios shared by pytest and live Pi runners."""

from __future__ import annotations

from dataclasses import dataclass, field


COMMON_MUST_NOT_CONTAIN = [
    "<tool_call>",
    '{"name":',
    '"arguments":',
    "vkusvill_products_search",
]


@dataclass(frozen=True, slots=True)
class ResponseContract:
    response_kind: str
    expected_profile: str | None = None
    requires_cart_button: bool | None = None
    max_chunks: int | None = None
    max_chars_total: int | None = None
    max_lines_total: int | None = None
    min_items_count: int | None = None
    max_items_count: int | None = None
    must_contain: list[str] = field(default_factory=list)
    must_contain_any: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    required_products: list[str] = field(default_factory=list)
    forbidden_products: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StageScenario:
    case_id: str
    user_id: int
    turns: list[str]
    status: str
    contract: ResponseContract
    known_issue: str | None = None


def _contract(
    response_kind: str,
    *,
    expected_profile: str | None = None,
    requires_cart_button: bool | None = None,
    max_chunks: int | None = 2,
    max_chars_total: int | None = 2200,
    max_lines_total: int | None = 60,
    min_items_count: int | None = None,
    max_items_count: int | None = None,
    must_contain: list[str] | None = None,
    must_contain_any: list[str] | None = None,
    must_not_contain: list[str] | None = None,
    required_products: list[str] | None = None,
    forbidden_products: list[str] | None = None,
) -> ResponseContract:
    return ResponseContract(
        response_kind=response_kind,
        expected_profile=expected_profile,
        requires_cart_button=requires_cart_button,
        max_chunks=max_chunks,
        max_chars_total=max_chars_total,
        max_lines_total=max_lines_total,
        min_items_count=min_items_count,
        max_items_count=max_items_count,
        must_contain=list(must_contain or []),
        must_contain_any=list(must_contain_any or []),
        must_not_contain=[*COMMON_MUST_NOT_CONTAIN, *(must_not_contain or [])],
        required_products=list(required_products or []),
        forbidden_products=list(forbidden_products or []),
    )


def _case(
    case_id: str,
    user_id: int,
    turns: list[str],
    *,
    status: str,
    known_issue: str | None = None,
    contract: ResponseContract,
) -> StageScenario:
    return StageScenario(
        case_id=case_id,
        user_id=user_id,
        turns=turns,
        status=status,
        known_issue=known_issue,
        contract=contract,
    )


SCENARIOS: list[StageScenario] = [
    _case(
        "TC-QTY-01",
        910001,
        ["собери корзину: яйца 30 штук, масло сливочное 500г, сыр 200 граммов"],
        status="known_issue",
        known_issue="Qty normalization for piece-based products is still unstable.",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=3,
            required_products=["яйц", "масл", "сыр"],
        ),
    ),
    _case(
        "TC-QTY-02",
        910002,
        ["картофель 2.5 кг, курица 1.7 кг, помидоры 0.5 кг"],
        status="known_issue",
        known_issue="Qty conversion for fractional kilograms still regresses on stage.",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=3,
            required_products=["картоф", "кур", "помид"],
        ),
    ),
    _case(
        "TC-QTY-03",
        910003,
        ["лук 100 г, чеснок 50г, имбирь 30 г"],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=2,
            required_products=["лук", "чеснок"],
        ),
    ),
    _case(
        "TC-QTY-04",
        910004,
        ["картофель 20 кг, курица 10 кг, рис 5 кг, молоко 10 литров"],
        status="known_issue",
        known_issue="Large-order qty conversion is a tracked regression scenario.",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=4,
            required_products=["картоф", "кур", "рис", "молок"],
        ),
    ),
    _case(
        "TC-QTY-05",
        910005,
        ["2 бутылки молока, 3 кг картофеля, 500 мл сливок, 1 пачка масла, десяток яиц"],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=4,
            required_products=["молок", "картоф", "слив", "масл", "яйц"],
        ),
    ),
    _case(
        "TC-MULTI-01",
        910006,
        [
            "собери корзину: молоко 1л, хлеб белый, сыр твердый 200г, масло сливочное, яйца 10 шт",
            "замени сыр твердый на моцареллу",
            "убери масло, добавь сметану и кефир",
        ],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=5,
            required_products=["молок", "хлеб", "моцар", "сметан", "кефир", "яйц"],
            forbidden_products=["масл"],
        ),
    ),
    _case(
        "TC-MULTI-02",
        910007,
        [
            "собери: молоко 1 литр, хлеб, масло сливочное",
            "добавь яйца и сыр",
            "убери хлеб",
            "молоко замени на 3.2%",
            "покажи итоговую корзину",
        ],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile=None,
            requires_cart_button=True,
            min_items_count=4,
            must_contain_any=["итог", "корзин"],
            required_products=["молок", "масл", "яйц", "сыр"],
            forbidden_products=["хлеб"],
        ),
    ),
    _case(
        "TC-MULTI-03",
        910008,
        [
            "собери корзину: картофель 2 кг, лук 1 кг, морковь 1 кг, курица 1.5 кг",
            "ещё добавь рис 1 кг и гречку 1 кг",
        ],
        status="known_issue",
        known_issue="Known qty explosion regression when adding products into an existing cart.",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=6,
            required_products=["картоф", "лук", "морков", "кур", "рис", "греч"],
        ),
    ),
    _case(
        "TC-EDGE-01",
        910009,
        ["хочу купить только молоко"],
        status="known_issue",
        known_issue=(
            "Single-product cart requests still sometimes trigger clarification instead of action."
        ),
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=1,
            max_items_count=2,
            required_products=["молок"],
        ),
    ),
    _case(
        "TC-EDGE-02",
        910010,
        ["ааааа"],
        status="stable",
        contract=_contract(
            "clarification",
            requires_cart_button=False,
            max_chunks=1,
            max_chars_total=600,
            max_items_count=0,
            must_not_contain=["Traceback", "Exception"],
        ),
    ),
    _case(
        "TC-EDGE-03",
        910011,
        ["🥛 молоко 🧀 сыр 🥚 яйца"],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=3,
            required_products=["молок", "сыр", "яйц"],
        ),
    ),
    _case(
        "TC-EDGE-04",
        910012,
        ["найди мне трюфели, фуа-гра и устрицы"],
        status="stable",
        contract=_contract(
            "fallback",
            requires_cart_button=False,
            max_chunks=1,
            max_chars_total=900,
            max_items_count=0,
            must_contain_any=["не наш", "не удалось", "нет в каталоге"],
        ),
    ),
    _case(
        "TC-NLP-01",
        910013,
        ["ну там типа молочку какую-нибудь и хлеб"],
        status="known_issue",
        known_issue=("Action-first behavior for vague but actionable lists is still not stable."),
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=2,
            required_products=["молок", "хлеб"],
            must_not_contain=["уточните"],
        ),
    ),
    _case(
        "TC-NLP-02",
        910014,
        ["собери корзину для борща на 4 порции"],
        status="stable",
        contract=_contract(
            "recipe",
            expected_profile="recipe",
            requires_cart_button=True,
            min_items_count=4,
            required_products=["свек", "капуст", "картоф"],
            must_not_contain=["7-днев", "на неделю"],
        ),
    ),
    _case(
        "TC-NLP-03",
        910015,
        ["хочу веганский стейк из мраморной говядины"],
        status="stable",
        contract=_contract(
            "clarification",
            requires_cart_button=False,
            max_chunks=1,
            max_chars_total=900,
            must_contain_any=["уточ", "не могу", "противореч"],
        ),
    ),
    _case(
        "TC-NLP-04",
        910016,
        ["сулугуни, мацони, лаваш, аджика, ткемали"],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=4,
            required_products=["сулугу", "мацон", "лаваш", "аджик"],
        ),
    ),
    _case(
        "TC-RECIPE-01",
        910017,
        ["собери продукты для лазаньи на 6 порций"],
        status="known_issue",
        known_issue=("Recipe assembly still picks irrelevant products for lasagna on stage."),
        contract=_contract(
            "recipe",
            expected_profile="recipe",
            requires_cart_button=True,
            min_items_count=5,
            must_not_contain=["суп куриный"],
            required_products=["сыр", "фарш", "соус"],
        ),
    ),
    _case(
        "TC-RECIPE-02",
        910018,
        [
            "собери для завтрака овсянку с ягодами, на обед — куриный суп, "
            "на ужин — пасту карбонара, всё на двоих"
        ],
        status="stable",
        contract=_contract(
            "recipe",
            expected_profile="recipe",
            requires_cart_button=True,
            min_items_count=6,
            must_not_contain=["7-днев", "на неделю"],
        ),
    ),
    _case(
        "TC-PERSONA-01",
        910019,
        ["собери продукты на неделю для ребёнка 3 года с аллергией на молоко и глютен"],
        status="stable",
        contract=_contract(
            "meal_plan",
            expected_profile="meal_plan",
            requires_cart_button=None,
            max_chunks=4,
            max_chars_total=3500,
            forbidden_products=["молок", "пшениц"],
        ),
    ),
    _case(
        "TC-PERSONA-02",
        910020,
        ["нужны перекусы в школу для ребёнка: без орехов, без молока, 5 дней"],
        status="known_issue",
        known_issue="School snack persona remains a tracked quality gap.",
        contract=_contract(
            "meal_plan",
            expected_profile="meal_plan",
            requires_cart_button=None,
            max_chunks=4,
            max_chars_total=3200,
            min_items_count=5,
            must_not_contain=["картоф"],
        ),
    ),
    _case(
        "TC-PERSONA-03",
        910021,
        ["собери самую дешёвую еду на 3 дня, бюджет 1000 рублей"],
        status="known_issue",
        known_issue=("Budget persona still needs stronger price-bound assertions on stage."),
        contract=_contract(
            "meal_plan",
            expected_profile="meal_plan",
            requires_cart_button=None,
            max_chunks=4,
            max_chars_total=3200,
            must_contain_any=["1000", "бюджет", "итого"],
        ),
    ),
    _case(
        "TC-PERSONA-04",
        910022,
        ["что можно приготовить из курицы, риса и лука? собери"],
        status="stable",
        contract=_contract(
            "recipe",
            expected_profile="recipe",
            requires_cart_button=True,
            min_items_count=3,
            must_contain_any=["куриц", "цыплен"],
            required_products=["рис", "лук"],
            must_not_contain=["на неделю"],
        ),
    ),
    _case(
        "TC-PERSONA-05",
        910023,
        [
            "высокобелковые продукты: куриная грудка 2 кг, творог 5% 1 кг, "
            "яйца 20 шт, протеиновые батончики"
        ],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=4,
            required_products=["кур", "твор", "яйц", "батон"],
        ),
    ),
    _case(
        "TC-PERSONA-07",
        910024,
        [
            "день рождения на 10 человек: чипсы, сыр нарезка, колбаса, "
            "оливки, виноград, сок 3 литра, торт"
        ],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=6,
            required_products=["чипс", "сыр", "колбас", "олив", "виноград", "сок", "торт"],
        ),
    ),
    _case(
        "TC-PERSONA-08",
        910025,
        [
            "шашлык на 8 человек: свинина 4 кг, лук 2 кг, помидоры 2 кг, "
            "лаваш 5 штук, кетчуп, горчица, уголь для мангала"
        ],
        status="known_issue",
        known_issue=("Shashlik scenario is a known regression and should stay visible via xfail."),
        contract=_contract(
            "cart",
            expected_profile="recipe",
            requires_cart_button=True,
            min_items_count=5,
            required_products=["свинин", "лук", "помид", "лаваш"],
            must_not_contain=["пепперони"],
        ),
    ),
    _case(
        "TC-PERSONA-09",
        910026,
        ["кето-завтрак на двоих: авокадо, бекон, яйца, сливочный сыр — без хлеба и круп"],
        status="stable",
        contract=_contract(
            "recipe",
            expected_profile="recipe",
            requires_cart_button=True,
            min_items_count=4,
            required_products=["авокад", "бекон", "яйц", "сыр"],
            forbidden_products=["хлеб"],
        ),
    ),
    _case(
        "TC-PERSONA-11",
        910027,
        ["мне бы хлеба, молочка и что-нибудь к чаю"],
        status="known_issue",
        known_issue=("Tea-time elderly persona still regresses into clarification on stage."),
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=3,
            required_products=["хлеб", "молок"],
            must_not_contain=["уточните"],
        ),
    ),
    _case(
        "TC-VOICE-01",
        910028,
        ["малако два литра хлеп батон сыр грамм двести"],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=3,
            required_products=["молок", "батон", "сыр"],
        ),
    ),
    _case(
        "TC-VOICE-02",
        910029,
        ["ну это самое закинь туда курочку какую-нибудь и картошечки с морковкой ну и ладно"],
        status="known_issue",
        known_issue="Conversational voice requests can still add extra items on stage.",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=3,
            max_items_count=3,
            required_products=["кур", "картоф", "морков"],
            forbidden_products=["лук", "масл"],
        ),
    ),
    _case(
        "TC-FORMAT-01",
        910030,
        ["полтора кило картошки, полкило моркови, четверть кило масла"],
        status="known_issue",
        known_issue="Conversational quantity formats still regress on stage.",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=3,
            required_products=["картоф", "морков", "масл"],
            must_not_contain=["овощ или десерт"],
        ),
    ),
    _case(
        "TC-FORMAT-02",
        910031,
        ["пара литров молока, тройку яблок, пяток яиц"],
        status="known_issue",
        known_issue="Colloquial numerals remain a known unresolved bug.",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=3,
            required_products=["молок", "яблок", "яйц"],
        ),
    ),
    _case(
        "TC-NEGATE-01",
        910032,
        ["собери на завтрак, но без яиц и без глютена"],
        status="stable",
        contract=_contract(
            "recipe",
            expected_profile=None,
            requires_cart_button=True,
            min_items_count=2,
            must_contain_any=["без яиц", "без глютена", "глютен"],
            forbidden_products=["яйц"],
        ),
    ),
    _case(
        "TC-NEGATE-02",
        910033,
        ["всё для оливье, только вместо колбасы — курица, и без горошка"],
        status="known_issue",
        known_issue=("Modified recipe ingredient selection still has known catalog mismatches."),
        contract=_contract(
            "recipe",
            expected_profile="recipe",
            requires_cart_button=True,
            min_items_count=4,
            required_products=["кур", "картоф"],
            forbidden_products=["свек", "горош"],
        ),
    ),
    _case(
        "TC-LANG-01",
        910034,
        ["I need milk, bread, eggs and cheese please"],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=4,
            must_not_contain=["milk", "bread", "eggs"],
            required_products=["молок", "хлеб", "яйц", "сыр"],
        ),
    ),
    _case(
        "TC-LANG-02",
        910035,
        ["moloko 2 litra, hleb, maslo"],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=3,
            required_products=["молок", "хлеб", "масл"],
        ),
    ),
    _case(
        "TC-CONTEXT-01",
        910036,
        ["заболел, нужно для лечения: мёд, лимон, имбирь, чай с мятой, куриный бульон"],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=5,
            required_products=["мед", "лимон", "имбир", "чай", "бульон"],
        ),
    ),
    _case(
        "TC-CONTEXT-02",
        910037,
        [
            "собери набор продуктов на пикник для 4 человек, без готовки — "
            "только то что можно есть сразу"
        ],
        status="stable",
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=5,
            must_contain_any=["пикник", "закуск", "готов"],
        ),
    ),
    _case(
        "TC-CONTEXT-03",
        910038,
        [
            "романтический ужин: стейк, вино красное сухое, руккола, "
            "пармезан, помидоры черри, оливковое масло"
        ],
        status="known_issue",
        known_issue=("Alcohol notice and cherry tomato quantity remain a tracked stage issue."),
        contract=_contract(
            "cart",
            expected_profile="cart",
            requires_cart_button=True,
            min_items_count=5,
            required_products=["стейк", "руккол", "пармез", "черри", "масл"],
            must_contain_any=["алког", "вино", "нельзя"],
        ),
    ),
    _case(
        "TC-COMPLEX-02",
        910039,
        ["у нас гости из Грузии, хочу приготовить хинкали, хачапури и аджапсандали — на 6 человек"],
        status="known_issue",
        known_issue="Complex Georgian cuisine recipe synthesis still has product-quality gaps.",
        contract=_contract(
            "recipe",
            expected_profile="recipe",
            requires_cart_button=True,
            min_items_count=6,
            must_contain_any=["хинкал", "хачапур", "аджапсанд"],
            must_not_contain=["цесар"],
        ),
    ),
]
