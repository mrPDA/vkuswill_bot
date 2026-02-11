"""Langfuse-трейсинг для прозрачности LLM-вызовов.

Оборачивает взаимодействия с GigaChat: каждое сообщение пользователя
создаёт trace, каждый вызов LLM — generation, каждый tool call — span.

Если Langfuse не сконфигурирован (langfuse_enabled=False), используется
NoOpTracer — все вызовы трейсинга становятся no-op.

Анонимизация: user_id хешируется (SHA-256, 12 символов), содержимое
сообщений сохраняется (нужно для анализа качества), но можно включить
маскировку через ``anonymize_messages=True``.

Совместимо с Langfuse Python SDK v2 (langfuse.trace() API).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)


# ── Анонимизация ─────────────────────────────────────────────────────


def _anonymize_user_id(user_id: str | int) -> str:
    """Хешировать Telegram user_id (SHA-256, 12 символов).

    Одинаковый user_id всегда даёт одинаковый хеш — можно группировать
    сессии одного пользователя без раскрытия Telegram ID.
    """
    raw = str(user_id).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


# Паттерны PII для маскировки в тексте сообщений
_PII_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Телефоны: +7..., 8..., и вариации
    (re.compile(r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"), "[PHONE]"),
    # Email
    (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"), "[EMAIL]"),
    # Номера карт (16 цифр, возможно через пробелы/дефисы)
    (re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"), "[CARD]"),
]


def _mask_pii(text: str) -> str:
    """Маскировать PII (телефоны, email, номера карт) в тексте."""
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class LangfuseTrace:
    """Обёртка над Langfuse trace с удобным API (SDK v2)."""

    def __init__(self, trace: Any) -> None:
        self._trace = trace

    @property
    def id(self) -> str:
        """ID трейса."""
        return self._trace.id  # type: ignore[no-any-return]

    def generation(
        self,
        *,
        name: str,
        model: str,
        input: Any = None,
        model_parameters: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LangfuseGeneration:
        """Создать generation-span для LLM-вызова."""
        gen = self._trace.generation(
            name=name,
            model=model,
            input=input,
            model_parameters=model_parameters,
            metadata=metadata,
        )
        return LangfuseGeneration(gen)

    def span(
        self,
        *,
        name: str,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> LangfuseSpan:
        """Создать span для tool call или другой операции."""
        sp = self._trace.span(
            name=name,
            input=input,
            metadata=metadata,
        )
        return LangfuseSpan(sp)

    def update(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Обновить trace (например, итоговый ответ)."""
        self._trace.update(output=output, metadata=metadata)


class LangfuseGeneration:
    """Обёртка над Langfuse generation (SDK v2)."""

    def __init__(self, generation: Any) -> None:
        self._generation = generation
        self._start_time = time.monotonic()

    def end(
        self,
        *,
        output: Any = None,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
        status_message: str | None = None,
    ) -> None:
        """Завершить generation (фиксирует output и usage)."""
        self._generation.end(
            output=output,
            usage=usage,
            metadata=metadata,
            level=level,
            status_message=status_message,
        )

    @property
    def latency_ms(self) -> float:
        """Прошедшее время в мс с момента создания generation."""
        return (time.monotonic() - self._start_time) * 1000


class LangfuseSpan:
    """Обёртка над Langfuse span (SDK v2)."""

    def __init__(self, span: Any) -> None:
        self._span = span
        self._start_time = time.monotonic()

    def end(
        self,
        *,
        output: Any = None,
        metadata: dict[str, Any] | None = None,
        level: str = "DEFAULT",
        status_message: str | None = None,
    ) -> None:
        """Завершить span."""
        self._span.end(
            output=output,
            metadata=metadata,
            level=level,
            status_message=status_message,
        )

    @property
    def latency_ms(self) -> float:
        """Прошедшее время в мс с момента создания span."""
        return (time.monotonic() - self._start_time) * 1000


# ── No-op реализации (когда Langfuse отключён) ──────────────────────────


class _NoOpTrace:
    id = "noop"

    def generation(self, **_kwargs: Any) -> _NoOpGeneration:
        return _NoOpGeneration()

    def span(self, **_kwargs: Any) -> _NoOpSpan:
        return _NoOpSpan()

    def update(self, **_kwargs: Any) -> None:
        pass


