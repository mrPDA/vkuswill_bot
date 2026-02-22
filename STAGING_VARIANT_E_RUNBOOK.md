# Staging Runbook (Variant E / Qwen over MCP)

Цель: исключить путаницу между staging и production при внедрении `CHAT_ENGINE=shopping_agent`.

Статус: рабочая инструкция для команды до полного rollout в production.

## 1. Непересекаемые контуры

Обязательные правила:
1. У staging отдельный Telegram bot token.
2. У staging отдельный Alice skill (draft), отдельный `ALICE_SKILL_ID`.
3. У staging отдельные секреты Lockbox (не использовать production secret id).
4. У staging отдельные контейнеры/порты/путь деплоя.
5. У staging отдельный Langfuse project (или отдельный host).
6. Деплой staging выполняется только через `deploy/deploy-staging.sh` и workflow `CD Staging`.

## 1.1. Где запускать staging

Файлы:
1. `/Users/denispukinov/Downloads/vkuswill_bot/deploy/deploy-staging.sh` — жёсткий wrapper с `-stg` ресурсами.
2. `/Users/denispukinov/Downloads/vkuswill_bot/.github/workflows/cd-staging.yml` — отдельный pipeline для staging.
3. `/Users/denispukinov/Downloads/vkuswill_bot/scripts/check_staging_deploy_guard.sh` — CI-gate, блокирует опасные изменения.

## 2. Именование (жестко фиксируем)

Использовать только такие имена:
1. Deploy dir: `/opt/vkuswill-bot-stg`
2. Bot container: `vkuswill-bot-stg`
3. MCP container: `vkuswill-mcp-server-stg`
4. Health port: `18080`
5. MCP port: `18081`
6. Public endpoint prefix: `/stg/`
7. Lockbox secret key: `LOCKBOX_SECRET_ID_STG`

Запрещено:
1. Использовать `vkuswill-bot` и `vkuswill-mcp-server` для staging.
2. Использовать production `BOT_TOKEN`, `ALICE_SKILL_ID`, `LOCKBOX_SECRET_ID`.
3. Публиковать staging webhook в production skill.

## 3. Env-модель staging

Минимально обязательные переменные staging:
1. `CHAT_ENGINE=shopping_agent`
2. `LLM_BASE_URL=https://llm.api.cloud.yandex.net/v1`
3. `LLM_API_KEY=<staging key>`
4. `LLM_MODEL=<staging model ref>`
5. `BOT_TOKEN=<staging telegram token>`
6. `ALICE_SKILL_ID=<staging skill id>`
7. `VOICE_LINK_API_KEY=<staging value>`
8. `DATABASE_URL=<staging db>`
9. `REDIS_URL=<staging redis>`

Рекомендуемые разделители данных:
1. Отдельная staging БД/схема.
2. Отдельный Redis DB index или key prefix.
3. Отдельный Langfuse проект.

## 4. Preflight-check перед деплоем staging

Выполнить и сохранить вывод в PR/comment:
```bash
echo "$DEPLOY_DIR"
echo "$CONTAINER_NAME"
echo "$MCP_CONTAINER_NAME"
echo "$LOCKBOX_SECRET_ID"
echo "$CHAT_ENGINE"
echo "$BOT_TOKEN" | sed 's/./*/g'
```

Критерии допуска:
1. `DEPLOY_DIR` указывает на `...-stg`.
2. Имена контейнеров заканчиваются на `-stg`.
3. `LOCKBOX_SECRET_ID` не равен production id.
4. `CHAT_ENGINE=shopping_agent`.

## 5. Rollback staging

Быстрый rollback в staging:
1. `CHAT_ENGINE=legacy`
2. redeploy staging
3. health-check `/stg/health`

Важно: rollback staging не должен трогать production env и production контейнеры.

## 6. Проверки после деплоя staging

Обязательные:
1. `GET /stg/health` -> `200`.
2. Golden equivalence tests проходят на staging-данных.
3. Voice-link `start/status` работает на staging skill.
4. Трейсы идут в staging Langfuse, не в production.

## 7. Переход в production

Разрешается только после:
1. Зеленого quality gate (pytest + ruff + golden).
2. Подтвержденных KPI на staging.
3. Явного change approval на включение `CHAT_ENGINE=shopping_agent` в production.

До этого момента production остается:
1. `CHAT_ENGINE=legacy`
2. production skill и production token без изменений.

## 8. Checklist секретов для CD Staging

Перед первым запуском workflow `/Users/denispukinov/Downloads/vkuswill_bot/.github/workflows/cd-staging.yml`
завести и проверить следующие secrets.

Обязательные secrets:
1. `YC_CR_REGISTRY_ID` — ID Container Registry.
2. `YC_CR_KEY_SECRET` — JSON key для `docker login cr.yandex`.
3. `VM_HOST_STG` — staging VM host/IP.
4. `VM_SSH_KEY_STG` — приватный SSH-ключ для staging VM.
5. `LOCKBOX_SECRET_ID_STG` — staging Lockbox secret id.

Проверка в workflow:
1. `CD Staging` имеет шаг `Validate required staging secrets`.
2. Пустой любой обязательный secret блокирует деплой до SSH шага.

## 9. Checklist ключей в Lockbox (staging)

Минимально для старта staging:
1. `BOT_TOKEN` — staging Telegram bot token.
2. `CHAT_ENGINE` — `shopping_agent`.
3. `LLM_BASE_URL`
4. `LLM_API_KEY`
5. `LLM_MODEL`
6. `MCP_SERVER_ENABLED` — `false` или `true` по целевому сценарию.
7. `MCP_SERVER_PORT` — `18081` (рекомендуемо для staging).
8. `WEBHOOK_HOST` — staging host.
9. `WEBHOOK_PORT` — `18080`.
10. `VOICE_LINK_API_KEY` — staging значение.
11. `DATABASE_URL` — staging DB.
12. `REDIS_URL` — staging Redis.

Рекомендуемые ключи для observability:
1. `LANGFUSE_ENABLED=true`
2. `LANGFUSE_HOST` (staging host/project)
3. `LANGFUSE_PUBLIC_KEY`
4. `LANGFUSE_SECRET_KEY`
