# Changelog

Все значимые изменения проекта документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/),
версионирование следует [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Добавлено

- **CD Raspberry Pi** — GitHub Actions `.github/workflows/cd-pi.yml`: pytest на `ubuntu-latest`, деплой на **self-hosted runner** на Pi (метка `vkuswill-pi`); скрипт `deploy/deploy-pi.sh`; гайд `docs/deploy-pi-self-hosted-runner.md`
- **`scripts/pi-install-github-runner.sh`** + `make pi-install-github-runner` — установка runner на Pi (ARM64), опционально unattended через `RUNNER_REGISTRATION_TOKEN` / `RUNNER_REPO_URL`
- **`scripts/sync_cd_files_to_pi.sh`** + `make sync-cd-pi` — копирование CD/runner файлов на Pi по SSH (по умолчанию хост `vkbot`)

### Изменено

- **CD Yandex** — автозапуск по тегу `v*.*.*` только при variable репозитория `ENABLE_YANDEX_CD=true`; иначе достаточно CD Raspberry Pi без секретов Yandex Cloud

## [0.21.1] — 2026-03-03

### Исправлено

- **Race condition в lock-менеджерах** (FND-002) — LRU-вытеснение теперь пропускает занятые locks, предотвращая параллельную обработку для одного пользователя
- **Ownership enforcement для voice-link API** (FND-003) — `/voice-link/order/status` требует `user_id` или `voice_user_id`, запрос только по `job_id` отклоняется
- **Fail-fast при storage_backend=redis** (FND-001) — конфиг с `redis` теперь падает на старте с понятной ошибкой, пока Redis не интегрирован в Telegram dialog path
- **RuntimeWarning в тестах** (FND-004) — устранены unawaited coroutine и unclosed file warnings, корректные sync/async моки

## [0.21.0] — 2026-02-28

### Добавлено

- **Graceful degradation при перегрузке LLM** — таймаут семафора `llm_queue_timeout_seconds`, `LLMOverloadedError`, вежливое сообщение пользователю при перегрузке

### Исправлено

- **Дублирование вводной фразы в корзине** — убрана повторная "Собрала корзину по вашему запросу." когда LLM уже сгенерировала вступление

## [0.20.0] — 2026-02-28

### Добавлено

- **Langfuse env-теги** — каждый трейс автоматически получает тег `env:prod` или `env:stage` на основе `PROMPT_LABEL`, позволяя фильтровать трейсы по окружению в Langfuse UI
- **recipe-extraction промпт в Langfuse** — промпт для извлечения ингредиентов перенесён в Langfuse Prompt Registry (с fallback в коде)

### Исправлено

- **Поиск ингредиентов для рецептов** — LLM-промпт теперь запрещает добавлять контекст блюда в search_query (было: "говядина для лагмана" → стало: "говядина"); regex `_DISH_CONTEXT_RE` расширен как страховка

## [0.19.0] — 2026-03-01

### Добавлено

- **Production CD pipeline** — `deploy-production.sh` обёртка для production-деплоя (аналог staging), валидация guard, `PROMPT_LABEL=production` в fallback `.env`
- **PromptRegistry** — 4-tier fallback загрузки промптов (Langfuse → env/Lockbox → файлы → stubs), поддержка `prompt_label` для разделения staging/production
- **Intent classifier** — определение профиля промпта (general/cart/recipe/status/linking) по тексту запроса
- **Preference mismatch hint** — предупреждение LLM когда результат поиска не совпадает с предпочтением пользователя
- **Локальная обработка preference tools** — virtual injection preference tools в ShoppingAgent, маршрутизация через PreferencesStore (без MCP)
- **Preamble/postamble** — сохранение текста LLM до и после детерминированной корзины
- **max_tokens для рецептов** — увеличенный лимит токенов для корзины по рецепту

### Исправлено

- **Preference compaction** — tool_result_compactor больше не обрезает preference-данные
- **Staging deploy env** — staging-специфичные env через `DEPLOY_EXTRA_ENV`

### Изменено

- **Архитектура agents** — декомпозиция на pantry_tags, quantity_utils, shopping_turn_components, cart_price_builder
- **user_store модуль** — рефакторинг tool_executor_pipeline, dialog_types

## [0.18.6] — 2026-02-23

### Исправлено

- **Langfuse usage для Qwen** — добавлен fallback расчёт токенов в `ShoppingAgent`, если провайдер не вернул `usage`; теперь `usage_details` не остаётся пустым в generation-traces
- **Маркировка источника usage** — в metadata generation добавляется `usage_source=provider|estimated|missing` для диагностики качества метрик
- **Формат legacy usage в Langfuse wrapper** — cost-ключи переведены в snake_case (`input_cost/output_cost/total_cost`) для совместимости с SDK v2 model usage schema
- **Тесты observability** — добавлены unit-тесты на fallback usage и формирование payload в `langfuse_tracing`

## [0.18.5] — 2026-02-23

### Исправлено

- **Qwen usage в Langfuse** — расширен парсинг usage-метрик (`input/output/total`) для альтернативных форматов ответа OpenAI-compatible API, включая `input_tokens`, `outputTokens`, строковые значения и fallback-расчёт `total = input + output`
- **Снижение токен-расхода в shopping_agent** — добавлены runtime-параметры `LLM_MAX_TOKENS` и `LLM_TEMPERATURE`, уменьшены дефолты `MAX_TOOL_CALLS` и лимиты истории для сокращения стоимости LLM-запросов
- **Release pipeline** — синхронизирована версия проекта в `pyproject.toml` с текущим циклом релизов, чтобы шаг `Verify pyproject.toml version` проходил для тегированных релизов

## [0.18.2] — 2026-02-20

### Исправлено

- **MCP ingress через nginx** — для маршрута `/mcp` зафиксирован upstream `Host: 127.0.0.1:8081`, чтобы исключить `421 Invalid Host header` от FastMCP при внешних запросах
- **Self-signed SSL nginx-конфиг** — в `setup-selfsigned-ssl.sh` добавлены маршруты `/mcp` и `/langfuse/`, чтобы не терять эти endpoint после bootstrap
- **Fallback SSL-конфиг в setup-ssl.sh** — синхронизирован с production-маршрутами `/mcp` и `/langfuse/`
- **Langfuse DATABASE_URL обработка** — устранено двойное URL-кодирование пароля в `deploy.sh`, из-за которого Langfuse падал с `P1000` (auth failed)

## [0.18.1] — 2026-02-19

### Исправлено

- **MCP HTTP startup в production** — убран некорректный вызов `FastMCP.run(..., host=..., port=...)`; host/port теперь задаются через `mcp.settings`, что совместимо с `mcp==1.26.x`
- **Тесты entrypoint MCP** — обновлены проверки `tests/test_mcp_server_main.py` для валидации HTTP-запуска через `settings`

## [0.18.0] — 2026-02-19

### Добавлено

- **MCP Server в production-контуре** — в CD/deploy добавлен отдельный контейнер `vkuswill-mcp-server` с управлением через `MCP_SERVER_ENABLED` и `MCP_SERVER_PORT`, плюс health-check после деплоя
- **Поддержка multi-client API keys для MCP** — авторизация теперь поддерживает `MCP_SERVER_API_KEYS` (JSON map) и fallback на `MCP_SERVER_API_KEY`
- **MCP endpoint в nginx** — добавлен reverse-proxy маршрут `/mcp` на внутренний порт MCP-сервера

### Изменено

- **Lockbox/Terraform для MCP** — добавлены переменные и секреты `MCP_SERVER_ENABLED`, `MCP_SERVER_PORT`, `MCP_SERVER_API_KEY`, `MCP_SERVER_API_KEYS`
- **CI security gate** — подключён `gitleaks` и исправлен checkout (`fetch-depth: 0`) для корректной работы secret scan в PR

## [0.17.0] — 2026-02-17

### Безопасность

- **Очистка Git-истории от секретов** — удалены `infra/sa-key.json`, `terraform.tfvars`, `backend.conf`, `tfplan`, `.terraform/` из всей истории через `git filter-repo`
- **Ротация всех скомпрометированных ключей** — Yandex Cloud SA, S3, PostgreSQL, Redis, Langfuse, Metabase, Bot Token, GigaChat API
- **SYSTEM_PROMPT вынесен в Yandex Lockbox** — промпт больше не хранится в репозитории, передаётся через секрет-менеджер

### Исправлено

- **GigaChat SSL** — CA bundle (`russian_ca_bundle.pem`) включён в Docker-образ (`COPY certs/`), GigaChat работает с `verify=True`
- **Баг с предпочтениями** — LLM больше не перезаписывает предпочтения при обычных заказах («хочу молоко» = покупка, не предпочтение); добавлено предупреждение при перезаписи существующего предпочтения
- **S3 Lifecycle Policy** — убран дублирующий runtime-вызов `ensure_lifecycle_policy()`, политика управляется через Terraform

### Изменено

- **GitHub Actions обновлены** — `actions/checkout` v4→v6, `astral-sh/setup-uv` v4→v7, `actions/upload-artifact` v4→v6, `docker/build-push-action` v5→v6
- **deploy.sh** — `SYSTEM_PROMPT` передаётся отдельным файлом вместо `--env-file` (поддержка многострочных значений)

## [0.16.0] — 2026-02-16

### Добавлено

- **Прогресс-индикатор сборки корзины** — бот показывает живое обновление статуса на каждом этапе: «⚙️ Анализирую запрос...» → «🍳 Подбираю рецепт...» → «🔍 Ищу продукты (3/12) — 25%» → «🛒 Формирую корзину...» → «✍️ Готовлю ответ...»; прогресс-сообщение автоматически удаляется перед отправкой финального ответа
- **Sub-progress для поиска ингредиентов** — при пакетном поиске рецепта обновляется счётчик найденных ингредиентов в реальном времени (on_found callback)

## [0.15.1] — 2026-02-16

### Добавлено

- **Админ-команда `/admin_cart_feedback`** — сводная статистика по обратной связи корзин: всего оценок, позитивных/негативных, satisfaction %, причины негатива, последние негативные отзывы
- **`UserStore.get_cart_feedback_stats()`** — агрегированные SQL-запросы по `user_events` с `event_type = 'cart_feedback'`

## [0.15.0] — 2026-02-16

### Добавлено

- **Кнопки обратной связи по корзине** — после сборки корзины к сообщению добавляются inline-кнопки «👍 Подобрано хорошо» / «👎 Не то»; при негативном фидбеке показываются уточняющие причины (не те товары / количество / дорого / другое); результаты сохраняются через `user_store.log_event("cart_feedback", ...)` для аналитики в Langfuse/Metabase

## [0.14.3] — 2026-02-16

### Исправлено

- **Инвалидация кэша рецептов при смене промпта** — добавлен `prompt_version` в `RecipeStore`; при обновлении `RECIPE_EXTRACTION_PROMPT` устаревший кэш автоматически сбрасывается и рецепт перегенерируется с новым промптом

## [0.14.2] — 2026-02-16

### Исправлено

- **Фильтрация нон-фуд товаров** — семена, рассада, саженцы, удобрения депроритизируются при выборе `best_match` для рецепта; поиск «свекла» теперь возвращает овощ, а не пакет семян «Свекла Мулатка»

## [0.14.1] — 2026-02-16

### Исправлено

- **suggested_q для микро-единиц** — зубчик, ст.л., ч.л., пучок, лист без `kg_equivalent` теперь дают `q=1` вместо сырого количества из рецепта (3 зубчика чеснока → 1 пачка, не 3)
- **Cap на дискретные товары** — `suggested_q` ограничен `MAX_DISCRETE_Q=5` для предотвращения заведомо завышенных количеств
- **Улучшен RECIPE_EXTRACTION_PROMPT** — расширенная таблица пересчёта `kg_equivalent` (свёкла, лаврушка, морковь и др.), исправлены плохие примеры `search_query` из реальных трейсов

## [0.14.0] — 2026-02-16

### Добавлено

- **Пакетный поиск ингредиентов (RecipeSearchService)** — новый сервис для параллельного поиска всех ингредиентов рецепта одним tool-вызовом вместо последовательных запросов; `asyncio.Semaphore(5)` для контроля конкурентности, автоматическое кэширование цен и синхронизация `search_log`
- **Anti-hallucination hint** — системная подсказка GigaChat после успешного создания корзины (`MessagesRole.USER`) с флагом `cart_success_hint_injected` для предотвращения дублирования; бот больше не «галлюцинирует» извинения после первой удачной корзины
- **kg_equivalent в RECIPE_EXTRACTION_PROMPT** — обязательное поле приблизительного веса ингредиента в кг для нестандартных единиц (зубчик, ст.л., ч.л., пучок); улучшает расчёт `suggested_q` в RecipeSearchService
- **Позитивный verify_cart** — отчёт проверки корзины содержит явное «Все позиции найдены» при полном совпадении
- **Observability для рецептов** — метрики `recipe_mode`, `recipe_search_used`, `recipe_search_count` в Langfuse

### Изменено

- **Улучшенный RECIPE_EXTRACTION_PROMPT** — лучшие search_query (1-2 слова как в магазине), обязательный kg_equivalent с примерами пересчёта
- **ToolExecutor** — маршрутизация нового инструмента `recipe_search`, синхронизация search_log из batch-результатов

## [0.13.0] — 2026-02-16

### Добавлено

- **Админ-команда /admin_reset_carts** — сброс счётчика корзин (`carts_created = 0`, `cart_limit = 5`, `survey_completed = FALSE`) для тестирования freemium-модели; метод `UserStore.reset_carts()` + хендлер в admin_router

## [0.12.4] — 2026-02-14

### Исправлено

- **AdminFilter: inner → outer middleware** — в aiogram 3 inner middleware (`dp.message.middleware`) запускается ПОСЛЕ `check_root_filters` в `_propagate_event()`, поэтому `AdminFilter` не видел `db_user` и все `/admin_*` команды отклонялись; заменён на `outer_middleware`, который оборачивает `propagate_event` целиком и запускается ДО root-фильтров

## [0.12.3] — 2026-02-14

### Исправлено

- **AdminFilter: kwargs вместо именованного параметра** — aiogram не инжектирует middleware-данные (`db_user`) в router-level фильтры через именованные параметры `__call__`; переход на `**kwargs` гарантирует получение `db_user` из data-контекста

## [0.12.2] — 2026-02-14

### Добавлено

- **Команда /me** — диагностика профиля: role, status, лимиты корзин, consent (для отладки admin-проблем)
- **Расширенное логирование** — `UserMiddleware` и `AdminFilter` логируют role/status/db_user_keys при admin-командах

## [0.12.1] — 2026-02-14

### Исправлено

- **Перехват admin-команд** — заменён `F.text.startswith` (magic-filter) на явный `BaseFilter` для надёжного перехвата `/admin_*` от неавторизованных пользователей; добавлено логирование решений `AdminFilter`
- **UserMiddleware для callback queries** — зарегистрирован `dp.callback_query.middleware` с облегчённой обработкой (read-only, без upsert); survey-кнопки и consent-кнопка теперь получают `db_user`

## [0.12.0] — 2026-02-14

### Добавлено

- **Информированное согласие (ADR-002)** — новые пользователи при /start видят уведомление об обработке сообщений ИИ-моделью GigaChat (Сбер) + кнопку «Понятно, начать!»; согласие фиксируется в БД (explicit/implicit)
- **Команда /privacy** — полная политика конфиденциальности: какие данные обрабатываются, кому передаются, как защищены, как удалить
- **Миграция 007** — поля `consent_given_at`, `consent_type` в таблице users + ретроактивное согласие для existing users
- **PRIVACY_POLICY.md** — документ политики конфиденциальности в репозитории

## [0.11.2] — 2026-02-14

### Изменено

- **Inline-кнопка корзины** — текстовая ссылка «Открыть корзину» убрана из тела сообщения, остаётся только inline-кнопка под сообщением; меньше визуального шума, нет ощущения рекламы

## [0.11.1] — 2026-02-14

### Исправлено

- **admin_user_ids из Lockbox** — Lockbox возвращал одиночное число вместо списка, pydantic отклонял конфиг при старте; добавлен `field_validator` для парсинга `int → [int]`
- **CD: no space left on device** — Docker-очистка (`system prune`, `image prune`) выполняется ДО `docker pull` и `docker login` на VM
- **Ruff format** — применено автоформатирование к `pii_utils.py`, `__main__.py`, `test_handlers.py`
- **Ruff lint** — инлайнирован `return` в `AdminFilter` (SIM103), удалён неиспользуемый импорт `_sanitize_history` (F401)

## [0.11.0] — 2026-02-14

### Добавлено

- **Защита персональных данных (152-ФЗ)** — общий модуль `pii_utils.py`: хеширование идентификаторов (SHA-256), маскировка PII (телефоны, email, карты, ИНН, СНИЛС), санитизация аргументов tool calls
- **S3 Lifecycle Policy** — автоудаление логов через 90 дней (`s3_log_retention_days`), политика устанавливается при старте бота
- **Inline-кнопка «Открыть корзину»** — после сборки корзины бот добавляет URL-кнопку для быстрого перехода на сайт ВкусВилл

### Изменено

- **S3LogHandler** — `user_id` / `chat_id` хешируются перед записью, `message` проходит PII-маскировку, добавлен `retention_days`
- **GigaChat логирование** — аргументы tool calls санитизируются через `sanitize_tool_args()`, результаты проходят `mask_pii()`
- **Langfuse** — `anonymize_messages=True` по умолчанию (текст сообщений не отправляется в Langfuse Cloud)
- **langfuse_tracing.py** — дедупликация кода: `_anonymize_user_id` и `_mask_pii` переиспользуют `pii_utils`

## [0.10.1] — 2026-02-14

### Исправлено

- **AdminFilter double-response** — router-level фильтр в aiogram 3 вызывался для КАЖДОГО сообщения, отправляя «нет прав администратора» ВСЕМ пользователям перед основным ответом; побочный эффект удалён, отказ теперь отправляется отдельным хендлером `handle_admin_unauthorized` только при попытке вызвать `/admin_*` команду
- **Admin-команды проваливались в GigaChat** — `/admin_analytics` и другие admin-команды от неадминов уходили в GigaChat вместо корректного отказа; добавлен перехватчик `F.text.startswith("/admin_")` перед `handle_text`

## [0.10.0] — 2026-02-14

### Добавлено

- **Metabase-дашборд PMF-опроса** — скрипт `setup_metabase_dashboards.py` создаёт дашборд с результатами Sean Ellis PMF-теста (распределение ответов, PMF score, текстовые отзывы)

### Исправлено

- **PEM-границы в CA-bundle** — исправлены склеенные `END CERTIFICATE` / `BEGIN CERTIFICATE` строки в `russian_ca_bundle.pem`, из-за которых SSL-верификация могла сбоить
- **Замечания code review Барбоссы** — исправления в GigaChat-сервисе по результатам ревью (типизация, обработка ошибок, async-паттерны)

## [0.9.1] — 2026-02-14

### Исправлено

- **Survey pending leak** — при недоступном `user_store` словарь `_survey_pending` не очищался, пользователь застревал в цикле перехвата сообщений; теперь pending очищается всегда

## [0.9.0] — 2026-02-14

### Добавлено

- **Sean Ellis PMF-тест** — заменён NPS (1-5 звёзд) на валидированный PMF-индикатор: «Как расстроитесь, если бот перестанет работать?» (>40% «Очень» = product-market fit)
- **Открытый текстовый отзыв** — шаг 3 опроса: пользователь пишет текст или нажимает «Всё отлично» (перехват в `handle_text`)
- **PMF score в `/admin_survey_stats`** — процент «очень расстроятся» + последние текстовые отзывы
- **11 тестов survey** — PMF callback, текстовый отзыв через handle_text, done callback, pending state

### Изменено

- **Survey flow** — 3 шага: PMF → полезная фича → открытый отзыв (вместо NPS → фича → «будете ли дальше»)
- **Варианты фич** — убраны нереализованные КБЖУ/бюджет, добавлены Сборка корзины/Другое
- **`user_store.get_survey_stats()`** — возвращает `pmf`, `feedback_count`, `recent_feedback` вместо `avg_nps`, `will_continue`
- **metadata `survey_completed`** — поля `pmf`, `useful_feature`, `feedback` вместо `nps`, `will_continue`

### Удалено

- **NPS (1-5 звёзд)** — заменён на Sean Ellis PMF-тест
- **Вопрос «Будете ли дальше?»** — заменён открытым отзывом (self-reported intent ненадёжен при incentive bias)

## [0.8.0] — 2026-02-14

### Добавлено

- **Анонимизация ПДн** — прекращена запись `username`, `first_name`, `last_name` в таблицу `users`; для работы бота и поддержки достаточно `user_id`
- **Миграция 006** — очистка существующих персональных данных в БД (`006_anonymize_pii.sql`)
- **Скрипт настройки Metabase-дашбордов** — автоматизация создания дашбордов через Metabase API (`scripts/setup_metabase_dashboards.py`)

### Изменено

- **UserStore.get_or_create()** — убраны параметры PII, upsert работает только с `user_id` и `language_code`
- **UserStore.ensure_admins()** — убрано `first_name = 'Admin'` из INSERT
- **/admin_user** — убран вывод username/имени, добавлена статистика корзин
- **Metabase-дашборд** — `user_id` вместо `COALESCE(username, first_name)` в карточке «Топ-пользователи»

## [0.7.0] — 2026-02-14

### Добавлено

- **Metabase BI-дашборды на production** — Terraform: PG user/database `metabase`, Security Group порт 3001, Lockbox секреты `METABASE_ENABLED` и `METABASE_DATABASE_URL`
- **deploy_metabase() в deploy.sh** — автоматический деплой контейнера `metabase/metabase:v0.58` на VM через CD pipeline (по аналогии с Langfuse)
- **CD pipeline: поддержка Metabase** — fallback env-переменные, диагностика контейнера в логах деплоя

## [0.6.0] — 2026-02-14

### Добавлено

- **Реферальная система** — команда `/invite` для генерации персональной реферальной ссылки, начисление бонусных корзин рефереру при регистрации нового пользователя, уведомление реферера о присоединении друга
- **Deep-link ref\_\<code\>** — обработка строковых реферальных кодов при `/start` (обратная совместимость с числовыми user\_id)
- **UserStore: реферальные методы** — `get_or_create_referral_code()`, `find_user_by_referral_code()`, `process_referral()`, `count_referrals()`
- **14 новых/обновлённых тестов** — покрытие реферальной системы, дифференцированных сообщений, атрибуции source

### Улучшено

- **Дифференцированные сообщения при исчерпании лимита** — Tier 1 (опрос не пройден): предложение `/survey`; Tier 2 (опрос пройден): предложение `/invite`
- **Корректная атрибуция source** — `source=organic` при невалидных реферальных кодах (ранее ошибочно `referral`)
- **Защитная проверка `message.bot`** — предотвращение `AttributeError` в `/invite` при `bot is None`

## [0.5.22] — 2026-02-14

### Исправлено

- **Объединение 2 корзин в одну** — после успешного создания корзины (`vkusvill_cart_link_create ok:true`) следующий вызов GigaChat принудительно текстовый (`function_call=none`), чтобы модель не продолжала собирать товары из предыдущих запросов в истории
- **Ошибка 422 при обрезке истории** — при `trim_list` финальный срез мог разорвать пару ASSISTANT(function_call) + FUNCTION, оставляя осиротевшее FUNCTION-сообщение. Добавлена санитизация `_sanitize_history()` после каждой обрезки (DialogManager, RedisDialogManager)

## [0.5.21] — 2026-02-14

### Исправлено

- **Диспетчеризация суммаризатора tool results** — OR-условия (`name == "tool" or "key" in data`) заменены на двухуровневую диспетчеризацию: точное совпадение по `name` (приоритет) → эвристика по ключам JSON (только если `name is None`). Устранена некорректная суммаризация при пересечении ключей между инструментами

### Добавлено

- 19 тестов на диспетчеризацию `_summarize_tool_result`, включая 3 регрессионных теста с «ключами-ловушками»

## [0.5.20] — 2026-02-14

### Исправлено

- **GigaChat 422 «invalid function result json string»** — freemium-хинт `[Корзина X из Y]` дописывался как plain-text к JSON-результату корзины, ломая валидность JSON для GigaChat API. Теперь хинт встраивается внутрь JSON-структуры (`data.freemium`)
- **HTML-сущности в названиях товаров** — `&nbsp;` из MCP-сервера заменяется на пробел в price_summary, `×` → `x` для совместимости с JSON-парсером GigaChat

## [0.5.15] — 2026-02-13

### Улучшено

- **X-Session-ID кеширование** — prefix-токены (system prompt + tools) кешируются между API-вызовами через `session_id_cvar`, ожидаемая экономия ~50-65% оплачиваемых токенов
- **Оптимизация SYSTEM_PROMPT** — удалены дубли, сжаты секции «Соленья», «Расчёт количества», «Алкоголь», «Анти-галлюцинация» (−15.4%, −579 токенов overhead)
- **Умная обрезка истории** — старые FUNCTION-сообщения суммаризируются при trim (вместо ~1000 токенов → ~30 токенов за tool result)
- **Сокращение recipe hint** — ~500→~120 символов (дубли перенесены в SYSTEM_PROMPT)
- **Расширенное логирование токенов** — `precached_tokens`, `billable_tokens` в structured logs и Langfuse metadata

## [0.5.14] — 2026-02-13

### Исправлено

- **deploy.sh** — добавлен `sudo` для `chown DATA_DIR` (deploy-пользователь не может менять владельца файлов, созданных контейнером)

## [0.5.13] — 2026-02-13

### Добавлено

- **MigrationRunner** — версионированная система SQL-миграций (заменяет `ensure_schema()`), отслеживает применённые миграции в таблице `schema_migrations`
- **StatsAggregator** — фоновая агрегация `user_events → daily_stats` (DAU, новые пользователи, сессии) с периодом 1 час
- **Survey flow** — опрос для получения бонусных корзин (freemium-модель): `/survey`, inline-кнопки, `SURVEY_BONUS_CARTS` в конфиге
- **Deep link tracking** — `/start habr`, `/start ref_123`, `/start vc` — атрибуция источника трафика через `user_events`
- **Лимиты корзин** — `FREE_CART_LIMIT` / `SURVEY_BONUS_CARTS` в config, проверка в `ToolExecutor`
- **Admin-команды аналитики** — `/admin_analytics [N]`, `/admin_funnel [N]` с агрегированными данными
- **UserStore: расширенные методы** — `log_event()`, `get_cart_count()`, `get_events()`, `complete_survey()`, admin-статистика
- **Metabase** — BI-дашборды в `docker-compose.yml` для локальной аналитики (daily_stats, user_events, users)
- **SQL-миграции** — `003_daily_stats`, `004_add_cart_limits`, `005_add_user_events_index`

### Изменено

- **Dockerfile** — фиксированный UID/GID 10001 для предсказуемых прав на файловую систему
- **deploy.sh** — `chown 10001:10001` + `chmod 750` вместо `chmod a+rwx` (безопасность DATA_DIR)

### Исправлено

- **ruff lint** — SIM105, RUF059, F401, E501, SIM102 в коде и тестах (включая pre-existing в persona_live_test)

## [0.5.12] — 2026-02-13

### Исправлено

- **force_text не срабатывал после подсказки о корзине** — `consecutive_skips` сбрасывался в 0 после инъекции подсказки, из-за чего для принудительного текстового ответа требовалось ещё 3 дубликата вместо 1; теперь сбрасывается в `max_consecutive_skips - 1`, и уже следующий дубликат включает force_text

## [0.5.11] — 2026-02-12

### Добавлено

- **КБЖУ-сервис (nutrition_lookup)** — новый инструмент для получения калорий, белков, жиров и углеводов на 100 г через Open Food Facts API (бесплатный, без API-ключа, доступен из РФ). Поддерживает русский язык, содержит ~30K русских продуктов включая ВкусВилл
- **Секция «Планирование питания на N дней»** в системном промпте — бот делает несколько поисков по категориям (суп, второе, салат), выбирает разнообразные блюда и сразу собирает корзину
- **Секция «КБЖУ и калорийность»** в системном промпте — бот использует nutrition_lookup для фильтрации по ккал, не выдумывает калорийность

## [0.5.8] — 2026-02-12

### Исправлено

- **GigaChat путал граммы рецепта с количеством упаковок** — `q=170` для пачки сахара 1 кг (= 170 кг!), `q=250` для бутылки молока 2 л (= 250 бутылок); добавлен вес упаковки (`weight_value`/`weight_unit`) в PriceInfo, умный пересчёт в `fix_unit_quantities`: `q = ceil(граммы_рецепта / вес_упаковки)` (170г / 1000г = 1 пачка)
- **PriceCache теперь хранит вес упаковки** — вес из search results (`weight: {value, unit}`) сохраняется в L1 (in-memory) и L2 (Redis), используется для валидации q

## [0.5.7] — 2026-02-12

### Исправлено

- **Яйца считались упаковками** — GigaChat ставил q=2 для 2 яиц (= 2 упаковки по 10 = 20 яиц за 444 руб); добавлен `pack_equivalent` в рецепты и cap q=1 для яиц в cart_processor
- **Лишние ингредиенты в рецепте** — GigaChat добавлял от себя «сливки 33%» (→ кокосовое молоко) к Наполеону; усилен запрет на добавление ингредиентов не из списка

## [0.5.6] — 2026-02-12

### Исправлено

- **Смешивание рецептов при переключении блюд** — GigaChat вызывал get_previous_cart во время рецептного flow, подмешивая ингредиенты старого рецепта (Оливье) в новый (Наполеон); добавлен запрет get_previous_cart в режиме рецепта

## [0.5.5] — 2026-02-12

### Исправлено

- **Анти-галлюцинация в системном промпте** — GigaChat выдумывал цены и ссылки на корзину без вызова инструментов; добавлены строгие правила в system prompt: обязательная последовательность поиск → корзина, запрет на генерацию цен/ссылок без tool calls
- **Регрессия hint recipe_ingredients** — запретительная формулировка hint заставляла GigaChat пропускать поиск и корзину целиком; переписан на директивную формулировку
- **Конвертация г→кг и мл→л в рецептах** — `_enrich_with_kg` теперь добавляет `kg_equivalent` и `l_equivalent` для ингредиентов в граммах/мл (200 г → 0.2 кг), чтобы GigaChat не путал граммы с количеством
- **Качество извлечения рецептов** — RECIPE_EXTRACTION_PROMPT усилен: минимум 6-8 ингредиентов для выпечки, минимум 4-6 для основных блюд

## [0.5.0] — 2026-02-12

### Добавлено

- **Langfuse LLM-observability** — трейсинг всех вызовов GigaChat (trace → generation → span)
- **Self-hosted Langfuse** — контейнер на VM рядом с ботом, данные в РФ (Yandex Cloud)
- **Анонимизация** — SHA-256 хеш user_id, автоматическая маскировка PII (телефоны, email, карты)
- **Terraform: Langfuse DB** — БД `langfuse` + пользователь в существующем Managed PostgreSQL
- **Lockbox: Langfuse секреты** — DATABASE_URL, NEXTAUTH_SECRET, SALT
- **Nginx: Langfuse UI** — проксирование через `/langfuse/` (HTTPS)
- **docker-compose: Langfuse** — self-hosted для локальной разработки
- **No-Op трейсинг** — нулевой оверхед когда Langfuse отключён
- **Промпты** — улучшены инструкции для корзины и рецептов
- **SSL** — самоподписанный сертификат для Telegram webhook + nginx reverse proxy
- **Тесты** — расширено покрытие до 1231 теста (S3LogHandler, промпты, конфигурация)

### Исправлено

- CD pipeline — S3 log переменные, GIGACHAT_MODEL, docker login, .env provisioning
- Deploy script — обработка отсутствия yc CLI и ошибок Lockbox

## [0.4.0] — 2026-02-11

### Добавлено

- **Async Cart Processor** — переход CartProcessor и ToolExecutor на async API
- **Двухуровневый кэш цен** — L1 in-memory + L2 Redis для PriceCache
- **Снимки корзины** — CartSnapshotStore сохраняет корзины в Redis
- **Async DialogManager** — асинхронный API и Redis-бэкенд для диалогов
- **CD Pipeline** — GitHub Actions workflow для автоматического деплоя на Yandex Cloud VM
- **Deploy-скрипт** — deploy.sh с Lockbox-секретами, Docker, health check
- **S3 логирование** — S3LogHandler для отправки логов в Yandex Object Storage (NDJSON)
- **Dockerfile** — multi-stage build, оптимизированный для production
- **docker-compose.yml** — локальная среда с Redis и PostgreSQL
- **Dependabot** — автообновление pip-зависимостей и GitHub Actions
- **Terraform** — инфраструктура Yandex Cloud (VM, CR, Redis, PostgreSQL, Lockbox, S3)
- **Система пользователей** — UserStore и UserMiddleware для управления пользователями
- **Admin-команды** — управление пользователями через Telegram
- **Load-тесты** — Locust + Telethon для нагрузочного тестирования
- **Миграции БД** — SQL-миграции для PostgreSQL
- **Семафор GigaChat** — ограничение параллельных запросов (15 по умолчанию)
- **Retry 429** — автоматический retry при rate-limiting от GigaChat API
- **Тесты** — расширено покрытие до 1141 теста

### Исправлено

- CI pipeline — корректная валидация merge-коммитов
- Ruff — игнорирование RUF001/RUF002/RUF003 для кириллицы
- Bandit B104 — nosec для webhook bind 0.0.0.0
- Импорт SYSTEM_PROMPT после переноса в prompts.py
- cryptography 46.0.4 → 46.0.5 (CVE-2026-26007)

### Изменено

- GigaChat God Class декомпозирован на 4 модуля (prompts, cart_processor, search_processor, tool_executor)
- MCP-клиент очищен, PriceCache выделен в отдельный модуль
- Безопасность усилена — HTML-санитизация, rate-limiter, хранилища
- Документация перенесена в docs/

## [0.3.0] — 2026-02-08

### Добавлено

- **Извлечение ингредиентов рецепта** — инструмент `recipe_ingredients` для GigaChat, автоматически разбивает блюдо на ингредиенты с расчётом количества
- **RecipeStore** — SQLite-кеш рецептов с TTL для ускорения повторных запросов
- **RECIPE_EXTRACTION_PROMPT** — специализированный промпт для извлечения ингредиентов из LLM
- **Расчёт количества по рецепту** — инструкции в системном промпте для корректного расчёта q с учётом размеров упаковок
- **ROADMAP** — план развития бота (публичный и технический)
- **Черновик статьи для Хабра** — articles/01-hook.md
- **Тесты** — расширено покрытие до 669 тестов (RecipeStore, промпты, GigaChat edge-cases, SearchProcessor, CartProcessor, Handlers, MCP Client)

### Исправлено

- Обработка `price_info` в SearchProcessor — защита от не-dict значений
- `.cursorignore` для корректной работы IDE

### Изменено

- `max_tool_calls` увеличен с 15 до 20 для поддержки рецептов с большим числом ингредиентов
- Системный промпт расширен инструкциями по работе с рецептами и расчёту количества

## [0.2.0] — 2026-02-06

### Добавлено

- **GigaChat интеграция** — ИИ-оркестрация с function calling для поиска товаров и сборки корзин
- **MCP-клиент** — JSON-RPC клиент для взаимодействия с MCP-сервером ВкусВилл (поиск, детали товаров, создание корзины)
- **Хранилище предпочтений** — SQLite-хранилище (aiosqlite) для запоминания вкусовых предпочтений пользователей
- **ThrottlingMiddleware** — rate limiting: 5 сообщений / 60 секунд на пользователя
- **Команда /reset** — сброс истории диалога
- **Верификация корзины** — автоматическая проверка, что все запрошенные товары попали в корзину
- **Кэширование цен** — цены из результатов поиска кэшируются для расчёта стоимости
- **Защита от зацикливания** — детекция повторных вызовов одних и тех же инструментов
- **CI/CD** — GitHub Actions для тестирования (Python 3.11–3.13), линтинга и автоматических релизов
- **Git hooks** — валидация Conventional Commits и запуск тестов перед push
- **Makefile** — утилиты разработки (install, test, lint, format, run)
- **Тесты безопасности** — SAST, AI Safety (prompt injection, jailbreak), Config Security, Input Validation
- **Шаблоны GitHub** — Issue templates (bug report, feature request), PR template

### Изменено

- Полностью переработан README.md с документацией функционала, архитектуры и инструкциями

## [0.1.0] — 2026-02-05

### Добавлено

- Инициализация проекта
- Базовая структура Telegram-бота на aiogram 3
- Конфигурация через pydantic-settings
