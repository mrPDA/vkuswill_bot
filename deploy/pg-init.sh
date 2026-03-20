#!/bin/bash
# ============================================================
# PostgreSQL init script — выполняется при первом запуске контейнера
# (когда /var/lib/postgresql/data пустая)
#
# Создаёт пользователей и базы данных:
#   - bot     / vkuswill  (Telegram-бот)
#   - langfuse / langfuse  (LLM-observability)
#   - metabase / metabase  (BI-дашборды)
#
# Монтируется в /docker-entrypoint-initdb.d/pg-init.sh
# Пароли передаются через env: PG_BOT_PASSWORD, PG_LANGFUSE_PASSWORD, PG_METABASE_PASSWORD
# ============================================================
set -e

echo "[pg-init] Creating users and databases..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    -- Users
    CREATE USER bot WITH PASSWORD '${PG_BOT_PASSWORD}' CONNECTION LIMIT 50;
    CREATE USER langfuse WITH PASSWORD '${PG_LANGFUSE_PASSWORD}' CONNECTION LIMIT 20;
    CREATE USER metabase WITH PASSWORD '${PG_METABASE_PASSWORD}' CONNECTION LIMIT 10;

    -- Databases
    CREATE DATABASE vkuswill OWNER bot;
    CREATE DATABASE langfuse OWNER langfuse;
    CREATE DATABASE metabase OWNER metabase;
EOSQL

# citext extension для Metabase (требуется для миграций)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname metabase <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS citext;
EOSQL

# bot user должен иметь полные привилегии на public-схему в vkuswill
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname vkuswill <<-EOSQL
    GRANT ALL PRIVILEGES ON SCHEMA public TO bot;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO bot;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO bot;
EOSQL

echo "[pg-init] Done: users (bot, langfuse, metabase) and databases (vkuswill, langfuse, metabase) created"
