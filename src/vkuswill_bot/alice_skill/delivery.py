"""Доставка ссылки на корзину в second-screen канал."""

from __future__ import annotations

from typing import Protocol

from vkuswill_bot.alice_skill.models import DeliveryResult


class LinkDeliveryAdapter(Protocol):
    """Единый интерфейс отправки ссылки в клиентский канал."""

    async def deliver_cart_link(
        self,
        user_ref: str,
        cart_link: str,
        total_rub: float | None,
        items_count: int,
    ) -> DeliveryResult:
        """Доставить ссылку на корзину пользователю."""


class AliceAppDeliveryAdapter:
    """Доставка ссылки в приложение Алисы через кнопку в ответе навыка."""

    async def deliver_cart_link(
        self,
        user_ref: str,
        cart_link: str,
        total_rub: float | None,
        items_count: int,
    ) -> DeliveryResult:
        del user_ref, total_rub, items_count
        return DeliveryResult(
            status="delivered",
            channel="alice_app_card",
            button_title="Открыть корзину",
            button_url=cart_link,
        )
