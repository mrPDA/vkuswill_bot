# Нагрузочное тестирование VkusVill Bot

## Уровни тестирования

| Уровень | Что тестируем | Инструмент | Требует |
|---------|--------------|------------|---------|
| 1. Service | `GigaChatService.process_message()` | asyncio-скрипт | Доступ к GigaChat API, MCP |
| 2. Webhook | Полный цикл через HTTP | Locust | Бот в webhook-режиме |
| 3. Telegram | Реальные сообщения | Telethon | Тестовые Telegram-аккаунты |

## Быстрый старт

### Установка зависимостей

```bash
uv add --optional loadtest locust telethon
```

### Уровень 1: Нагрузка на сервисный слой

Самый полезный тест — стреляем напрямую в `process_message()`, минуя Telegram.
Находит узкие места: GigaChat API, MCP, Redis, корзина.

```bash
# 50 виртуальных пользователей, 100 сообщений, 10 RPS
uv run python loadtests/service_load_test.py \
    --users 50 \
    --messages 100 \
    --rps 10

# Только burst-тест (все сообщения одновременно)
uv run python loadtests/service_load_test.py \
    --users 200 \
    --messages 200 \
    --rps 0 \
    --burst
```

### Уровень 2: Webhook (Locust)

Переключи бота в webhook-режим (`USE_WEBHOOK=true`) и стреляй фейковыми Update-ами.

```bash
# Запуск Locust с веб-интерфейсом
uv run locust -f loadtests/locustfile.py --host http://localhost:8080

# Headless-режим: 100 пользователей, ramp-up 10/сек, 5 минут
uv run locust -f loadtests/locustfile.py \
    --host http://localhost:8080 \
    --users 100 \
    --spawn-rate 10 \
    --run-time 5m \
    --headless
```

### Уровень 3: Реальные Telegram-сообщения

> ⚠️ Требует тестовые Telegram-аккаунты. Используй с осторожностью — Telegram может заблокировать за спам.

```bash
# Получить api_id и api_hash на https://my.telegram.org
export TELEGRAM_API_ID=12345
export TELEGRAM_API_HASH=abcdef...
export TELEGRAM_BOT_USERNAME=your_bot_username

uv run python loadtests/telegram_load_test.py \
    --sessions loadtests/sessions/ \
    --users 5 \
    --messages 20 \
    --delay 3.0
```

## Что измеряем

| Метрика | Описание | Целевое значение |
|---------|----------|-----------------|
| p50 latency | Медианное время ответа | < 5 сек |
| p95 latency | 95-й перцентиль | < 15 сек |
| p99 latency | 99-й перцентиль | < 30 сек |
| Error rate | Процент ошибок | < 1% |
| Throughput | Обработанных сообщений/сек | >= 5 RPS |
| GigaChat concurrency | Параллельные запросы к GigaChat | <= 15 |
| Memory usage | Потребление RAM | < 512 Mi |

## Сценарии нагрузки

### 1. Smoke test
- 5 пользователей, 10 сообщений, 1 RPS
- Цель: убедиться что всё работает

### 2. Нормальная нагрузка
- 50 пользователей, 200 сообщений, 5 RPS
- Цель: обычный день

### 3. Хабр-эффект (пик)
- 500 пользователей, 1000 сообщений, 50 RPS
- Цель: первые часы после публикации

### 4. Stress test
- 1000 пользователей, burst, без ограничения RPS
- Цель: найти точку отказа

## Интерпретация результатов

### Узкие места (bottlenecks)

| Симптом | Вероятная причина | Решение |
|---------|------------------|---------|
| Высокая латентность, низкий error rate | GigaChat API медленный | Увеличить `gigachat_max_concurrent`, кэширование |
| 429 ошибки от GigaChat | Превышен лимит RPS | Token bucket, очередь, увеличить квоту |
| Таймауты MCP | MCP-сервер не справляется | Кэширование результатов поиска, retry |
| OOM (Out of Memory) | Утечка памяти в диалогах/кэше | Проверить лимиты PriceCache, MAX_CONVERSATIONS |
| Rate limit от бота | ThrottlingMiddleware | Увеличить `rate_limit` или `period` |
| Redis timeout | Redis перегружен | Увеличить ресурсы, connection pool |
