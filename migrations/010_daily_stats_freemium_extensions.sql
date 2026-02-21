-- Миграция 010: расширение daily_stats под freemium-метрики для Metabase

ALTER TABLE daily_stats
    ADD COLUMN IF NOT EXISTS trial_carts INTEGER NOT NULL DEFAULT 0;

ALTER TABLE daily_stats
    ADD COLUMN IF NOT EXISTS referral_links INTEGER NOT NULL DEFAULT 0;

ALTER TABLE daily_stats
    ADD COLUMN IF NOT EXISTS referral_bonuses INTEGER NOT NULL DEFAULT 0;

ALTER TABLE daily_stats
    ADD COLUMN IF NOT EXISTS feedback_bonuses INTEGER NOT NULL DEFAULT 0;
