"""Исключения агентов."""

from __future__ import annotations


class LLMOverloadedError(Exception):
    """Все слоты LLM-семафора заняты, очередь переполнена."""
