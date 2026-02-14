"""Утилиты защиты персональных данных (PII).

Общий модуль для маскировки PII, хеширования идентификаторов
и санитизации данных перед логированием.

Используется в:
- ``s3_log_handler.py`` — маскировка PII в S3-логах
- ``langfuse_tracing.py`` — анонимизация для LLM-observability
- ``gigachat_service.py`` — санитизация аргументов tool calls
"""

from __future__ import annotations

import hashlib
import json
import re


# ---------------------------------------------------------------------------
# Хеширование идентификаторов
# ---------------------------------------------------------------------------


def hash_user_id(user_id: str | int) -> str:
    """Хешировать Telegram user_id (SHA-256, 12 символов).

    Одинаковый user_id всегда даёт одинаковый хеш — можно группировать
    сессии одного пользователя без раскрытия Telegram ID.

    >>> hash_user_id(123456789)
    'c27db0c8e6f0'
    """
    raw = str(user_id).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


# ---------------------------------------------------------------------------
# PII-маскировка текста
# ---------------------------------------------------------------------------

# Паттерны PII для маскировки
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Телефоны: +7..., 8..., и вариации
    (
        re.compile(r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),
        "[PHONE]",
    ),
    # Email
    (
        re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
        "[EMAIL]",
    ),
    # Номера карт (16 цифр, возможно через пробелы/дефисы)
    (
        re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
        "[CARD]",
    ),
    # ИНН (10 или 12 цифр, только целое слово)
    (
        re.compile(r"\b\d{10}(?:\d{2})?\b"),
        "[INN]",
    ),
    # СНИЛС (формат XXX-XXX-XXX XX)
    (
        re.compile(r"\b\d{3}-\d{3}-\d{3}\s?\d{2}\b"),
        "[SNILS]",
    ),
]


def mask_pii(text: str) -> str:
    """Маскировать PII (телефоны, email, карты, ИНН, СНИЛС) в тексте.

    >>> mask_pii("Позвоните мне: +7 (999) 123-45-67")
    'Позвоните мне: [PHONE]'
    >>> mask_pii("email: user@example.com")
    'email: [EMAIL]'
    """
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Санитизация аргументов tool calls для логирования
# ---------------------------------------------------------------------------

# Максимальная длина значения аргумента в логах
_MAX_ARG_VALUE_LENGTH = 80


def sanitize_tool_args(tool_name: str, args: dict) -> str:
    """Санитизировать аргументы tool call для безопасного логирования.

    - Маскирует PII в строковых значениях.
    - Обрезает длинные значения.
    - Для списков показывает только количество элементов.

    Returns:
        JSON-строка с санитизированными аргументами.
    """
    sanitized: dict = {}
    for key, value in args.items():
        if isinstance(value, str):
            # Маскировка PII
            safe_value = mask_pii(value)
            # Обрезка длинных строк
            if len(safe_value) > _MAX_ARG_VALUE_LENGTH:
                safe_value = safe_value[:_MAX_ARG_VALUE_LENGTH] + "..."
            sanitized[key] = safe_value
        elif isinstance(value, list):
            # Для списков — только количество элементов (не раскрываем содержимое)
            sanitized[key] = f"[{len(value)} items]"
        elif isinstance(value, dict):
            sanitized[key] = f"{{...{len(value)} keys}}"
        else:
            sanitized[key] = value

    return json.dumps(sanitized, ensure_ascii=False)
