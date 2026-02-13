-- Миграция 004: Счётчик корзин и лимиты для freemium-модели
-- Модель «5 + 5 + invite»: 5 бесплатных корзин, +5 за survey, +3 за реферала.

ALTER TABLE users ADD COLUMN IF NOT EXISTS carts_created    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS cart_limit       INTEGER NOT NULL DEFAULT 5;
ALTER TABLE users ADD COLUMN IF NOT EXISTS survey_completed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code    TEXT UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by      BIGINT REFERENCES users(user_id);
