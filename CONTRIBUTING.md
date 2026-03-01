# Руководство по внесению вклада в vkuswill_bot

Спасибо за интерес к проекту! Это руководство поможет вам внести свой вклад.

## 📋 Оглавление

- [Начало работы](#начало-работы)
- [Процесс разработки](#процесс-разработки)
- [Стандарты кода](#стандарты-кода)
- [Тестирование](#тестирование)
- [Pull Request](#pull-request)
- [Правила ведения репозитория](#правила-ведения-репозитория)

## 🚀 Начало работы

### Требования

- Python 3.11+
- uv (пакетный менеджер)
- Git

### Установка проекта

```bash
# 1. Склонируйте репозиторий
git clone <repository-url>
cd vkuswill_bot

# 2. Установите зависимости
uv sync

# 3. Скопируйте конфигурацию
cp .env.example .env

# 4. Заполните переменные окружения в .env
# BOT_TOKEN=your_telegram_bot_token
# LLM_API_KEY=your_api_key
# LLM_MODEL=your_model_id

# 5. Запустите тесты
uv run pytest
```

## 🔄 Процесс разработки

### 1. Создайте issue

Перед началом работы создайте issue с описанием:
- Что вы хотите сделать
- Зачем это нужно
- Как планируете реализовать

### 2. Создайте ветку

```bash
# Для новой функции
git checkout -b feature/your-feature-name

# Для исправления бага
git checkout -b fix/issue-number-description
```

Формат: `feature/<name>`, `fix/<issue>-<desc>`, `docs/<desc>`.

### 3. Внесите изменения

- Следуйте стандартам кода проекта
- Пишите понятный и читаемый код
- Добавляйте docstrings для функций и классов
- Обрабатывайте ошибки корректно

### 4. Напишите тесты

```bash
# Запустите тесты
uv run pytest

# Проверьте покрытие
uv run pytest --cov=src/vkuswill_bot
```

### 5. Создайте коммиты

Используйте формат Conventional Commits:

```bash
git commit -m "feat(bot): добавить команду /help"
git commit -m "fix(gigachat): исправить обработку ошибок"
git commit -m "test: добавить тесты для MCP клиента"
```

Типы: `feat`, `fix`, `test`, `docs`, `refactor`, `chore`. Scope — модуль: `bot`, `agents`, `alice`, `services`, `infra`.

### 6. Отправьте изменения

```bash
# Синхронизируйте с main
git fetch origin
git rebase origin/main

# Отправьте в remote
git push origin feature/your-feature-name
```

### 7. Создайте Pull Request

Следуйте шаблону PR:

```markdown
## Что изменено

Краткое описание изменений

## Зачем

Объяснение необходимости изменений

## Как проверить

1. Шаги для тестирования
2. Команды для запуска
3. Ожидаемый результат

## Чеклист

- [ ] Код протестирован локально
- [ ] Добавлены/обновлены тесты
- [ ] Документация обновлена
- [ ] Нет конфликтов с main
```

PR должен содержать описание, ссылку на issue и чеклист проверки.

## 💻 Стандарты кода

### Python стиль

```python
# Хорошо
async def process_message(message: Message) -> None:
    """Обрабатывает входящее сообщение."""
    try:
        response = await chat_engine.process_message(user_id, message.text)
        await message.answer(response)
    except Exception as e:
        logger.error("Ошибка обработки сообщения: %s", e)
        await message.answer("Произошла ошибка. Попробуйте позже.")

# Плохо
def process(msg):
    resp = engine.gen(msg.text)
    msg.answer(resp)
```

### Структура кода

- **Используйте type hints** для всех функций
- **Пишите docstrings** для публичных функций и классов
- **Обрабатывайте исключения** явно
- **Логируйте** важные события и ошибки
- **Не используйте** `print()` для отладки в production коде

### Форматирование

```bash
# Линтер (обязателен перед коммитом)
uv run ruff check src/ tests/
```

## 🧪 Тестирование

### Написание тестов

```python
import pytest
from unittest.mock import AsyncMock, patch

async def test_message_handler():
    """Тест обработчика сообщений."""
    # Arrange
    mock_message = AsyncMock()
    mock_message.text = "Привет"
    
    # Act
    with patch('vkuswill_bot.services.gigachat_service') as mock_service:
        mock_service.generate_response.return_value = "Привет!"
        await process_message(mock_message)
    
    # Assert
    mock_message.answer.assert_called_once_with("Привет!")
```

### Запуск тестов

```bash
# Все тесты
uv run pytest

# Конкретный файл
uv run pytest tests/test_handlers.py

# С покрытием
uv run pytest --cov=src/vkuswill_bot --cov-report=html

# С подробным выводом
uv run pytest -v
```

## 📝 Pull Request

### Чеклист перед созданием PR

- [ ] Код протестирован локально
- [ ] Все тесты проходят
- [ ] Добавлены новые тесты (если нужно)
- [ ] Документация обновлена (если нужно)
- [ ] Нет конфликтов с main
- [ ] Коммиты следуют Conventional Commits
- [ ] PR имеет понятное описание

### Процесс ревью

1. Минимум 1 approve от мэйнтейнера
2. Все комментарии с `[BLOCKER]` должны быть разрешены
3. Все тесты должны проходить
4. Нет конфликтов слияния

Комментарии с `[BLOCKER]` блокируют мёрж.

## 📚 Правила ведения репозитория

Проект использует следующие стандарты:

1. **Conventional Commits** — формат коммитов (`feat`, `fix`, `test`, ...)
2. **Именование веток** — `feature/`, `fix/`, `docs/`
3. **Semantic Versioning** — версионирование через CHANGELOG.md
4. **Ruff** — линтер и форматтер (`uv run ruff check`)
5. **pytest** — обязательные тесты для новой логики

## ❓ Вопросы

Если у вас есть вопросы:

1. Проверьте существующие issues
2. Создайте новый issue с меткой `question`
3. Опишите проблему подробно

## 📜 Лицензия

Внося вклад в проект, вы соглашаетесь с тем, что ваш код будет распространяться под той же лицензией, что и проект.

## 🙏 Благодарности

Спасибо за ваш вклад в развитие проекта!

---

**Полезные ссылки:**
- [Документация Telegram Bot API](https://core.telegram.org/bots/api)
- [Документация aiogram](https://docs.aiogram.dev/)
- [Yandex Cloud AI Studio](https://yandex.cloud/ru/services/ai-studio)
- [Model Context Protocol](https://modelcontextprotocol.io/)
