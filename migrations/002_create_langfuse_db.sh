#!/bin/bash
# Создать дополнительную БД для Langfuse (при инициализации PostgreSQL контейнера)
# Скрипт выполняется docker-entrypoint автоматически из /docker-entrypoint-initdb.d/

set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE langfuse;
    GRANT ALL PRIVILEGES ON DATABASE langfuse TO $POSTGRES_USER;
    CREATE DATABASE metabase;
    GRANT ALL PRIVILEGES ON DATABASE metabase TO $POSTGRES_USER;
EOSQL

echo "Databases 'langfuse' and 'metabase' created successfully"
