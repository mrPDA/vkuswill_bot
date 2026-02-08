"""Хранилище предпочтений пользователей (SQLite).

Каждый пользователь может иметь по одному предпочтению на категорию.
Например: мороженое → пломбир в шоколаде на палочке.
"""

import json
import logging
import os

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS preferences (
    user_id    INTEGER NOT NULL,
    category   TEXT    NOT NULL,
    preference TEXT    NOT NULL,
    created_at TEXT    DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, category)
)
"""

# Лимиты длины строк для защиты от раздувания БД
MAX_CATEGORY_LENGTH = 100
MAX_PREFERENCE_LENGTH = 500
MAX_PREFERENCES_PER_USER = 50


class PreferencesStore:
    """Async-хранилище предпочтений на базе SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _ensure_db(self) -> aiosqlite.Connection:
        """Открыть или переиспользовать соединение с БД."""
        if self._db is None:
            # Создаём директорию если не существует
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute(_CREATE_TABLE_SQL)
            await self._db.commit()
            logger.info("SQLite база предпочтений открыта: %s", self._db_path)
        return self._db

    async def get_all(self, user_id: int) -> list[dict]:
        """Получить все предпочтения пользователя.

        Returns:
            Список словарей {category, preference}.
        """
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT category, preference FROM preferences "
            "WHERE user_id = ? ORDER BY category",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [{"category": row["category"], "preference": row["preference"]} for row in rows]

    async def get_formatted(self, user_id: int) -> str:
        """Получить предпочтения в формате JSON для GigaChat.

        GigaChat API требует, чтобы результат функции был валидным JSON.
        """
        prefs = await self.get_all(user_id)
        if not prefs:
            return json.dumps(
                {"ok": True, "preferences": [], "message": "Нет сохранённых предпочтений."},
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": True, "preferences": prefs},
            ensure_ascii=False,
        )

    async def set(self, user_id: int, category: str, preference: str) -> str:
        """Сохранить предпочтение (upsert по user_id + category).

        Валидирует длину строк и лимит количества предпочтений
        для защиты от раздувания БД.

        Returns:
            Подтверждение в формате JSON-строки для GigaChat.
        """
        category = category.strip().lower()[:MAX_CATEGORY_LENGTH]
        preference = preference.strip()[:MAX_PREFERENCE_LENGTH]

        if not category or not preference:
            return json.dumps(
                {"ok": False, "message": "Категория и предпочтение не могут быть пустыми."},
                ensure_ascii=False,
            )

        db = await self._ensure_db()

        # Проверяем лимит количества предпочтений на пользователя
        # (только если это новая категория, а не обновление существующей)
        cursor = await db.execute(
            "SELECT COUNT(*) as cnt FROM preferences WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        count = row["cnt"] if row else 0

        # Проверяем, существует ли уже эта категория
        cursor = await db.execute(
            "SELECT 1 FROM preferences WHERE user_id = ? AND category = ?",
            (user_id, category),
        )
        existing = await cursor.fetchone()

        if not existing and count >= MAX_PREFERENCES_PER_USER:
            logger.warning(
                "Лимит предпочтений: user=%d, count=%d, max=%d",
                user_id, count, MAX_PREFERENCES_PER_USER,
            )
            return json.dumps(
                {
                    "ok": False,
                    "message": f"Достигнут лимит предпочтений ({MAX_PREFERENCES_PER_USER}). "
                    "Удалите ненужные, чтобы добавить новые.",
                },
                ensure_ascii=False,
            )

        await db.execute(
            "INSERT OR REPLACE INTO preferences (user_id, category, preference) "
            "VALUES (?, ?, ?)",
            (user_id, category, preference),
        )
        await db.commit()
        logger.info(
            "Предпочтение сохранено: user=%d, %s → %s",
            user_id,
            category,
            preference,
        )
        return json.dumps(
            {
                "ok": True,
                "message": f"Запомнил: {category} → {preference}",
            },
            ensure_ascii=False,
        )

    async def delete(self, user_id: int, category: str) -> str:
        """Удалить предпочтение.

        Returns:
            Подтверждение в формате JSON-строки для GigaChat.
        """
        db = await self._ensure_db()
        cursor = await db.execute(
            "DELETE FROM preferences WHERE user_id = ? AND category = ?",
            (user_id, category.strip().lower()),
        )
        await db.commit()
        if cursor.rowcount > 0:
            logger.info(
                "Предпочтение удалено: user=%d, %s", user_id, category,
            )
            return json.dumps(
                {"ok": True, "message": f"Предпочтение «{category}» удалено."},
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": True, "message": f"Предпочтение «{category}» не найдено."},
            ensure_ascii=False,
        )

    async def close(self) -> None:
        """Закрыть соединение с БД."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("SQLite база предпочтений закрыта.")
