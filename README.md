# Vkuswill Bot

Telegram-бот для заказа продуктов из ВкусВилл с использованием GigaChat и MCP (Model Context Protocol).

## Описание

Бот позволяет пользователям заказывать продукты из ВкусВилл через Telegram, используя естественный язык. Интеграция с GigaChat обеспечивает умную обработку запросов, а MCP-сервер предоставляет доступ к функционалу ВкусВилл.

## Возможности

- Заказ продуктов через Telegram
- Интеграция с GigaChat для обработки естественного языка
- Использование MCP для взаимодействия с API ВкусВилл
- Поддержка истории диалогов
- Настраиваемые лимиты на количество вызовов инструментов

## Требования

- Python >= 3.11
- uv (менеджер пакетов)
- Telegram Bot Token
- GigaChat API ключ
- Доступ к MCP-серверу ВкусВилл

## Установка

1. Клонируйте репозиторий:
```bash
git clone https://github.com/yourusername/vkuswill_bot.git
cd vkuswill_bot
```

2. Установите зависимости через uv:
```bash
uv sync
```

3. Создайте файл `.env` на основе `.env.example`:
```bash
cp .env.example .env
```

4. Заполните необходимые переменные окружения в `.env`:
- `BOT_TOKEN` - токен вашего Telegram бота
- `GIGACHAT_CREDENTIALS` - ключ авторизации GigaChat
- `GIGACHAT_MODEL` - модель GigaChat (по умолчанию: GigaChat)
- `GIGACHAT_SCOPE` - область доступа GigaChat (по умолчанию: GIGACHAT_API_PERS)
- `MCP_SERVER_URL` - URL MCP-сервера ВкусВилл
- `DEBUG` - режим отладки (true/false)

## Запуск

### Через скрипт (рекомендуется):
```bash
./run.sh
```

### Напрямую через Python:
```bash
uv run python -m vkuswill_bot
```

### Остановка:
```bash
./stop.sh
```

## Структура проекта

```
vkuswill_bot/
├── src/
│   └── vkuswill_bot/
│       ├── __init__.py
│       ├── __main__.py          # Точка входа
│       ├── config.py             # Конфигурация
│       ├── bot/
│       │   ├── __init__.py
│       │   └── handlers.py       # Обработчики Telegram
│       └── services/
│           ├── __init__.py
│           ├── gigachat_service.py  # Сервис GigaChat
│           └── mcp_client.py        # MCP клиент
├── tests/                        # Тесты
├── .env.example                  # Пример конфигурации
├── pyproject.toml                # Зависимости проекта
├── run.sh                        # Скрипт запуска
└── stop.sh                       # Скрипт остановки
```

## Тестирование

Запуск тестов:
```bash
uv run pytest
```

С покрытием кода:
```bash
uv run pytest --cov=src/vkuswill_bot
```

## Разработка

Проект использует:
- **aiogram** - фреймворк для Telegram ботов
- **gigachat** - клиент для GigaChat API
- **mcp** - библиотека для работы с Model Context Protocol
- **pydantic-settings** - управление настройками
- **pytest** - тестирование

## Лицензия

[Укажите лицензию]

## Автор

[Ваше имя]
