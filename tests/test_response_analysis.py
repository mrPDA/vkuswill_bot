"""Тесты для vkuswill_bot.agents.response_analysis."""

from __future__ import annotations

import pytest

from vkuswill_bot.agents.response_analysis import (
    is_additive_cart_intent,
    is_cart_intent,
    looks_like_partial_recipe_reply,
    should_start_fresh_context,
)


# ── is_additive_cart_intent ───────────────────────────────────────────


class TestIsAdditiveCartIntent:
    @pytest.mark.parametrize(
        "text",
        [
            "Добавь ещё молоко",
            "а ещё хлеб",
            "еще сыр",
            "дополни корзину маслом",
            "и еще яйца",
            "плюс кефир",
            "к этой корзине добавь",
            "к предыдущей корзине",
        ],
    )
    def test_positive(self, text: str):
        assert is_additive_cart_intent(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "собери борщ",
            "привет",
            "покажи рецепт",
            "",
        ],
    )
    def test_negative(self, text: str):
        assert is_additive_cart_intent(text) is False


# ── is_cart_intent ────────────────────────────────────────────────────


class TestIsCartIntent:
    @pytest.mark.parametrize(
        "text",
        [
            "собери корзину на борщ",
            "закажи продукты",
            "добавь молоко",
            "купить хлеб",
            "ингредиенты для пирога",
            "рецепт борща",
            "приготовь салат",
            "свари суп",
            "испечь пирог",
            "убери сыр",
            "удали молоко",
            "замени масло",
            "измени количество",
            "поменяй хлеб",
            "объедини корзины",
        ],
    )
    def test_positive(self, text: str):
        assert is_cart_intent(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "привет",
            "как дела",
            "",
            "расскажи анекдот",
        ],
    )
    def test_negative(self, text: str):
        assert is_cart_intent(text) is False


# ── looks_like_partial_recipe_reply ───────────────────────────────────


class TestLooksLikePartialRecipeReply:
    @pytest.mark.parametrize(
        "text",
        [
            "Я подобрал ингредиенты для борща",
            "Подобрала список продуктов",
            "Вот рецепт, если нужно, могу собрать корзину",
            "Могу продолжить подбор",
        ],
    )
    def test_positive(self, text: str):
        assert looks_like_partial_recipe_reply(text) is True

    def test_empty_string(self):
        assert looks_like_partial_recipe_reply("") is False

    def test_whitespace_only(self):
        assert looks_like_partial_recipe_reply("   ") is False

    def test_excludes_open_cart(self):
        assert looks_like_partial_recipe_reply("Ингредиенты — открыть корзину") is False

    def test_excludes_share_basket(self):
        assert looks_like_partial_recipe_reply("Рецепт share_basket ссылка") is False

    def test_no_markers(self):
        assert looks_like_partial_recipe_reply("Привет, как дела?") is False


# ── should_start_fresh_context ────────────────────────────────────────


def _cart_ready_assistant_msg(
    link: str = "https://vkusvill.ru/cart/123",
) -> dict:
    """Ответ ассистента, похожий на собранную корзину."""
    return {
        "role": "assistant",
        "content": f'Собрала корзину! Итого: 1500 руб.\n<a href="{link}">Открыть корзину</a>',
    }


def _history_with_cart() -> list[dict]:
    return [
        {"role": "user", "content": "собери борщ"},
        {"role": "assistant", "content": "Ищу ингредиенты..."},
        _cart_ready_assistant_msg(),
    ]


class TestShouldStartFreshContext:
    def test_empty_history(self):
        assert should_start_fresh_context(text="собери борщ", history=None) is False

    def test_short_history(self):
        history = [{"role": "user", "content": "привет"}, {"role": "assistant", "content": "ок"}]
        assert should_start_fresh_context(text="собери борщ", history=history) is False

    def test_modify_markers_prevent_fresh(self):
        history = _history_with_cart()
        assert (
            should_start_fresh_context(text="добавь ещё молоко в корзину", history=history) is False
        )

    def test_non_cart_intent_no_fresh(self):
        history = _history_with_cart()
        assert should_start_fresh_context(text="привет", history=history) is False

    def test_no_assistant_message_no_fresh(self):
        history = [
            {"role": "user", "content": "собери борщ"},
            {"role": "user", "content": "ещё"},
            {"role": "user", "content": "купи хлеб"},
        ]
        assert should_start_fresh_context(text="собери салат", history=history) is False

    def test_no_cart_in_last_assistant_no_fresh(self):
        history = [
            {"role": "user", "content": "собери борщ"},
            {"role": "assistant", "content": "Я думаю..."},
            {"role": "assistant", "content": "Пока ищу продукты."},
        ]
        assert should_start_fresh_context(text="собери салат", history=history) is False

    def test_cart_ready_plus_new_request_triggers_fresh(self):
        history = _history_with_cart()
        assert should_start_fresh_context(text="собери салат", history=history) is True

    def test_explicit_new_cart_triggers_fresh(self):
        history = _history_with_cart()
        assert (
            should_start_fresh_context(text="собери новая корзина на торт", history=history) is True
        )

    def test_status_query_no_fresh(self):
        history = _history_with_cart()
        assert should_start_fresh_context(text="статус корзины", history=history) is False

    def test_vkusvill_link_in_assistant_triggers(self):
        history = [
            {"role": "user", "content": "собери борщ"},
            {"role": "assistant", "content": "Ищу..."},
            {
                "role": "assistant",
                "content": '<a href="https://vkusvill.ru/cart/abc">Ссылка</a>',
            },
        ]
        assert should_start_fresh_context(text="собери салат", history=history) is True

    def test_replace_markers_prevent_fresh(self):
        history = _history_with_cart()
        assert should_start_fresh_context(text="замени молоко в корзине", history=history) is False

    def test_delete_markers_prevent_fresh(self):
        history = _history_with_cart()
        assert should_start_fresh_context(text="убери сыр из корзины", history=history) is False
