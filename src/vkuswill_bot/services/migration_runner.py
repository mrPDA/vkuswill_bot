"""Версионированный раннер SQL-миграций (PostgreSQL).

Сканирует папку ``migrations/`` на наличие ``*.sql`` файлов,
применяет только новые (ещё не записанные в ``schema_migrations``)
в порядке сортировки имён файлов.

Использование:

    runner = MigrationRunner(pool)
    applied = await runner.run()          # -> ["001", "003", "004", "005"]

Конвенции именования файлов:
    NNN_описание.sql     (например, 001_create_users.sql)
    Файлы без расширения .sql игнорируются (002_create_langfuse_db.sh).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# Папка с миграциями (корень проекта / migrations)
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

# Таблица для отслеживания применённых миграций
_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT        PRIMARY KEY,
    filename    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class MigrationRunner:
    """Применяет SQL-миграции с отслеживанием версий.

    Каждая миграция выполняется внутри транзакции.
    Повторный вызов ``run()`` безопасен — уже применённые пропускаются.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        migrations_dir: Path | None = None,
    ) -> None:
        self._pool = pool
        self._dir = migrations_dir or MIGRATIONS_DIR

    @staticmethod
    def _extract_version(filename: str) -> str:
        """Извлечь версию из имени файла: '001_create_users.sql' → '001'."""
        return filename.split("_", 1)[0]

    def _discover(self) -> list[tuple[str, Path]]:
        """Найти все .sql файлы и вернуть [(version, path)] в порядке версий."""
        if not self._dir.is_dir():
            logger.warning("Папка миграций не найдена: %s", self._dir)
            return []
        files = sorted(self._dir.glob("*.sql"))
        result: list[tuple[str, Path]] = []
        for f in files:
            version = self._extract_version(f.name)
            result.append((version, f))
        return result

    async def _get_applied(self, conn: asyncpg.Connection) -> set[str]:
        """Получить множество уже применённых версий."""
        rows = await conn.fetch("SELECT version FROM schema_migrations")
        return {row["version"] for row in rows}

    async def run(self) -> list[str]:
        """Применить все новые миграции.

        Returns:
            Список версий, которые были применены в этом вызове.
        """
        async with self._pool.acquire() as conn:
            # Bootstrap: создать schema_migrations если не существует
            await conn.execute(_BOOTSTRAP_SQL)

            applied = await self._get_applied(conn)
            migrations = self._discover()

            newly_applied: list[str] = []
            for version, path in migrations:
                if version in applied:
                    continue

                sql = path.read_text(encoding="utf-8")
                # Каждая миграция — в отдельной транзакции
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, filename) "
                        "VALUES ($1, $2)",
                        version,
                        path.name,
                    )
                newly_applied.append(version)
                logger.info(
                    "Миграция %s применена: %s",
                    version,
                    path.name,
                )

            if newly_applied:
                logger.info(
                    "Применено миграций: %d (%s)",
                    len(newly_applied),
                    ", ".join(newly_applied),
                )
            else:
                logger.info("Все миграции актуальны, новых нет")

            return newly_applied