class _NoOpGeneration:
    latency_ms = 0.0

    def end(self, **_kwargs: Any) -> None:
        pass


class _NoOpSpan:
    latency_ms = 0.0

    def end(self, **_kwargs: Any) -> None:
        pass


# ── Основной сервис ──────────────────────────────────────────────────────


class LangfuseService:
    """Сервис Langfuse-трейсинга.

    Если ``enabled=False`` (по умолчанию), все методы возвращают no-op объекты,
    не влияя на производительность.

    Совместим с Langfuse Python SDK v2 (Langfuse.trace() API).
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        public_key: str = "",
        secret_key: str = "",
        host: str = "https://cloud.langfuse.com",
        anonymize_messages: bool = False,
    ) -> None:
        self._enabled = enabled
        self._client: Any = None
        self._anonymize_messages = anonymize_messages

        if enabled and public_key and secret_key:
            try:
                from langfuse import Langfuse

                self._client = Langfuse(
                    public_key=public_key,
                    secret_key=secret_key,
                    host=host,
                )
                logger.info(
                    "Langfuse трейсинг включён (host=%s)",
                    host,
                )
            except Exception as exc:
                logger.warning(
                    "Не удалось инициализировать Langfuse: %s. Трейсинг отключён.",
                    exc,
                )
                self._enabled = False
        elif enabled:
            logger.warning(
                "LANGFUSE_ENABLED=true, но ключи не заданы. Трейсинг отключён.",
            )
            self._enabled = False

    @property
    def enabled(self) -> bool:
        """Активен ли трейсинг."""
        return self._enabled and self._client is not None

    def trace(
        self,
        *,
        name: str = "chat",
        user_id: str | None = None,
        input: Any = None,
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> LangfuseTrace | _NoOpTrace:
        """Создать trace для обработки сообщения пользователя.

        Анонимизация применяется автоматически:
        - user_id и session_id хешируются (SHA-256, 12 символов)
        - PII в input маскируется (телефоны, email, карты)
        - Если anonymize_messages=True, весь input заменяется на "[REDACTED]"
        """
        if not self.enabled:
            return _NoOpTrace()

        # Анонимизация user_id и session_id
        anon_user_id = _anonymize_user_id(user_id) if user_id else None
        anon_session_id = _anonymize_user_id(session_id) if session_id else None

        # Маскировка input
        safe_input = input
        if isinstance(input, str):
            safe_input = "[REDACTED]" if self._anonymize_messages else _mask_pii(input)

        trace = self._client.trace(
            name=name,
            user_id=anon_user_id,
            input=safe_input,
            metadata=metadata,
            session_id=anon_session_id,
            tags=tags,
        )
        return LangfuseTrace(trace)

    def flush(self) -> None:
        """Отправить накопленные события в Langfuse."""
        if self._client is not None:
            try:
                self._client.flush()
            except Exception as exc:
                logger.debug("Ошибка при flush Langfuse: %s", exc)

    def shutdown(self) -> None:
        """Корректное завершение — flush + shutdown SDK."""
        if self._client is not None:
            try:
                self._client.flush()
                self._client.shutdown()
                logger.info("Langfuse клиент закрыт")
            except Exception as exc:
                logger.debug("Ошибка при shutdown Langfuse: %s", exc)


def _messages_to_langfuse(messages: list) -> list[dict[str, Any]]:
    """Конвертировать Messages GigaChat в формат для Langfuse input/output."""
    result = []
    for msg in messages:
        entry: dict[str, Any] = {"role": str(msg.role)}
        if msg.content:
            entry["content"] = msg.content
        if hasattr(msg, "function_call") and msg.function_call:
            raw_args = msg.function_call.arguments
            if isinstance(raw_args, str):
                try:
                    parsed_args = json.loads(raw_args)
                except (json.JSONDecodeError, ValueError):
                    parsed_args = raw_args
            else:
                parsed_args = raw_args
            entry["function_call"] = {
                "name": msg.function_call.name,
                "arguments": parsed_args,
            }
        if hasattr(msg, "name") and msg.name:
            entry["name"] = msg.name
        result.append(entry)
    return result
