# Отладка ошибок бота через Langfuse API

> **Дата создания:** 14.02.2026  
> **Обновлено:** 20.03.2026 — актуализация под Qwen/Yandex OpenAI-compatible, Raspberry Pi, self-hosted Langfuse.
>
> **Автор:** Бен Ганн (doc-writer)

---

## Общая информация

Langfuse — система LLM-observability: трейсы взаимодействий бота с **OpenAI-compatible** провайдером (в production чаще **Yandex Cloud AI Studio / Qwen** по `LLM_MODEL`; текущие переменные см. в `.env.example`). Каждое сообщение пользователя создаёт **trace**, каждый вызов LLM — **generation**, каждый вызов инструмента — **span**.

### Структура трейса (shopping_agent / Telegram)

```
Trace (chat)
├── input: текст пользователя (с маскировкой PII при LANGFUSE_ANONYMIZE_MESSAGES)
├── user_id: анонимизированный (SHA-256, 12 символов)
├── session_id: анонимизированный
├── tags: например ["env:prod", "shopping_agent", "telegram_or_voice", "qwen_openai", "profile:general"]
│
├── Generation (shopping-agent-1, shopping-agent-2, …)
│   ├── model: URI/имя из конфига (например gpt://…/qwen…/latest)
│   ├── input: messages в формате Langfuse
│   ├── model_parameters: tools, step, provider, prompt_profile, temperature, max_tokens, …
│   ├── metadata: prompt (provenance)
│   ├── output: content или tool_calls
│   └── usage_details, cost_details (если провайдер/SDK отдают)
│
├── Span (tool:tool_name) — для каждого tool call
│   ├── input: распарсенные аргументы инструмента
│   ├── metadata: call_number, full_length
│   └── output: результат (усечённый)
│
└── trace.update(): output (финальный ответ), metadata (total_steps, tool_calls, error)
```

---

## Подключение к API

### Авторизация

Langfuse API использует **Basic Auth** с ключами проекта:

| Параметр | Переменная окружения | Пример |
|----------|---------------------|--------|
| Login | `LANGFUSE_PUBLIC_KEY` | `pk-lf-01b3d85e-...` |
| Password | `LANGFUSE_SECRET_KEY` | `sk-lf-e0fcf694-...` |

В curl авторизация передаётся через `-u`:

```bash
curl -u "pk-lf-...:sk-lf-..." "http://<LANGFUSE_HOST>/api/public/..."
```

### Хост

Подставьте в примерах `curl` свой **`LANGFUSE_HOST`** (как доходит **с той машины, где выполняется запрос**):

| Окружение | URL для API/UI из браузера или хоста |
|-----------|--------------------------------------|
| Raspberry Pi (LAN) | `http://<IP_малинки>:3000` (порт `LANGFUSE_PORT`, по умолчанию 3000) |
| Docker Compose (ПК, локально) | `http://localhost:3000` |
| Legacy VM (если ещё используется) | свой фиксированный URL, например `http://<host>:3000` |
| SaaS | `https://cloud.langfuse.com` |

**Контейнер бота** в `docker-compose.pi.yml` ходит в Langfuse по **`http://langfuse:3000`** (имя сервиса в сети compose), не по LAN-IP.

---

## Полезные API-эндпоинты

| Метод | Эндпоинт | Описание |
|-------|----------|----------|
| GET | `/api/public/traces` | Список трейсов (с фильтрацией) |
| GET | `/api/public/traces/{traceId}` | Конкретный трейс |
| GET | `/api/public/observations?traceId={traceId}` | Все наблюдения (generations + spans) трейса |
| GET | `/api/public/observations/{observationId}` | Конкретное наблюдение |
| GET | `/api/public/sessions` | Список сессий |
| GET | `/api/public/models` | Зарегистрированные модели |

---

## Просмотр ошибок конкретного трейса

### Шаг 1: Получить traceId

TraceId можно найти:
- В **логах бота** (JSON-формат, поле `trace_id`)
- В **Langfuse UI** → Traces → нажать на нужный трейс → скопировать ID из URL
- В **Langfuse UI** → фильтр по `tags`, `user_id`, дате

### Шаг 2: Запросить все observations трейса

```bash
curl -s \
  --connect-timeout 5 \
  --max-time 15 \
  -u "pk-lf-YOUR_PUBLIC_KEY:sk-lf-YOUR_SECRET_KEY" \
  "http://HOST:3000/api/public/observations?traceId=TRACE_ID&limit=30"
```

