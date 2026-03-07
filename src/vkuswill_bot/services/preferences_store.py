"""Хранилище предпочтений пользователей (SQLite).

Каждый пользователь может иметь по одному предпочтению на категорию.
Например: мороженое → пломбир в шоколаде на палочке.
"""

import json
import logging
import os
import stat
from typing import Any

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

_CREATE_PROFILE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS preference_profiles (
    user_id      INTEGER PRIMARY KEY,
    profile_json TEXT    NOT NULL,
    updated_at   TEXT    DEFAULT CURRENT_TIMESTAMP
)
"""

# Лимиты длины строк для защиты от раздувания БД
MAX_CATEGORY_LENGTH = 100
MAX_PREFERENCE_LENGTH = 500
MAX_PREFERENCES_PER_USER = 50
MAX_PROFILE_LIST_ITEMS = 50
_DIET_MARKERS: dict[str, tuple[str, ...]] = {
    "vegan": ("vegan", "веган", "plant based", "plant-based", "plant_based"),
    "vegetarian": ("vegetarian", "вегетариан", "без мяса"),
    "halal": ("halal", "халяль", "халал"),
    "default": ("default", "обыч", "стандарт", "omnivore", "омнивор"),
}


class PreferencesStore:
    """Async-хранилище предпочтений на базе SQLite."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._readonly = False

    # ------------------------------------------------------------------
    # Инициализация и проверка доступа
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_permissions(path: str) -> bool:
        """Попытаться сделать файл доступным на запись (u+w).

        Returns:
            True если удалось исправить или файл уже writable.
        """
        try:
            st = os.stat(path)
            if not (st.st_mode & stat.S_IWUSR):
                os.chmod(path, st.st_mode | stat.S_IWUSR)
                logger.info("Исправлены права на запись: %s", path)
            return True
        except OSError as e:
            logger.warning("Не удалось исправить права %s: %s", path, e)
            return False

    def _ensure_writable_paths(self) -> None:
        """Проверить и исправить права на .db, .db-wal, .db-shm."""
        for suffix in ("", "-wal", "-shm"):
            p = self._db_path + suffix
            if os.path.exists(p):
                self._fix_permissions(p)

    async def _ensure_db(self) -> aiosqlite.Connection:
        """Открыть или переиспользовать соединение с БД."""
        if self._db is None:
            # Создаём директорию если не существует
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

            # Попытка исправить права на существующие файлы БД
            self._ensure_writable_paths()

            self._db = await aiosqlite.connect(self._db_path)
            self._db.row_factory = aiosqlite.Row
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute(_CREATE_TABLE_SQL)
            await self._db.execute(_CREATE_PROFILE_TABLE_SQL)
            await self._db.commit()
            logger.info("SQLite база предпочтений открыта: %s", self._db_path)

            # Проверка записи: пробный INSERT + DELETE
            await self._check_write_access()
        return self._db

    async def _check_write_access(self) -> None:
        """Проверить, что БД доступна на запись (пробный INSERT + DELETE)."""
        try:
            if self._db is None:
                self._readonly = True
                logger.error("SQLite: _check_write_access вызван до инициализации _db")
                return
            await self._db.execute(
                "INSERT OR REPLACE INTO preferences "
                "(user_id, category, preference) VALUES (0, '__write_test__', 'ok')",
            )
            await self._db.execute(
                "DELETE FROM preferences WHERE user_id = 0 AND category = '__write_test__'",
            )
            await self._db.commit()
            self._readonly = False
            logger.info("SQLite: проверка записи — ОК")
        except Exception as e:
            self._readonly = True
            logger.error(
                "SQLite READONLY! БД %s недоступна на запись: %s. "
                "Предпочтения НЕ будут сохраняться до перезапуска.",
                self._db_path,
                e,
            )

    # ------------------------------------------------------------------
    # Structured profile helpers (meal-plan ready)
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_profile() -> dict[str, Any]:
        return {
            "schema_version": 1,
            "hard_constraints": {},
            "soft_preferences": {
                "cuisines": [],
                "liked_ingredients": [],
                "disliked_ingredients": [],
                "freeform_preferences": {},
            },
            "operational_preferences": {},
        }

    @staticmethod
    def _to_bool(value: str) -> bool | None:
        low = value.strip().lower()
        if low in {"true", "1", "yes", "y", "да", "д"}:
            return True
        if low in {"false", "0", "no", "n", "нет", "н"}:
            return False
        return None

    @staticmethod
    def _to_int(value: str) -> int | None:
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return int(text)
        return None

    @staticmethod
    def _canonicalize_diet(value: object) -> str:
        low = " ".join(
            str(value).strip().lower().replace("_", " ").replace("-", " ").split()
        )
        if not low:
            return ""
        for canonical, markers in _DIET_MARKERS.items():
            if any(marker in low for marker in markers):
                return canonical
        return low

    @staticmethod
    def _split_values(value: str) -> list[str]:
        # Поддерживаем CSV/semicolon/newline в значении preference.
        separators = [",", ";", "\n", "|"]
        chunks = [value]
        for sep in separators:
            next_chunks: list[str] = []
            for chunk in chunks:
                next_chunks.extend(chunk.split(sep))
            chunks = next_chunks

        result: list[str] = []
        seen: set[str] = set()
        for raw in chunks:
            item = raw.strip()[:MAX_PREFERENCE_LENGTH]
            if not item:
                continue
            marker = item.lower()
            if marker in seen:
                continue
            seen.add(marker)
            result.append(item)
            if len(result) >= MAX_PROFILE_LIST_ITEMS:
                break
        return result

    @staticmethod
    def _norm_category(category: str) -> tuple[str, str]:
        normalized = " ".join(category.strip().lower().split())[:MAX_CATEGORY_LENGTH]
        return normalized, normalized.replace(" ", "_")

    @staticmethod
    def _list_to_csv(values: Any) -> str:
        if not isinstance(values, list):
            return ""
        return ",".join(str(item) for item in values)

    @classmethod
    def _ensure_profile_shape(cls, profile: dict[str, Any] | None) -> dict[str, Any]:
        base = cls._empty_profile()
        if not isinstance(profile, dict):
            return base

        hard = profile.get("hard_constraints")
        soft = profile.get("soft_preferences")
        operational = profile.get("operational_preferences")

        if isinstance(hard, dict):
            normalized_hard = dict(hard)
            if "diet" in normalized_hard:
                canonical = cls._canonicalize_diet(normalized_hard.get("diet"))
                if canonical:
                    normalized_hard["diet"] = canonical
                else:
                    normalized_hard.pop("diet", None)
            base["hard_constraints"] = normalized_hard
        if isinstance(soft, dict):
            merged_soft = dict(base["soft_preferences"])
            merged_soft.update(soft)
            merged_soft["cuisines"] = cls._split_values(
                cls._list_to_csv(merged_soft.get("cuisines"))
            )
            merged_soft["liked_ingredients"] = cls._split_values(
                cls._list_to_csv(merged_soft.get("liked_ingredients"))
            )
            merged_soft["disliked_ingredients"] = cls._split_values(
                cls._list_to_csv(merged_soft.get("disliked_ingredients"))
            )
            freeform = merged_soft.get("freeform_preferences")
            if not isinstance(freeform, dict):
                merged_soft["freeform_preferences"] = {}
            else:
                normalized_freeform: dict[str, str] = {}
                for k, v in freeform.items():
                    key, _ = cls._norm_category(str(k))
                    val = str(v).strip()[:MAX_PREFERENCE_LENGTH]
                    if key and val:
                        normalized_freeform[key] = val
                merged_soft["freeform_preferences"] = normalized_freeform
            base["soft_preferences"] = merged_soft
        if isinstance(operational, dict):
            base["operational_preferences"] = dict(operational)
        return base

    async def _build_profile_from_legacy_preferences(
        self,
        db: aiosqlite.Connection,
        user_id: int,
    ) -> dict[str, Any]:
        cursor = await db.execute(
            "SELECT category, preference FROM preferences WHERE user_id = ? ORDER BY category",
            (user_id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return self._empty_profile()

        profile = self._empty_profile()
        for row in rows:
            category = str(row["category"]).strip().lower()[:MAX_CATEGORY_LENGTH]
            preference = str(row["preference"]).strip()[:MAX_PREFERENCE_LENGTH]
            if not category or not preference:
                continue
            profile = self._apply_preference_to_profile(
                profile,
                category=category,
                preference=preference,
            )
        return profile

    async def _get_profile_from_db(
        self,
        db: aiosqlite.Connection,
        user_id: int,
    ) -> dict[str, Any]:
        cursor = await db.execute(
            "SELECT profile_json FROM preference_profiles WHERE user_id = ?",
            (user_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return await self._build_profile_from_legacy_preferences(db, user_id)
        raw = row["profile_json"]
        if not isinstance(raw, str) or not raw:
            return await self._build_profile_from_legacy_preferences(db, user_id)
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return await self._build_profile_from_legacy_preferences(db, user_id)
        if not isinstance(payload, dict):
            return await self._build_profile_from_legacy_preferences(db, user_id)
        return self._ensure_profile_shape(payload)

    async def _save_profile_to_db(
        self,
        db: aiosqlite.Connection,
        user_id: int,
        profile: dict[str, Any],
    ) -> None:
        normalized = self._ensure_profile_shape(profile)
        payload = json.dumps(normalized, ensure_ascii=False)
        await db.execute(
            "INSERT OR REPLACE INTO preference_profiles (user_id, profile_json) VALUES (?, ?)",
            (user_id, payload),
        )

    def _apply_preference_to_profile(
        self,
        profile: dict[str, Any],
        *,
        category: str,
        preference: str,
    ) -> dict[str, Any]:
        normalized = self._ensure_profile_shape(profile)
        hard = normalized["hard_constraints"]
        soft = normalized["soft_preferences"]
        operational = normalized["operational_preferences"]
        freeform = soft["freeform_preferences"]

        cat, cat_alias = self._norm_category(category)
        values = self._split_values(preference)
        bool_value = self._to_bool(preference)
        int_value = self._to_int(preference)

        if cat_alias in {"diet", "диета"}:
            canonical_diet = self._canonicalize_diet(preference)
            if canonical_diet:
                hard["diet"] = canonical_diet
        elif cat_alias in {
            "allergies",
            "allergy",
            "аллергии",
            "аллергены",
            "allergens",
            "allergens_excluded",
        }:
            hard["allergens_excluded"] = values
        elif cat_alias in {"no_pork", "без_свинины"}:
            hard["no_pork"] = bool_value if bool_value is not None else preference
        elif cat_alias in {"fasting_mode", "пост"}:
            hard["fasting_mode"] = preference
        elif cat_alias in {"cuisine", "cuisines", "кухня", "кухни"}:
            soft["cuisines"] = values
        elif cat_alias in {"liked_ingredients", "likes", "favorite", "люблю", "любимые"}:
            soft["liked_ingredients"] = values
        elif cat_alias in {
            "disliked_ingredients",
            "dislikes",
            "avoid",
            "не_люблю",
            "нелюбимые",
            "исключить",
        }:
            soft["disliked_ingredients"] = values
        elif cat_alias in {"spice_level", "острота"}:
            soft["spice_level"] = preference
        elif cat_alias in {"high_protein", "высокий_белок"}:
            soft["high_protein"] = bool_value if bool_value is not None else preference
        elif cat_alias in {"low_carb", "меньше_углеводов"}:
            soft["low_carb"] = bool_value if bool_value is not None else preference
        elif cat_alias in {"meal_types", "приемы_пищи", "приемы_еды"}:
            operational["meal_types"] = values
        elif cat_alias in {"max_cook_time_min", "макс_время_готовки"}:
            operational["max_cook_time_min"] = int_value if int_value is not None else preference
        elif cat_alias in {"max_dishes", "макс_блюд"}:
            operational["max_dishes"] = int_value if int_value is not None else preference
        else:
            freeform[cat] = preference

        return normalized

    def _remove_preference_from_profile(
        self,
        profile: dict[str, Any],
        *,
        category: str,
    ) -> dict[str, Any]:
        normalized = self._ensure_profile_shape(profile)
        hard = normalized["hard_constraints"]
        soft = normalized["soft_preferences"]
        operational = normalized["operational_preferences"]
        freeform = soft["freeform_preferences"]

        _, cat_alias = self._norm_category(category)

        if cat_alias in {"diet", "диета"}:
            hard.pop("diet", None)
        elif cat_alias in {
            "allergies",
            "allergy",
            "аллергии",
            "аллергены",
            "allergens",
            "allergens_excluded",
        }:
            hard.pop("allergens_excluded", None)
        elif cat_alias in {"no_pork", "без_свинины"}:
            hard.pop("no_pork", None)
        elif cat_alias in {"fasting_mode", "пост"}:
            hard.pop("fasting_mode", None)
        elif cat_alias in {"cuisine", "cuisines", "кухня", "кухни"}:
            soft["cuisines"] = []
        elif cat_alias in {"liked_ingredients", "likes", "favorite", "люблю", "любимые"}:
            soft["liked_ingredients"] = []
        elif cat_alias in {
            "disliked_ingredients",
            "dislikes",
            "avoid",
            "не_люблю",
            "нелюбимые",
            "исключить",
        }:
            soft["disliked_ingredients"] = []
        elif cat_alias in {"spice_level", "острота"}:
            soft.pop("spice_level", None)
        elif cat_alias in {"high_protein", "высокий_белок"}:
            soft.pop("high_protein", None)
        elif cat_alias in {"low_carb", "меньше_углеводов"}:
            soft.pop("low_carb", None)
        elif cat_alias in {"meal_types", "приемы_пищи", "приемы_еды"}:
            operational.pop("meal_types", None)
        elif cat_alias in {"max_cook_time_min", "макс_время_готовки"}:
            operational.pop("max_cook_time_min", None)
        elif cat_alias in {"max_dishes", "макс_блюд"}:
            operational.pop("max_dishes", None)
        else:
            cat, _ = self._norm_category(category)
            freeform.pop(cat, None)

        return normalized

    async def get_all(self, user_id: int) -> list[dict]:
        """Получить все предпочтения пользователя.

        Returns:
            Список словарей {category, preference}.
        """
        db = await self._ensure_db()
        cursor = await db.execute(
            "SELECT category, preference FROM preferences WHERE user_id = ? ORDER BY category",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [{"category": row["category"], "preference": row["preference"]} for row in rows]

    async def get_formatted(self, user_id: int) -> str:
        """Получить предпочтения в формате JSON для LLM.

        LLM API требует, чтобы результат функции был валидным JSON.
        """
        prefs = await self.get_all(user_id)
        profile = await self.get_profile(user_id)
        if not prefs:
            return json.dumps(
                {
                    "ok": True,
                    "preferences": [],
                    "profile": profile,
                    "message": "Нет сохранённых предпочтений.",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": True, "preferences": prefs, "profile": profile},
            ensure_ascii=False,
        )

    async def get_profile(self, user_id: int) -> dict[str, Any]:
        """Получить структурированный профиль предпочтений для meal-plan."""
        db = await self._ensure_db()
        return await self._get_profile_from_db(db, user_id)

    async def set(self, user_id: int, category: str, preference: str) -> str:
        """Сохранить предпочтение (upsert по user_id + category).

        Валидирует длину строк и лимит количества предпочтений
        для защиты от раздувания БД.

        Returns:
            Подтверждение в формате JSON-строки для LLM.
        """
        category = category.strip().lower()[:MAX_CATEGORY_LENGTH]
        preference = preference.strip()[:MAX_PREFERENCE_LENGTH]

        if not category or not preference:
            return json.dumps(
                {"ok": False, "message": "Категория и предпочтение не могут быть пустыми."},
                ensure_ascii=False,
            )

        db = await self._ensure_db()

        # Быстрый отказ если БД readonly (не повторяем бесполезные попытки)
        if self._readonly:
            logger.warning(
                "Отклонена запись в readonly БД: user=%d, %s → %s",
                user_id,
                category,
                preference,
            )
            return json.dumps(
                {
                    "ok": False,
                    "message": "Не удалось сохранить предпочтение: база данных "
                    "временно недоступна на запись. Предпочтение НЕ сохранено. "
                    "Сообщи пользователю об этой проблеме.",
                },
                ensure_ascii=False,
            )

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
                user_id,
                count,
                MAX_PREFERENCES_PER_USER,
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
            "INSERT OR REPLACE INTO preferences (user_id, category, preference) VALUES (?, ?, ?)",
            (user_id, category, preference),
        )
        profile = await self._get_profile_from_db(db, user_id)
        profile = self._apply_preference_to_profile(
            profile,
            category=category,
            preference=preference,
        )
        await self._save_profile_to_db(db, user_id, profile)
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
            Подтверждение в формате JSON-строки для LLM.
        """
        db = await self._ensure_db()

        if self._readonly:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Не удалось удалить предпочтение: база данных "
                    "временно недоступна на запись.",
                },
                ensure_ascii=False,
            )

        normalized_category = category.strip().lower()
        cursor = await db.execute(
            "DELETE FROM preferences WHERE user_id = ? AND category = ?",
            (user_id, normalized_category),
        )
        profile = await self._get_profile_from_db(db, user_id)
        profile = self._remove_preference_from_profile(
            profile,
            category=normalized_category,
        )
        await self._save_profile_to_db(db, user_id, profile)
        await db.commit()
        if cursor.rowcount > 0:
            logger.info(
                "Предпочтение удалено: user=%d, %s",
                user_id,
                category,
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
