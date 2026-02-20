"""Типы данных для voice-сценария Алисы."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryResult:
    """Результат доставки ссылки в клиентский канал."""

    status: str
    channel: str
    button_title: str | None = None
    button_url: str | None = None


@dataclass(frozen=True)
class VoiceOrderResult:
    """Результат обработки голосовой команды заказа."""

    ok: bool
    voice_text: str
    cart_link: str | None = None
    total_rub: float | None = None
    items_count: int = 0
    delivery: DeliveryResult | None = None
    requires_linking: bool = False
    error_code: str | None = None
