"""Кеш рецептов (SQLite).

Хранит список ингредиентов для блюд, чтобы не запрашивать
у GigaChat повторно. Рецепты глобальные (не привязаны к пользователю).
"""

import json
import logging
import os

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS recipes (
    dish_name   TEXT    NOT NULL PRIMARY KEY,
    servings    INTEGER NOT NULL,
    ingredients TEXT    NOT NULL,
    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP
)
"""


class RecipeStore:
    """Async-кеш рецептов на базе SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def _ensure_db(self) -> aiosqlite.Connection:
        """Открыть или переиспользовать соединение с БД."""
        if self._db is None:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute(_CREATE_TABLE_SQL)
            await self._db.commit()
            logger.info("SQLite кеш рецептов открыт: %s", self._db_path)
        return self._db

    @staticmethod
    def normalize_dish_name(dish_name: str) -> str:
        """Нормализовать название блюда для поиска в кеше."""
        return dish_name.strip().lower()

    @staticmethod
    def scale_ingredients(
        ingredients: list[dict],
        from_servings: int,
        to_servings: int,
    ) -> list[dict]:
        """Масштабировать количества ингредиентов на другое число порций.

        Пропорционально изменяет quantity для каждого ингредиента.
        Не числовые количества (например, "по вкусу") остаются как есть.
        """
        if from_servings == to_servings or from_servings <= 0:
            return ingredients

        ratio = to_servings / from_servings
        scaled = []
        for ing in ingredients:
            scaled_ing = {**ing}
            q = ing.get("quantity")
            if isinstance(q, (int, float)) and q > 0:
                scaled_ing["quantity"] = round(q * ratio, 2)
            scaled.append(scaled_ing)
        return scaled

    async def get(self, dish_name: str) -> dict | None:
        """Найти рецепт в кеше.

        Returns:
            Словарь {dish_name, servings, ingredients} или None.
        """
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT dish_name, servings, ingredients FROM recipes "
            "WHERE dish_name = ?",
            (self.normalize_dish_name(dish_name),),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        try:
            ingredients = json.loads(row["ingredients"])
        except (json.JSONDecodeError, TypeError):
            return None
        return {
            "dish_name": row["dish_name"],
            "servings": row["servings"],
            "ingredients": ingredients,
        }

    async def save(
        self,
        dish_name: str,
        servings: int,
        ingredients: list[dict],
    ) -> None:
        """Сохранить рецепт в кеш."""
        db = await self._ensure_db()
        await db.execute(
            "INSERT OR REPLACE INTO recipes (dish_name, servings, ingredients) "
            "VALUES (?, ?, ?)",
            (
                self.normalize_dish_name(dish_name),
                servings,
                json.dumps(ingredients, ensure_ascii=False),
            ),
        )
        await db.commit()
        logger.info(
            "Рецепт закеширован: %s на %d порций (%d ингредиентов)",
            dish_name,
            servings,
            len(ingredients),
        )

    async def close(self) -> None:
        """Закрыть соединение с БД."""
        if self._db is not None:
            await self._db.close()
            self._db = None
            logger.info("SQLite кеш рецептов закрыт.")
