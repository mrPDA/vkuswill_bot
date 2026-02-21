-- Миграция 009: обновлённая freemium-модель
-- 10 дней безлимитного trial, затем бонусы:
-- A) survey +5, B) referral +3 после первой корзины друга, C) feedback +2 (1/30 дней)

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS feedback_bonus_granted_at TIMESTAMPTZ;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS referral_bonus_granted_at TIMESTAMPTZ;

-- Для новых пользователей после trial базовый лимит = 0.
ALTER TABLE users
    ALTER COLUMN cart_limit SET DEFAULT 0;
