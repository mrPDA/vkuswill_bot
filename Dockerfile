# ============================================================
# Multi-stage Dockerfile для VkusVill Bot
# Оптимизирован для production в Yandex Cloud
# ============================================================

# --- Stage 1: Builder ---
FROM python:3.12-slim AS builder

WORKDIR /app

# Установить uv для быстрой установки зависимостей
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Индекс PyPI (для Pi/регионов без доступа к pypi.org передайте build-arg из compose)
ARG UV_INDEX_URL=https://pypi.org/simple
ENV UV_INDEX_URL=${UV_INDEX_URL}
# Медленные каналы — дольше ждём ответ
ENV UV_HTTP_TIMEOUT=300

# Копировать файлы зависимостей
COPY pyproject.toml uv.lock ./

# Установить зависимости (без dev/test/loadtest)
RUN uv sync --frozen --no-dev --no-editable

# Копировать исходный код, миграции и скрипты
COPY src/ src/
COPY migrations/ migrations/
COPY scripts/ scripts/

# --- Stage 2: Runtime ---
FROM python:3.12-slim AS runtime

# Метаданные
LABEL maintainer="VkusVill Bot Team"
LABEL description="Telegram bot for VkusVill — GigaChat + MCP"

# Создать непривилегированного пользователя (фиксированный UID/GID для предсказуемых прав)
RUN groupadd -r -g 10001 botuser && useradd -r -u 10001 -g botuser -d /app -s /sbin/nologin botuser

WORKDIR /app

# Копировать venv, код, миграции и скрипты из builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/migrations /app/migrations
COPY --from=builder /app/scripts /app/scripts

# CA-сертификаты НУЦ Минцифры (для SSL-верификации GigaChat)
COPY certs/ /app/certs/

# Создать директорию для данных (SQLite — legacy)
RUN mkdir -p /app/data && chown -R botuser:botuser /app

# Переключиться на непривилегированного пользователя
USER botuser

# Переменные окружения
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src"

# Порт для webhook-режима
EXPOSE 8080

# Health check: webhook → /health (HTTP или HTTPS при WEBHOOK_KEY_PATH); polling → OK
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python /app/scripts/webhook_healthcheck.py

# Запуск бота
CMD ["python", "-m", "vkuswill_bot"]
