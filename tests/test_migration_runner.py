"""Тесты для MigrationRunner (версионирование SQL-миграций)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from vkuswill_bot.services.migration_runner import MigrationRunner


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------


def _make_pool() -> tuple[MagicMock, AsyncMock]:
    """Создать мок asyncpg.Pool с контекстным менеджером acquire."""
    pool = MagicMock()
    conn = AsyncMock()
    # pool.acquire() → async context manager → conn
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    # conn.transaction() в asyncpg — синхронный вызов,
    # возвращает объект с async context manager.
    # Подменяем conn.transaction на обычный MagicMock.
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock()
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)
    return pool, conn


def _make_migration_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    """Создать временную папку с SQL-файлами миграций."""
    d = tmp_path / "migrations"
    d.mkdir()
    for name, content in files.items():
        (d / name).write_text(content, encoding="utf-8")
    return d


@pytest.fixture
def pool_and_conn():
    return _make_pool()


# ---------------------------------------------------------------------------
# Тесты
# ---------------------------------------------------------------------------


class TestMigrationRunner:
    """Тесты MigrationRunner."""

    @pytest.mark.asyncio
    async def test_creates_schema_migrations_table(self, pool_and_conn, tmp_path):
        """run() создаёт таблицу schema_migrations при первом вызове."""
        pool, conn = pool_and_conn
        conn.fetch.return_value = []  # нет применённых миграций
        migrations_dir = _make_migration_dir(tmp_path, {})

        runner = MigrationRunner(pool, migrations_dir=migrations_dir)
        await runner.run()

        # Первый execute — bootstrap SQL
        bootstrap_call = conn.execute.call_args_list[0][0][0]
        assert "schema_migrations" in bootstrap_call

    @pytest.mark.asyncio
    async def test_applies_new_migrations(self, pool_and_conn, tmp_path):
        """run() применяет миграции, которых нет в schema_migrations."""
        pool, conn = pool_and_conn
        conn.fetch.return_value = []  # пустая schema_migrations

        migrations_dir = _make_migration_dir(
            tmp_path,
            {
                "001_users.sql": "CREATE TABLE users (id INT);",
                "002_events.sql": "CREATE TABLE events (id INT);",
            },
        )

        runner = MigrationRunner(pool, migrations_dir=migrations_dir)
        applied = await runner.run()

        assert applied == ["001", "002"]
        # bootstrap + 2 * (execute SQL + insert version) = 5 execute calls
        assert conn.execute.call_count == 5

    @pytest.mark.asyncio
    async def test_skips_already_applied(self, pool_and_conn, tmp_path):
        """run() пропускает уже применённые миграции."""
        pool, conn = pool_and_conn
        # 001 уже применена
        conn.fetch.return_value = [{"version": "001"}]

        migrations_dir = _make_migration_dir(
            tmp_path,
            {
                "001_users.sql": "CREATE TABLE users (id INT);",
                "002_events.sql": "CREATE TABLE events (id INT);",
            },
        )

        runner = MigrationRunner(pool, migrations_dir=migrations_dir)
        applied = await runner.run()

        assert applied == ["002"]
        # bootstrap + 1 * (execute SQL + insert version) = 3 execute calls
        assert conn.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_all_applied_no_op(self, pool_and_conn, tmp_path):
        """run() не выполняет миграции, если все уже применены."""
        pool, conn = pool_and_conn
        conn.fetch.return_value = [
            {"version": "001"},
            {"version": "002"},
        ]

        migrations_dir = _make_migration_dir(
            tmp_path,
            {
                "001_users.sql": "CREATE TABLE users (id INT);",
                "002_events.sql": "CREATE TABLE events (id INT);",
            },
        )

        runner = MigrationRunner(pool, migrations_dir=migrations_dir)
        applied = await runner.run()

        assert applied == []
        # Только bootstrap execute
        assert conn.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_ignores_non_sql_files(self, pool_and_conn, tmp_path):
        """run() игнорирует файлы без расширения .sql."""
        pool, conn = pool_and_conn
        conn.fetch.return_value = []

        migrations_dir = _make_migration_dir(
            tmp_path,
            {
                "001_users.sql": "CREATE TABLE users (id INT);",
            },
        )
        # Добавляем .sh файл (не должен подхватиться)
        (migrations_dir / "002_langfuse.sh").write_text("#!/bin/bash\necho ok")

        runner = MigrationRunner(pool, migrations_dir=migrations_dir)
        applied = await runner.run()

        assert applied == ["001"]

    @pytest.mark.asyncio
    async def test_applies_in_sorted_order(self, pool_and_conn, tmp_path):
        """run() применяет миграции в порядке сортировки имён файлов."""
        pool, conn = pool_and_conn
        conn.fetch.return_value = []

        migrations_dir = _make_migration_dir(
            tmp_path,
            {
                "003_stats.sql": "CREATE TABLE stats (id INT);",
                "001_users.sql": "CREATE TABLE users (id INT);",
                "005_index.sql": "CREATE INDEX idx ON users (id);",
            },
        )

        runner = MigrationRunner(pool, migrations_dir=migrations_dir)
        applied = await runner.run()

        assert applied == ["001", "003", "005"]

    @pytest.mark.asyncio
    async def test_missing_dir_returns_empty(self, pool_and_conn, tmp_path):
        """run() не падает если папка миграций не существует."""
        pool, conn = pool_and_conn
        conn.fetch.return_value = []

        runner = MigrationRunner(pool, migrations_dir=tmp_path / "nonexistent")
        applied = await runner.run()

        assert applied == []

    def test_extract_version(self):
        """_extract_version корректно извлекает версию из имени файла."""
        assert MigrationRunner._extract_version("001_create_users.sql") == "001"
        assert MigrationRunner._extract_version("005_add_index.sql") == "005"
        assert MigrationRunner._extract_version("100_big_change.sql") == "100"

    @pytest.mark.asyncio
    async def test_records_applied_version(self, pool_and_conn, tmp_path):
        """run() записывает версию и имя файла в schema_migrations."""
        pool, conn = pool_and_conn
        conn.fetch.return_value = []

        migrations_dir = _make_migration_dir(
            tmp_path,
            {"001_users.sql": "CREATE TABLE users (id INT);"},
        )

        runner = MigrationRunner(pool, migrations_dir=migrations_dir)
        await runner.run()

        # Найти вызов INSERT INTO schema_migrations
        insert_calls = [
            call
            for call in conn.execute.call_args_list
            if len(call[0]) >= 1
            and "schema_migrations" in str(call[0][0])
            and "INSERT" in str(call[0][0])
        ]
        assert len(insert_calls) == 1
        assert insert_calls[0][0][1] == "001"
        assert insert_calls[0][0][2] == "001_users.sql"
