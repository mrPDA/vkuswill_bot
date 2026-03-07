"""Runtime loader for user preferences in shopping/meal-plan flows."""

from __future__ import annotations

from logging import Logger
from typing import Any

from vkuswill_bot.services.preferences_parser import parse_preference_profile, parse_preferences


async def load_user_preferences_bundle(
    *,
    preferences_store: Any,
    user_id: int,
    logger: Logger,
) -> tuple[dict[str, str], dict[str, Any]]:
    if preferences_store is None:
        return {}, {}
    try:
        raw = await preferences_store.get_formatted(user_id)
    except Exception as exc:
        logger.warning("Failed to load user preferences for %s: %s", user_id, exc)
        return (
            {},
            {
                "operational_preferences": {
                    "stored_preferences_notice": (
                        "stored preferences недоступны, "
                        "использованы только явные ограничения запроса"
                    )
                }
            },
        )
    return parse_preferences(raw), parse_preference_profile(raw)