### Шаг 3: Разобрать ответ

Ответ содержит массив `data` с observations, отсортированными по `startTime`. Каждое наблюдение имеет:

| Поле | Описание |
|------|----------|
| `type` | `GENERATION` (LLM-вызов) или `SPAN` (tool call) |
| `name` | Имя: `shopping-agent-1`, `classify-intent`, `tool:vkusvill_products_search`, … |
| `input` | Входные данные |
| `output` | Результат (для spans — ответ инструмента) |
| `metadata` | Доп. информация: `step`, `call_number`, `full_length` |
| `level` | `DEFAULT`, `WARNING`, `ERROR` |
| `statusMessage` | Сообщение об ошибке (если есть) |
| `startTime` / `endTime` | Временные метки |

---

## Примеры команд

### Найти конкретный tool call (например, корзина)

Пример — запрос observations трейса и поиск span инструмента с `cart` в имени (подставьте **свои** ключи и `HOST`):

```bash
curl -s \
  --connect-timeout 5 \
  --max-time 15 \
  -u "pk-lf-PUBLIC:sk-lf-SECRET" \
  "http://HOST:3000/api/public/observations?traceId=TRACE_ID&limit=30" \
  2>&1 | python3 -c "
import json, sys
data = json.load(sys.stdin)
obs = sorted(data.get('data', []), key=lambda x: x.get('startTime', ''))
for o in obs:
    name = o.get('name', '')
    if 'cart' in name.lower() and o.get('type') == 'SPAN':
        output = o.get('output', '')
        meta = o.get('metadata', {})
        print(f'name: {name}')
        print(f'full_length: {meta.get(\"full_length\")}')
        if isinstance(output, str):
            print(f'OUTPUT ({len(output)} chars):')
            print(output)
        print()
"
```

### Показать все observations трейса (хронологически)

```bash
curl -s \
  -u "pk-lf-PUBLIC:sk-lf-SECRET" \
  "http://HOST:3000/api/public/observations?traceId=TRACE_ID&limit=50" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
obs = sorted(data.get('data', []), key=lambda x: x.get('startTime', ''))
for o in obs:
    t = o.get('type', '?')
    name = o.get('name', '?')
    level = o.get('level', 'DEFAULT')
    status = o.get('statusMessage', '')
    marker = ' !!!' if level in ('WARNING', 'ERROR') else ''
    print(f'[{t:10}] {name:30} level={level}{marker}')
    if status:
        print(f'            statusMessage: {status}')
"
```

### Найти трейсы с ошибками за последний час

```bash
curl -s \
  -u "pk-lf-PUBLIC:sk-lf-SECRET" \
  "http://HOST:3000/api/public/traces?limit=20&orderBy=timestamp.desc" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('data', []):
    meta = t.get('metadata', {}) or {}
    if meta.get('error'):
        print(f'trace_id: {t[\"id\"]}')
        print(f'  input: {str(t.get(\"input\", \"\"))[:80]}')
        print(f'  error: {meta[\"error\"]}')
        print()
"
```

### Получить информацию о конкретном трейсе

```bash
curl -s \
  -u "pk-lf-PUBLIC:sk-lf-SECRET" \
  "http://HOST:3000/api/public/traces/TRACE_ID" \
  | python3 -m json.tool
```

### Посмотреть usage и стоимость generations

```bash
curl -s \
  -u "pk-lf-PUBLIC:sk-lf-SECRET" \
  "http://HOST:3000/api/public/observations?traceId=TRACE_ID&limit=30" \
  | python3 -c "
import json, sys
data = json.load(sys.stdin)
for o in sorted(data.get('data', []), key=lambda x: x.get('startTime', '')):
    if o.get('type') != 'GENERATION':
        continue
    name = o.get('name', '?')
    usage = o.get('usageDetails', {}) or {}
    cost = o.get('costDetails', {}) or {}
    print(f'{name}: input={usage.get(\"input\",0)} output={usage.get(\"output\",0)} total={usage.get(\"total\",0)} tokens')
    print(f'  cost: {cost}')
"
```

---

## Langfuse UI

Помимо API, можно использовать веб-интерфейс Langfuse:

