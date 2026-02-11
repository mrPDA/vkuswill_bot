# Changelog

Все значимые изменения проекта документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/),
версионирование следует [Semantic Versioning](https://semver.org/).

## [0.4.0] — 2026-02-11

### Добавлено

- **Async Cart Processor** — переход CartProcessor и ToolExecutor на async API
- **Двухуровневый кэш цен** — L1 in-memory + L2 Redis для PriceCache
- **Снимки корзины** — CartSnapshotStore сохраняет корзины в Redis
- **Async DialogManager** — асинхронный API и Redis-бэкенд для диалогов
- **CD Pipeline** — GitHub Actions workflow для автоматического деплоя на Yandex Cloud VM
- **Deploy-скрипт** — deploy.sh с Lockbox-секретами, Docker, health check
- **S3 логирование** — S3LogHandler для отправки логов в Yandex Object Storage (NDJSON)
- **Dockerfile** — multi-stage build, оптимизированный для production
- **docker-compose.yml** — локальная среда с Redis и PostgreSQL
- **Dependabot** — автообновление pip-зависимостей и GitHub Actions
- **Terraform** — инфраструктура Yandex Cloud (VM, CR, Redis, PostgreSQL, Lockbox, S3)
- **Система пользователей** — UserStore и UserMiddleware для управления пользователями
- **Admin-команды** — управление пользователями через Telegram
- **Load-тесты** — Locust + Telethon для нагрузочного тестирования
- **Миграции БД** — SQL-миграции для PostgreSQL
- **Семафор GigaChat** — ограничение параллельных запросов (15 по умолчанию)
- **Retry 429** — автоматический retry при rate-limiting от GigaChat API
- **Тесты** — расширено покрытие до 1141 теста

### Исправлено

- CI pipeline — корректная валидация merge-коммитов
- Ruff — игнорирование RUF001/RUF002/RUF003 для кириллицы
- Bandit B104 — nosec для webhook bind 0.0.0.0
- Импорт SYSTEM_PROMPT после переноса в prompts.py
- cryptography 46.0.4 → 46.0.5 (CVE-2026-26007)

### Изменено

- GigaChat God Class декомпозирован на 4 модуля (prompts, cart_processor, search_processor, tool_executor)
- MCP-клиент очищен, PriceCache выделен в отдельный модуль
- Безопасность усилена — HTML-санитизация, rate-limiter, хранилища
- Документация перенесена в docs/

## [0.3.0] — 2026-02-08

### Добавлено

- **Извлечение ингредиентов рецепта** — инструмент `recipe_ingredients` для GigaChat, автоматически разбивает блюдо на ингредиенты с расчётом количества
- **RecipeStore** — SQLite-кеш рецептов с TTL для ускорения повторных запросов
- **RECIPE_EXTRACTION_PROMPT** — специализированный промпт для извлечения ингредиентов из LLM
- **Расчёт количества по рецепту** — инструкции в системном промпте для корректного расчёта q с учётом размеров упаковок
- **ROADMAP** — план развития бота (публичный и технический)
- **Черновик статьи для Хабра** — articles/01-hook.md
- **Тесты** — расширено покрытие до 669 тестов (RecipeStore, промпты, GigaChat edge-cases, SearchProcessor, CartProcessor, Handlers, MCP Client)

### Исправлено

- Обработка `price_info` в SearchProcessor — защита от не-dict значений
- `.cursorignore` для корректной работы IDE

### Изменено

- `max_tool_calls` увеличен с 15 до 20 для поддержки рецептов с большим числом ингредиентов
- Системный промпт расширен инструкциями по работе с рецептами и расчёту количества

## [0.2.0] — 2026-02-06

### Добавлено

- **GigaChat интеграция** — ИИ-оркестрация с function calling для поиска товаров и сборки корзин
- **MCP-клиент** — JSON-RPC клиент для взаимодействия с MCP-сервером ВкусВилл (поиск, детали товаров, создание корзины)
- **Хранилище предпочтений** — SQLite-хранилище (aiosqlite) для запоминания вкусовых предпочтений пользователей
- **ThrottlingMiddleware** — rate limiting: 5 сообщений / 60 секунд на пользователя
- **Команда /reset** — сброс истории диалога
- **Верификация корзины** — автоматическая проверка, что все запрошенные товары попали в корзину
- **Кэширование цен** — цены из результатов поиска кэшируются для расчёта стоимости
- **Защита от зацикливания** — детекция повторных вызовов одних и тех же инструментов
- **CI/CD** — GitHub Actions для тестирования (Python 3.11–3.13), линтинга и автоматических релизов
- **Git hooks** — валидация Conventional Commits и запуск тестов перед push
- **Makefile** — утилиты разработки (install, test, lint, format, run)
- **Тесты безопасности** — SAST, AI Safety (prompt injection, jailbreak), Config Security, Input Validation
- **Шаблоны GitHub** — Issue templates (bug report, feature request), PR template

### Изменено

- Полностью переработан README.md с документацией функционала, архитектуры и инструкциями

## [0.1.0] — 2026-02-05

### Добавлено

- Инициализация проекта
- Базовая структура Telegram-бота на aiogram 3
- Конфигурация через pydantic-settings
