-- Миграция 001: Таблицы users и user_events
-- Применяется автоматически при первом запуске бота (UserStore._ensure_schema).

-- Таблица пользователей
CREATE TABLE IF NOT EXISTS users (
    user_id         BIGINT      PRIMARY KEY,   -- Telegram user ID
    username        TEXT,                        -- @username (nullable, может меняться)
    first_name      TEXT        NOT NULL DEFAULT '',
    last_name       TEXT,
    language_code   TEXT        DEFAULT 'ru',

    -- Роли и статус
    role            TEXT        NOT NULL DEFAULT 'user'
                    CHECK (role IN ('user', 'admin')),
    status          TEXT        NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'blocked', 'limited')),
    blocked_reason  TEXT,
    blocked_at      TIMESTAMPTZ,

    -- Персональные лимиты (NULL = дефолтные из config)
    rate_limit      INTEGER,
    rate_period     REAL,

    -- Статистика
    message_count   INTEGER     NOT NULL DEFAULT 0,
    last_message_at TIMESTAMPTZ,

    -- Временные метки
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Лог событий (для аналитики)
CREATE TABLE IF NOT EXISTS user_events (
    id          BIGSERIAL   PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_type  TEXT        NOT NULL,   -- 'message', 'search', 'cart', 'order', 'command'
    metadata    JSONB,                   -- данные события
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_events_user_id ON user_events(user_id);
CREATE INDEX IF NOT EXISTS idx_user_events_type ON user_events(event_type);
CREATE INDEX IF NOT EXISTS idx_user_events_created ON user_events(created_at);
