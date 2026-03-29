"""Обработчики обратной связи по корзинам."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

if TYPE_CHECKING:
    from vkuswill_bot.services.user_store import UserStore

logger = logging.getLogger(__name__)

router = Router()


def _extract_cart_url_from_keyboard(
    keyboard: InlineKeyboardMarkup,
) -> str | None:
    """Извлечь URL корзины из клавиатуры."""
    # ... реализация извлечения URL ...
    return None


def _cart_only_keyboard(cart_url: str) -> InlineKeyboardMarkup:
    """Создать клавиатуру только с кнопкой корзины."""
    # ... реализация создания клавиатуры ...
    pass


@router.callback_query(F.data.startswith("cart_feedback_positive_"))
async def cart_feedback_positive(callback: CallbackQuery) -> None:
    """Обработчик положительной обратной связи по корзине."""
    # ... реализация обратной связи положительно ...
    pass


@router.callback_query(F.data.startswith("cart_feedback_negative_"))
async def cart_feedback_negative(callback: CallbackQuery) -> None:
    """Обработчик отрицательной обратной связи по корзине."""
    # ... реализация обратной связи отрицательно ...
    pass


@router.callback_query(F.data.startswith("cart_feedback_reason_"))
async def cart_feedback_reason(callback: CallbackQuery) -> None:
    """Обработчик выбора причины отрицательной обратной связи."""
    # ... реализация выбора причины ...
    pass