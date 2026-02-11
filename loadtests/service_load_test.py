"""Нагрузочное тестирование сервисного слоя VkusVill Bot.

Стреляет напрямую в GigaChatService.process_message(), минуя Telegram.
Находит реальные узкие места: GigaChat API, MCP, Redis, корзина.

Использование:
    uv run python loadtests/service_load_test.py --users 50 --messages 100 --rps 10
    uv run python loadtests/service_load_test.py --users 200 --burst
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Типичные запросы пользователей (имитация реальных сценариев)
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
    "Найди кофе в зёрнах",
    "Хочу купить детское питание",
    "Подбери ингредиенты для пасты карбонара",
    "Есть ли безлактозное молоко?",
    "Собери набор для пикника",
]

FOLLOWUP_QUERIES = [
    "Добавь ещё сок апельсиновый",
    "Замени молоко на безлактозное",
    "Покажи что-то подешевле",
    "Убери сыр из корзины",
    "Сколько получается всего?",
    "А есть со скидкой?",
]

SIMPLE_QUERIES = [
    "Привет!",
    "Спасибо!",
    "Что ты умеешь?",
    "Помощь",
]


@dataclass
class RequestResult:
    """Результат одного запроса."""

    user_id: int
    query: str
    latency_ms: float
    success: bool
    error: str = ""
    response_length: int = 0


@dataclass
class LoadTestReport:
    """Итоговый отчёт о нагрузочном тестировании."""

    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    errors: dict[str, int] = field(default_factory=dict)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration_sec(self) -> float:
        return self.end_time - self.start_time

    @property
    def rps(self) -> float:
        if self.duration_sec <= 0:
            return 0.0
        return self.total_requests / self.duration_sec

    @property
    def error_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.failed / self.total_requests * 100

    @property
    def p50(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.5)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p95(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    @property
    def p99(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.99)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]

    def add(self, result: RequestResult) -> None:
        self.total_requests += 1
        if result.success:
            self.successful += 1
            self.latencies_ms.append(result.latency_ms)
        else:
            self.failed += 1
            err_key = result.error[:80]
            self.errors[err_key] = self.errors.get(err_key, 0) + 1

    def print_report(self) -> None:
        print("\n" + "=" * 70)
        print("  ОТЧЁТ О НАГРУЗОЧНОМ ТЕСТИРОВАНИИ")
        print("=" * 70)
        print(f"  Длительность:        {self.duration_sec:.1f} сек")
        print(f"  Всего запросов:      {self.total_requests}")
        print(f"  Успешных:            {self.successful}")
        print(f"  Ошибок:              {self.failed} ({self.error_rate:.1f}%)")
        print(f"  Throughput:          {self.rps:.2f} RPS")
        print()
        if self.latencies_ms:
            print("  Латентность (мс):")
            print(f"    min:               {min(self.latencies_ms):.0f}")
            print(f"    p50 (медиана):     {self.p50:.0f}")
            print(f"    p95:               {self.p95:.0f}")
            print(f"    p99:               {self.p99:.0f}")
            print(f"    max:               {max(self.latencies_ms):.0f}")
            print(f"    среднее:           {statistics.mean(self.latencies_ms):.0f}")
            if len(self.latencies_ms) > 1:
                print(f"    std dev:           {statistics.stdev(self.latencies_ms):.0f}")
        if self.errors:
            print()
            print("  Ошибки:")
            for err, count in sorted(self.errors.items(), key=lambda x: -x[1]):
                print(f"    [{count}x] {err}")
        print("=" * 70)


async def create_gigachat_service():
    """Инициализация GigaChatService с реальными зависимостями."""
    from vkuswill_bot.config import config
    from vkuswill_bot.services.cart_processor import CartProcessor
    from vkuswill_bot.services.dialog_manager import DialogManager
    from vkuswill_bot.services.gigachat_service import GigaChatService
    from vkuswill_bot.services.mcp_client import VkusvillMCPClient
    from vkuswill_bot.services.preferences_store import PreferencesStore
    from vkuswill_bot.services.price_cache import PriceCache, TwoLevelPriceCache
    from vkuswill_bot.services.recipe_store import RecipeStore
    from vkuswill_bot.services.redis_client import create_redis_client
    from vkuswill_bot.services.search_processor import SearchProcessor
    from vkuswill_bot.services.tool_executor import ToolExecutor

    # Увеличенный пул потоков
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=50))

    mcp_client = VkusvillMCPClient(config.mcp_server_url)
    prefs_store = PreferencesStore(config.database_path)
    recipe_store = RecipeStore(config.recipe_database_path)

    redis_client = None
    cart_snapshot_store = None
    if config.storage_backend == "redis" and config.redis_url:
        try:
            from vkuswill_bot.services.cart_snapshot_store import CartSnapshotStore
            from vkuswill_bot.services.redis_dialog_manager import RedisDialogManager

            redis_client = await create_redis_client(config.redis_url)
            dialog_manager = RedisDialogManager(
                redis=redis_client, max_history=config.max_history_messages,
            )
            price_cache = TwoLevelPriceCache(redis=redis_client)
            cart_snapshot_store = CartSnapshotStore(redis=redis_client)
        except Exception:
            price_cache = PriceCache()
            dialog_manager = DialogManager(max_history=config.max_history_messages)
    else:
        price_cache = PriceCache()
        dialog_manager = DialogManager(max_history=config.max_history_messages)

    search_processor = SearchProcessor(price_cache)
    cart_processor = CartProcessor(price_cache)
    tool_executor = ToolExecutor(
        mcp_client=mcp_client,
        search_processor=search_processor,
        cart_processor=cart_processor,
        preferences_store=prefs_store,
        cart_snapshot_store=cart_snapshot_store,
    )

    service = GigaChatService(
        credentials=config.gigachat_credentials,
        model=config.gigachat_model,
        scope=config.gigachat_scope,
        mcp_client=mcp_client,
        preferences_store=prefs_store,
        recipe_store=recipe_store,
        max_tool_calls=config.max_tool_calls,
        max_history=config.max_history_messages,
        dialog_manager=dialog_manager,
        tool_executor=tool_executor,
        gigachat_max_concurrent=config.gigachat_max_concurrent,
    )

    # Предзагрузка инструментов
    try:
        await mcp_client.get_tools()
    except Exception as e:
        print(f"⚠️  MCP инструменты не загрузились: {e}")

    return service, redis_client


def pick_query(message_idx: int) -> str:
    """Выбрать запрос в зависимости от номера сообщения."""
    if message_idx == 0:
        # Первое сообщение — поисковый запрос
        return random.choice(SEARCH_QUERIES)
    elif random.random() < 0.7:
        # 70% — follow-up
        return random.choice(FOLLOWUP_QUERIES)
    elif random.random() < 0.5:
        # 15% — новый поиск
        return random.choice(SEARCH_QUERIES)
    else:
        # 15% — простые сообщения
        return random.choice(SIMPLE_QUERIES)


async def simulate_user(
    service,
    user_id: int,
    num_messages: int,
    report: LoadTestReport,
    rps_limiter: asyncio.Semaphore | None,
    delay_between_messages: float = 0.0,
) -> None:
    """Симуляция одного пользователя: отправка серии сообщений."""
    for i in range(num_messages):
        query = pick_query(i)

        if rps_limiter:
            await rps_limiter.acquire()

        start = time.monotonic()
        try:
            response = await service.process_message(user_id, query)
            latency_ms = (time.monotonic() - start) * 1000
            result = RequestResult(
                user_id=user_id,
                query=query,
                latency_ms=latency_ms,
                success=True,
                response_length=len(response),
            )
        except Exception as e:
            latency_ms = (time.monotonic() - start) * 1000
            result = RequestResult(
                user_id=user_id,
                query=query,
                latency_ms=latency_ms,
                success=False,
                error=str(e),
            )

        report.add(result)

        status = "✓" if result.success else "✗"
        print(
            f"  {status} user={user_id} msg={i+1}/{num_messages} "
            f"latency={result.latency_ms:.0f}ms "
            f"query=\"{query[:40]}...\"",
        )

        if delay_between_messages > 0 and i < num_messages - 1:
            await asyncio.sleep(delay_between_messages)


async def rps_token_refiller(
    semaphore: asyncio.Semaphore,
    target_rps: float,
    total_tokens: int,
) -> None:
    """Пополняет семафор с заданной частотой (token bucket)."""
    interval = 1.0 / target_rps
    for _ in range(total_tokens):
        semaphore.release()
        await asyncio.sleep(interval)


async def run_load_test(
    num_users: int,
    messages_per_user: int,
    target_rps: float,
    burst: bool,
) -> None:
    """Основная функция нагрузочного тестирования."""
    total_messages = num_users * messages_per_user

    print("=" * 70)
    print("  НАГРУЗОЧНОЕ ТЕСТИРОВАНИЕ VkusVill Bot")
    print("=" * 70)
    print(f"  Виртуальных пользователей: {num_users}")
    print(f"  Сообщений на пользователя: {messages_per_user}")
    print(f"  Всего сообщений:           {total_messages}")
    print(f"  Целевой RPS:               {'burst (без лимита)' if burst else target_rps}")
    print("=" * 70)
    print()

    print("Инициализация сервисов...")
    service, redis_client = await create_gigachat_service()
    print("✓ Сервисы инициализированы\n")

    report = LoadTestReport()

    # RPS limiter (token bucket через семафор)
    rps_limiter = None
    refiller_task = None
    if not burst and target_rps > 0:
        rps_limiter = asyncio.Semaphore(0)
        refiller_task = asyncio.create_task(
            rps_token_refiller(rps_limiter, target_rps, total_messages),
        )

    # Генерируем уникальные user_id для каждого виртуального пользователя
    base_user_id = 900_000_000  # чтобы не пересечься с реальными
    user_ids = [base_user_id + i for i in range(num_users)]

    report.start_time = time.monotonic()

    # Запускаем всех виртуальных пользователей параллельно
    tasks = [
        simulate_user(
            service=service,
            user_id=uid,
            num_messages=messages_per_user,
            report=report,
            rps_limiter=rps_limiter,
            delay_between_messages=0.5 if not burst else 0.0,
        )
        for uid in user_ids
    ]

    print(f"Запуск {num_users} виртуальных пользователей...\n")
    await asyncio.gather(*tasks, return_exceptions=True)

    report.end_time = time.monotonic()

    if refiller_task:
        refiller_task.cancel()
        try:
            await refiller_task
        except asyncio.CancelledError:
            pass

    # Очистка
    print("\nОчистка ресурсов...")
    await service.close()
    if redis_client:
        from vkuswill_bot.services.redis_client import close_redis_client
        await close_redis_client(redis_client)

    report.print_report()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Нагрузочное тестирование VkusVill Bot (сервисный слой)",
    )
    parser.add_argument(
        "--users", type=int, default=10,
        help="Количество виртуальных пользователей (по умолчанию: 10)",
    )
    parser.add_argument(
        "--messages", type=int, default=3,
        help="Сообщений на пользователя (по умолчанию: 3)",
    )
    parser.add_argument(
        "--rps", type=float, default=5.0,
        help="Целевой RPS (запросов в секунду, по умолчанию: 5)",
    )
    parser.add_argument(
        "--burst", action="store_true",
        help="Burst-режим: все запросы без ограничения RPS",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Подробное логирование",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    # Заглушаем шумные логгеры
    logging.getLogger("gigachat").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    asyncio.run(
        run_load_test(
            num_users=args.users,
            messages_per_user=args.messages,
            target_rps=args.rps,
            burst=args.burst,
        ),
    )


if __name__ == "__main__":
    main()
