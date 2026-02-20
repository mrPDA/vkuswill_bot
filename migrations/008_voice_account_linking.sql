-- Миграция 008: account linking для voice-клиентов (Алиса и др.)
-- Таблицы:
--   voice_account_links  -- активные связи voice user -> internal user
--   voice_link_codes     -- одноразовые коды привязки с TTL

CREATE TABLE IF NOT EXISTS voice_account_links (
    id              BIGSERIAL   PRIMARY KEY,
    voice_provider  TEXT        NOT NULL,  -- alice, etc.
    voice_user_id   TEXT        NOT NULL,
    user_id         BIGINT      NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    status          TEXT        NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'revoked')),
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (voice_provider, voice_user_id)
);

CREATE INDEX IF NOT EXISTS idx_voice_account_links_user_id
ON voice_account_links(user_id);

CREATE TABLE IF NOT EXISTS voice_link_codes (
    id                      BIGSERIAL   PRIMARY KEY,
    voice_provider          TEXT        NOT NULL,
    user_id                 BIGINT      NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    code_hash               TEXT        NOT NULL,
    expires_at              TIMESTAMPTZ NOT NULL,
    used_at                 TIMESTAMPTZ,
    used_by_voice_user_id   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Активные (неиспользованные) коды должны быть уникальны внутри provider.
CREATE UNIQUE INDEX IF NOT EXISTS ux_voice_link_codes_active
ON voice_link_codes(voice_provider, code_hash)
WHERE used_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_voice_link_codes_provider_expires
ON voice_link_codes(voice_provider, expires_at DESC);
