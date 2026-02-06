"""Общие хелперы для тестов.

Публичные функции для создания тестовых объектов GigaChat и aiogram.
Импортируй из этого модуля, а не дублируй в каждом тесте.
"""

import json
from unittest.mock import AsyncMock, MagicMock

from gigachat.models import (
    ChatCompletion,
    Choices,
    FunctionCall,
    Messages,
    MessagesRole,
    Usage,
)

# Общий объект Usage для тестовых ответов GigaChat
USAGE = Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)


def make_text_response(text: str) -> ChatCompletion:
    """Создать ответ GigaChat с текстом (без function_call)."""
    return ChatCompletion(
        choices=[
            Choices(
                message=Messages(
                    role=MessagesRole.ASSISTANT,
                    content=text,
                ),
                index=0,
                finish_reason="stop",
            )
        ],
        created=1000000,
        model="GigaChat",
        usage=USAGE,
        object="chat.completion",
    )


def make_function_call_response(
    name: str, arguments: dict | str
) -> ChatCompletion:
    """Создать ответ GigaChat с вызовом функции."""
    if isinstance(arguments, str):
        args = json.loads(arguments)
    else:
        args = arguments
    return ChatCompletion(
        choices=[
            Choices(
                message=Messages(
                    role=MessagesRole.ASSISTANT,
                    content="",
                    function_call=FunctionCall(name=name, arguments=args),
                ),
                index=0,
                finish_reason="function_call",
            )
        ],
        created=1000000,
        model="GigaChat",
        usage=USAGE,
        object="chat.completion",
    )


def make_message(text: str = "", user_id: int = 1) -> MagicMock:
    """Создать мок aiogram.types.Message."""
    msg = MagicMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.chat = MagicMock()
    msg.chat.id = 100
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.send_chat_action = AsyncMock()
    return msg
