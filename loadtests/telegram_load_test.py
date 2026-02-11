"""Нагрузочное тестирование через реальные Telegram-сообщения (Telethon).

Отправляет реальные сообщения боту от нескольких Telegram-аккаунтов.
Это самый реалистичный, но самый сложный в настройке тест.

⚠️ ВАЖНО:
- Требует тестовые Telegram-аккаунты (не основной!)
- Telegram может заблокировать за спам при большой нагрузке
- Не злоупотребляйте: 5-10 аккаунтов, 10-20 сообщений, задержка 3+ сек

Подготовка:
    1. Получить api_id и api_hash на https://my.telegram.org
    2. Для каждого аккаунта запустить авторизацию:
       python -c "
       from telethon.sync import TelegramClient
       client = TelegramClient('loadtests/sessions/user1', API_ID, 'API_HASH')
       client.start()
       "
    3. Файлы сессий сохранятся в loadtests/sessions/

Использование:
    export TELEGRAM_API_ID=12345
    export TELEGRAM_API_HASH=abcdef...
    export TELEGRAM_BOT_USERNAME=vkuswill_test_bot

    uv run python loadtests/telegram_load_test.py \
        --sessions loadtests/sessions/ \
        --users 5 \
        --messages 10 \
        --delay 3.0
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Запросы
# ---------------------------------------------------------------------------

QUERIES = [
    "Хочу купить молоко и хлеб",
    "Подбери продукты для завтрака",
    "Найди безглютеновый хлеб",
    "Собери корзину для ужина до 1000 руб",
    "Покажи акции на молочку",
    "Хочу фрукты и овощи свежие",
    "Найди протеиновые батончики",
    "Что есть из готовой еды?",
    "Подбери ингредиенты для пасты",
    "Есть безлактозное молоко?",
]


@dataclass
class TelethonResult:
    user: str
    query: str
    latency_ms: float
    success: bool
    response: str = ""
    error: str = ""


@dataclass
class TelethonReport:
    results: list[TelethonResult] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    def add(self, result: TelethonResult) -> None:
        self.results.append(result)

    def print_report(self) -> None:
        successful = [r for r in self.results if r.success]
        failed = [r for r in self.results if not r.success]
        latencies = [r.latency_ms for r in successful]

        duration = self.end_time - self.start_time

        print("\n" + "=" * 70)
        print("  ОТЧЁТ: TELEGRAM LOAD TEST (Telethon)")
        print("=" * 70)
        print(f"  Длительность:    {duration:.1f} сек")
        print(f"  Всего запросов:  {len(self.results)}")
        print(f"  Успешных:        {len(successful)}")
        print(f"  Ошибок:          {len(failed)}")
        if latencies:
            print(f"\n  Латентность (мс) — время до ответа бота:")
            print(f"    min:           {min(latencies):.0f}")
            print(f"    p50:           {sorted(latencies)[len(latencies)//2]:.0f}")
            print(f"    max:           {max(latencies):.0f}")
            print(f"    среднее:       {statistics.mean(latencies):.0f}")
        if failed:
            print(f"\n  Ошибки:")
            for r in failed:
                print(f"    [{r.user}] {r.error[:80]}")
        print("=" * 70)


async def run_telethon_user(
    session_path: str,
    api_id: int,
    api_hash: str,
    bot_username: str,
    num_messages: int,
    delay: float,
    report: TelethonReport,
) -> None:
    """Отправить сообщения боту от одного Telegram-аккаунта."""
    try:
        from telethon import TelegramClient
    except ImportError:
        print("❌ Telethon не установлен. Установите: uv add --optional loadtest telethon")
        return

    session_name = Path(session_path).stem
    client = TelegramClient(session_path, api_id, api_hash)

    try:
        await client.start()
        print(f"  ✓ {session_name}: авторизован")

        for i in range(num_messages):
            query = random.choice(QUERIES)
            start = time.monotonic()

            try:
                # Отправляем сообщение боту
                await client.send_message(bot_username, query)

                # Ждём ответа (polling с таймаутом)
                response_text = ""
                timeout = 60.0  # макс. ожидание ответа
                poll_start = time.monotonic()

                while time.monotonic() - poll_start < timeout:
                    await asyncio.sleep(1.0)
                    # Получаем последние сообщения от бота
                    messages = await client.get_messages(bot_username, limit=1)
                    if messages and not messages[0].out:
                        # Это ответ бота (не наше сообщение)
                        msg_time = messages[0].date.timestamp()
                        if msg_time >= start:
                            response_text = messages[0].text or ""
                            break

                latency_ms = (time.monotonic() - start) * 1000

                if response_text:
                    result = TelethonResult(
                        user=session_name, query=query,
                        latency_ms=latency_ms, success=True,
                        response=response_text[:100],
                    )
                else:
                    result = TelethonResult(
                        user=session_name, query=query,
                        latency_ms=latency_ms, success=False,
                        error="Timeout: бот не ответил за 60 сек",
                    )

            except Exception as e:
                latency_ms = (time.monotonic() - start) * 1000
                result = TelethonResult(
                    user=session_name, query=query,
                    latency_ms=latency_ms, success=False,
                    error=str(e),
                )

            report.add(result)
            status = "✓" if result.success else "✗"
            print(
                f"  {status} {session_name} [{i+1}/{num_messages}] "
                f"{result.latency_ms:.0f}ms — \"{query[:40]}\"",
            )

            if i < num_messages - 1:
                await asyncio.sleep(delay)

    finally:
        await client.disconnect()


async def run_telegram_load_test(
    sessions_dir: str,
    num_users: int,
    messages_per_user: int,
    delay: float,
) -> None:
    """Запуск нагрузочного теста через Telethon."""
    api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")
    bot_username = os.environ.get("TELEGRAM_BOT_USERNAME", "")

    if not all([api_id, api_hash, bot_username]):
        print("❌ Установите переменные окружения:")
        print("   TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_USERNAME")
        return

    # Найти файлы сессий
    sessions_path = Path(sessions_dir)
    session_files = sorted(sessions_path.glob("*.session"))

    if not session_files:
        print(f"❌ Нет файлов сессий в {sessions_dir}")
        print("   Создайте сессии: см. docstring в начале файла")
        return

    # Ограничиваем количество пользователей числом сессий
    actual_users = min(num_users, len(session_files))
    if actual_users < num_users:
        print(
            f"⚠️  Запрошено {num_users} пользователей, "
            f"но найдено {len(session_files)} сессий. Используем {actual_users}.",
        )

    print("=" * 70)
    print("  TELEGRAM LOAD TEST (Telethon)")
    print("=" * 70)
    print(f"  Пользователей:  {actual_users}")
    print(f"  Сообщений:      {messages_per_user} на пользователя")
    print(f"  Задержка:       {delay} сек между сообщениями")
    print(f"  Бот:            @{bot_username}")
    print("=" * 70)

    report = TelethonReport()
    report.start_time = time.monotonic()

    tasks = [
        run_telethon_user(
            session_path=str(session_files[i]).replace(".session", ""),
            api_id=api_id,
            api_hash=api_hash,
            bot_username=bot_username,
            num_messages=messages_per_user,
            delay=delay,
            report=report,
        )
        for i in range(actual_users)
    ]

    await asyncio.gather(*tasks, return_exceptions=True)
    report.end_time = time.monotonic()
    report.print_report()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Нагрузочное тестирование через реальные Telegram-сообщения",
    )
    parser.add_argument(
        "--sessions", default="loadtests/sessions/",
        help="Директория с файлами сессий Telethon",
    )
    parser.add_argument("--users", type=int, default=3)
    parser.add_argument("--messages", type=int, default=5)
    parser.add_argument("--delay", type=float, default=3.0)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run_telegram_load_test(
        sessions_dir=args.sessions,
        num_users=args.users,
        messages_per_user=args.messages,
        delay=args.delay,
    ))


if __name__ == "__main__":
    main()
