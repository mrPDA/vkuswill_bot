"""Locust-сценарий для нагрузочного тестирования VkusVill Bot (webhook-режим).

Эмулирует Telegram Update-ы, отправляя их на webhook-эндпоинт бота.

Требования:
    - Бот запущен в webhook-режиме (USE_WEBHOOK=true, WEBHOOK_PORT=8080)
    - Locust установлен: uv add --optional loadtest locust

Запуск:
    # С веб-интерфейсом (http://localhost:8089)
    uv run locust -f loadtests/locustfile.py --host http://localhost:8080

    # Headless (для CI/CD)
    uv run locust -f loadtests/locustfile.py \
        --host http://localhost:8080 \
        --users 100 --spawn-rate 10 --run-time 5m --headless
"""

from __future__ import annotations

import json
import random
import time

from locust import HttpUser, between, task

# ---------------------------------------------------------------------------
# Шаблон Telegram Update (фейковый, но валидный для aiogram)
# ---------------------------------------------------------------------------

_UPDATE_COUNTER = 0


def _make_update(user_id: int, text: str) -> dict:
    """Собрать фейковый Telegram Update (Message)."""
    global _UPDATE_COUNTER  # noqa: PLW0603
    _UPDATE_COUNTER += 1

    return {
        "update_id": _UPDATE_COUNTER,
        "message": {
            "message_id": _UPDATE_COUNTER,
            "from": {
                "id": user_id,
                "is_bot": False,
                "first_name": f"LoadTest_{user_id}",
                "language_code": "ru",
            },
            "chat": {
                "id": user_id,
                "first_name": f"LoadTest_{user_id}",
                "type": "private",
            },
            "date": int(time.time()),
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# Запросы (имитация реальных пользователей)
# ---------------------------------------------------------------------------

SEARCH_QUERIES = [
    "Хочу купить молоко, хлеб и сыр",
    "Подбери продукты для завтрака на двоих",
    "Найди безглютеновый хлеб",
    "Что есть из веганских продуктов?",
    "Собери корзину для ужина, бюджет 1000 руб",
    "Покажи акции на молочные продукты",
    "Хочу заказать фрукты и овощи",
    "Найди протеиновые батончики",
    "Что есть из готовой еды?",
    "Подбери продукты для салата Цезарь",
]

FOLLOWUP_QUERIES = [
    "Добавь ещё сок апельсиновый",
    "Замени молоко на безлактозное",
    "Покажи что-то подешевле",
    "Убери сыр из корзины",
    "Сколько получается всего?",
]


# ---------------------------------------------------------------------------
# Locust User
# ---------------------------------------------------------------------------


class TelegramBotUser(HttpUser):
    """Виртуальный пользователь Telegram.

    Имитирует реальное поведение:
    1. Отправляет /start
    2. Делает поисковый запрос
    3. Уточняет/дополняет
    4. Иногда сбрасывает диалог
    """

    # Интервал между запросами: 2-10 секунд (имитация реального пользователя)
    wait_time = between(2, 10)

    def on_start(self) -> None:
        """Инициализация: уникальный user_id для каждого Locust-пользователя."""
        self.user_id = random.randint(800_000_000, 899_999_999)
        self.message_count = 0

    def _send_update(self, text: str, name: str = "message") -> None:
        """Отправить фейковый Telegram Update на webhook."""
        update = _make_update(self.user_id, text)
        # aiogram webhook ожидает POST на корень или /webhook
        self.client.post(
            "/webhook",
            json=update,
            name=name,
            headers={"Content-Type": "application/json"},
        )
        self.message_count += 1

    @task(1)
    def start_command(self) -> None:
        """Отправить /start."""
        self._send_update("/start", name="/start")

    @task(5)
    def search_products(self) -> None:
        """Основной сценарий: поиск продуктов."""
        query = random.choice(SEARCH_QUERIES)
        self._send_update(query, name="search")

    @task(3)
    def followup(self) -> None:
        """Follow-up запрос (уточнение)."""
        query = random.choice(FOLLOWUP_QUERIES)
        self._send_update(query, name="followup")

    @task(1)
    def reset_dialog(self) -> None:
        """Сброс диалога."""
        self._send_update("/reset", name="/reset")
