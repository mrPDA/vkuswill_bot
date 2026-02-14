-- Миграция 007: Информированное согласие на обработку сообщений (ADR-002)
-- Добавляет поля для фиксации момента и способа согласия пользователя.

ALTER TABLE users ADD COLUMN consent_given_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN consent_type TEXT;  -- 'explicit' | 'implicit'

-- Ретроактивное согласие: existing users с сообщениями считаются
-- давшими implicit consent на момент регистрации.
UPDATE users SET consent_given_at = created_at, consent_type = 'implicit'
WHERE message_count > 0 AND consent_given_at IS NULL;