| Что | Где в UI |
|-----|----------|
| Все трейсы | Traces → список с фильтрацией по дате, тегам, пользователю |
| Детали трейса | Traces → нажать на трейс → таймлайн generations и spans |
| Ошибки | Traces → фильтр `level = ERROR` |
| Стоимость | Dashboard → Cost по дням и моделям |
| Sessions | Sessions → группировка трейсов по сессии пользователя |

**URL UI:** для Pi — `http://<IP_малинки>:3000`; на ПК с compose — `http://localhost:3000`; для облака — `https://cloud.langfuse.com` (проект в аккаунте).

---

## Переменные окружения

Все переменные настраиваются в `.env` или через секреты (Yandex Lockbox):

| Переменная | Описание | Значение по умолчанию |
|-----------|----------|----------------------|
| `LANGFUSE_ENABLED` | Включить трейсинг | `false` |
| `LANGFUSE_PUBLIC_KEY` | Публичный ключ проекта | `""` |
| `LANGFUSE_SECRET_KEY` | Секретный ключ проекта | `""` |
| `LANGFUSE_HOST` | URL Langfuse для SDK (бот) | В коде по умолчанию `https://cloud.langfuse.com`; на Pi в compose переопределяется на `http://langfuse:3000` |
| `LANGFUSE_ANONYMIZE_MESSAGES` | Скрывать текст сообщений (152-ФЗ) | `false` в `.env.example`; на Pi часто `true` |
| `PROMPT_LABEL` | Метка промптов в Langfuse Prompt Management | `production` |
| `LANGFUSE_NEXTAUTH_URL` | URL для NextAuth (только self-hosted) | Должен совпадать с тем, как открывают UI в браузере |
| `LANGFUSE_NEXTAUTH_SECRET` / `LANGFUSE_SALT` | Секреты NextAuth / hashing (self-hosted) | Задать на Pi (`openssl rand -hex …`) |

---

## Советы по отладке

1. **Начинай с trace** — найди traceId в логах или UI, затем запроси observations
2. **Сортируй по времени** — observations приходят неупорядоченными, сортируй по `startTime`
3. **Ищи `level=ERROR`** — ошибки помечаются уровнем ERROR в observations
4. **Проверяй `metadata.error`** — в trace.metadata ошибки записываются отдельно
5. **`full_length` в spans** — показывает полную длину ответа инструмента (до усечения)
6. **`statusMessage`** — содержит текст ошибки для проблемных observations

---

## Raspberry Pi (docker-compose.pi.yml)

На Pi в compose поднимается **self-hosted Langfuse** (`langfuse/langfuse:2`) и отдельная БД `langfuse` в том же PostgreSQL (подготовка через одноразовый сервис `langfuse-db-prep`).

1. В `.env` задайте `LANGFUSE_NEXTAUTH_URL` так, как открываете UI в браузере (например `http://192.168.0.151:3000`), и сгенерируйте `LANGFUSE_NEXTAUTH_SECRET` / `LANGFUSE_SALT` (`openssl rand -hex 32` и `openssl rand -hex 16`).
2. `docker compose -f docker-compose.pi.yml up -d --build` — дождитесь healthy у postgres и запуска Langfuse.
3. Откройте UI на порту `LANGFUSE_PORT` (по умолчанию 3000), зарегистрируйте организацию, создайте проект, скопируйте **Public / Secret key** в `.env` как `LANGFUSE_PUBLIC_KEY` и `LANGFUSE_SECRET_KEY`, включите `LANGFUSE_ENABLED=true`, `PROMPT_LABEL=production`, перезапустите бота.
4. Загрузите промпты в Langfuse из репозитория:
   - из экспорта JSON: `make docker-pi-langfuse-import-prompts` или  
     `docker compose -f docker-compose.pi.yml exec -T bot python /app/scripts/import_prompts_from_langfuse_export.py --label production`;
   - добить из текстовых файлов (каталог `prompts/` смонтирован в бот):  
     `docker compose -f docker-compose.pi.yml exec -T bot python /app/scripts/migrate_prompts_to_langfuse.py --label production`  
     (в образе уже есть `prompts/langfuse-export/*.production.json` и `profile_meal_plan.txt`).

Бот обращается к Langfuse по **внутреннему** URL `http://langfuse:3000`; переменная `LANGFUSE_HOST` в контейнере бота задаётся в compose и не должна указывать на облако при использовании локального сервиса.
