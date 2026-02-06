# Стандарты кода проекта vkuswill_bot

## Стек технологий

| Компонент | Технология | Версия |
|-----------|-----------|--------|
| Язык | Python | >= 3.11 |
| Пакетный менеджер | uv | latest |
| Бот-фреймворк | aiogram | >= 3.15 |
| LLM | GigaChat API | >= 0.1.36 |
| Протокол | MCP | >= 1.9 |
| Конфигурация | pydantic-settings | >= 2.0 |
| HTTP-клиент | httpx | async |
| Тестирование | pytest + pytest-asyncio | >= 8.0 |
| HTTP-моки | respx | >= 0.22 |

## Архитектурные принципы

### Слои приложения

```
Telegram (aiogram) → handlers.py → services/ → External APIs
                                  ├── gigachat_service.py → GigaChat API
                                  └── mcp_client.py → MCP Server
```

**Правила:**
1. **handlers** — только обработка Telegram-событий, делегация в services
2. **services** — бизнес-логика, взаимодействие с внешними API
3. **config** — единая точка конфигурации через env-переменные
4. Каждый слой зависит только от нижестоящих

### Dependency Injection

Сервисы передаются в handlers через аргументы, не через глобальные переменные:

```python
# ПРАВИЛЬНО — DI через аргументы
async def text_handler(message: Message, gigachat_service: GigaChatService) -> None:
    response = await gigachat_service.generate_response(message.from_user.id, message.text)

# НЕПРАВИЛЬНО — глобальная переменная
gigachat = GigaChatService()
async def text_handler(message: Message) -> None:
    response = await gigachat.generate_response(...)
```

## Стандарты кода

### Именование

| Элемент | Стиль | Пример |
|---------|-------|--------|
| Модули | snake_case | `gigachat_service.py` |
| Функции | snake_case | `generate_response()` |
| Классы | PascalCase | `GigaChatService` |
| Константы | UPPER_SNAKE_CASE | `MAX_TOOL_CALLS` |
| Приватные | _prefix | `_parse_response()` |

### Async-паттерны

```python
# Контекстные менеджеры для ресурсов
async with httpx.AsyncClient() as client:
    response = await client.get(url)

# Асинхронное ожидание вместо блокирующего
await asyncio.sleep(1)  # НЕ time.sleep(1)

# Конкурентное выполнение где возможно
results = await asyncio.gather(task1(), task2())
```

### Логирование

```python
import logging

logger = logging.getLogger(__name__)

# ПРАВИЛЬНО — ленивое форматирование
logger.info("Обработка сообщения от user_id=%d", user_id)
logger.error("Ошибка запроса: %s", error, exc_info=True)

# НЕПРАВИЛЬНО
print(f"Обработка сообщения от {user_id}")
logger.info(f"Обработка: {user_id}")  # f-string вычисляется всегда
```

### Telegram-специфика

```python
# Разделение длинных сообщений (лимит 4096 символов)
MAX_MESSAGE_LENGTH = 4096

# Индикатор набора текста
await message.answer_chat_action(ChatAction.TYPING)

# Корректный parse_mode
await message.answer(text, parse_mode="HTML")
```

### Конфигурация

```python
# Все настройки через pydantic-settings
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    bot_token: str
    gigachat_api_key: str
    mcp_server_url: str
    max_tool_calls: int = 15
    max_history_messages: int = 50
    debug: bool = False

    model_config = SettingsConfigDict(env_file=".env")
```

## MCP-клиент: стандарты

- JSON-RPC 2.0 для коммуникации
- Persistent HTTP-соединение (keep-alive)
- Retry: 3 попытки с экспоненциальным backoff
- Кэширование описаний инструментов
- Таймауты на все запросы
- Валидация аргументов инструментов

## GigaChat-сервис: стандарты

- Per-user история сообщений
- Лимит tool_calls: `max_tool_calls` (default 15)
- Лимит истории: `max_history_messages` (default 50)
- Детекция циклов в вызовах инструментов
- Обрезка истории при превышении лимита
- Кэширование описаний MCP-инструментов

## Стандарты тестирования

### Структура теста

```python
@pytest.mark.asyncio
async def test_описание_сценария(fixture1, fixture2):
    """Тест: что именно проверяем."""
    # Arrange — подготовка
    mock_service = AsyncMock()

    # Act — действие
    result = await function_under_test(mock_service)

    # Assert — проверка
    assert result == expected
    mock_service.method.assert_called_once_with(arg)
```

### Фикстуры

- Общие фикстуры в `tests/conftest.py`
- Моки внешних сервисов через `AsyncMock`
- HTTP-моки через `respx`
- Каждый тест изолирован (нет зависимости между тестами)

### Команды

```bash
uv run pytest -v                        # Все тесты
uv run pytest --cov --cov-report=term   # С покрытием
uv run pytest -k "test_handlers"        # Фильтр по имени
uv run pytest -k "security"             # Тесты безопасности
```
