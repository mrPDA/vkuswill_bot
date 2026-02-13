-- Миграция 003: Ежедневные агрегаты для аналитики
-- Заполняется фоновой задачей StatsAggregator (раз в час).
-- Одна строка на день — компактная таблица для быстрых запросов.

CREATE TABLE IF NOT EXISTS daily_stats (
    date              DATE        PRIMARY KEY,
    dau               INTEGER     NOT NULL DEFAULT 0,   -- уникальных пользователей с session_start
    new_users         INTEGER     NOT NULL DEFAULT 0,   -- событий bot_start с is_new_user=true
    sessions          INTEGER     NOT NULL DEFAULT 0,   -- событий session_start
    carts_created     INTEGER     NOT NULL DEFAULT 0,   -- событий cart_created
    total_gmv         NUMERIC     NOT NULL DEFAULT 0,   -- сумма total_sum из cart_created
    avg_cart_value    NUMERIC     NOT NULL DEFAULT 0,   -- средний чек
    searches          INTEGER     NOT NULL DEFAULT 0,   -- событий product_search
    errors            INTEGER     NOT NULL DEFAULT 0,   -- событий bot_error
    cart_limits_hit   INTEGER     NOT NULL DEFAULT 0,   -- событий cart_limit_reached
    surveys_completed INTEGER     NOT NULL DEFAULT 0,   -- событий survey_completed
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
