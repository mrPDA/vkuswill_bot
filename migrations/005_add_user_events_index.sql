-- Миграция 005: Составной индекс для ускорения воронки (get_funnel).
-- Покрывает запросы COUNT(DISTINCT user_id) с фильтром по event_type и created_at.

CREATE INDEX IF NOT EXISTS idx_user_events_type_created_user
    ON user_events (event_type, created_at, user_id);
