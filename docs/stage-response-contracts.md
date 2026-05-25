# Stage Response Contracts

Этот набор нужен, чтобы проверять не только `items_count` и `trace_id`, но и то, **что реально увидит пользователь** в Telegram: текст ответа, наличие кнопки корзины, разбивку на чанки и отсутствие служебного мусора.

## Основные файлы

- `src/vkuswill_bot/bot/telegram_delivery.py`:
  Общая логика доставки ответа в Telegram:
  HTML-санитизация, вынос ссылки корзины в inline-кнопку, разбивка длинного текста.
- `src/vkuswill_bot/testing/response_contract_cases.py`:
  Канонический источник исполняемых `TC-*` сценариев и контрактов. Его используют
  stage pytest и live runner.
- `tests/stage_response_contract_cases.py`:
  Compatibility wrapper для старых импортов; новые кейсы добавляй в
  `src/vkuswill_bot/testing/response_contract_cases.py`.
- `tests/test_stage_response_contracts.py`:
  Интеграционный pytest-раннер для stage/debug API + Langfuse-проверки.
- `scripts/run_live_response_contracts.py`:
  Локальный live runner: запускает те же `TC-*` через текущий `ShoppingAgent`
  runtime без stage debug API.

## Что проверяет контракт

Для каждого сценария проверяются:

- ожидаемый `diagnostics.prompt_profile`
- наличие или отсутствие inline-кнопки корзины
- максимальный объём ответа
- число Telegram-чанков
- обязательные и запрещённые фразы в ответе
- обязательные и запрещённые товары в `cart_snapshot`
- подтверждение, что trace реально относится к `stage`

## Stage pytest runner

Подготовь переменные окружения:

```bash
set -a
source .env
set +a
export RUN_STAGE_RESPONSE_CONTRACTS=1
export STAGE_BASE_URL="https://89.169.138.16"
export STAGE_VERIFY_SSL=0
```

Нужны:

- `DEBUG_API_KEY_STG`
- `LANGFUSE_HOST`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`

`STAGE_VERIFY_SSL=0` по умолчанию подходит для текущего stage с self-signed TLS на debug API. Если сертификат станет доверенным, можно включить строгую проверку через `STAGE_VERIFY_SSL=1`.

Запуск:

```bash
uv run pytest tests/test_stage_response_contracts.py -m stage -rs
```

Если нужно запустить только один кейс:

```bash
uv run pytest tests/test_stage_response_contracts.py -m stage -k "TC-NLP-02" -rs
```

`stable`-кейсы должны проходить. Кейсы со статусом `known_issue` помечены как `xfail`: они нужны, чтобы известные проблемы не потерялись и автоматически стали заметны, когда неожиданно починятся.

## Local live runner

Live runner нужен для проверки текущего локального runtime до деплоя или на Pi,
когда stage debug API недоступен. Он собирает `ShoppingAgent` через
`create_chat_engine()`, использует тот же prompt registry и применяет те же
контракты из `src/vkuswill_bot/testing/response_contract_cases.py`.

Минимальный запуск stable-кейсов:

```bash
uv run python scripts/run_live_response_contracts.py --status stable
```

Один кейс с подробным выводом:

```bash
uv run python scripts/run_live_response_contracts.py \
  --case TC-PERSONA-01 \
  --verbose
```

Ограниченный smoke и JSON-отчёт:

```bash
uv run python scripts/run_live_response_contracts.py \
  --status stable \
  --max-scenarios 3 \
  --report-json response-contract-report.json
```

Ограничения:

- runner вызывает настоящий LLM и MCP, поэтому нужны валидные `LLM_API_KEY`,
  `LLM_MODEL`, `MCP_SERVER_URL` и, если MCP защищён, `MCP_SERVER_API_KEY`;
- `--with-user-store` подключает PostgreSQL только когда задан `DATABASE_URL`;
- `--report-json` пишет файл напрямую и не создаёт родительские директории;
- `known_issue` не падает как pytest `xfail`: outcome будет `xfail` или `xpass`
  в JSON summary.

## Как выбирать кейсы

Сценарии сгруппированы по `case_id`:

| Префикс | Что покрывает | Примеры важных проверок |
|---------|---------------|--------------------------|
| `TC-QTY-*` | Количества и единицы в корзине | Нормализация `q`, дробные кг/л, разговорные числительные |
| `TC-MULTI-*` | Многоходовые корзины | Замены, удаления, итоговый снимок корзины |
| `TC-NLP-*` и `TC-RECIPE-*` | Рецепты и намерения | Рецепт vs обычная корзина, отсутствие лишнего meal-plan |
| `TC-PERSONA-*` | Персоны, ограничения и мероприятия | `meal_plan`, allergy/diet constraints, большие группы |
| `TC-VOICE-*` и `TC-LANG-*` | Голосовые/транслит/английские запросы | Предобработка текста до поиска и корзины |

Для meal-plan регрессий смотри `TC-PERSONA-01..03`: они проверяют
`diagnostics.prompt_profile == "meal_plan"`, ограничения по аллергенам/бюджету и
размер ответа. Для cart quantity regressions смотри `TC-QTY-*`: часть кейсов
остаётся `known_issue`, чтобы нестабильные конвертации не исчезали из регулярных
прогонов.

## Pre-flight перед stage/PR

Перед деплоем или PR, который меняет debug API, response contracts, prompts,
cart/meal-plan routing или примеры с секретами, выполни:

```bash
make secret-scan
make lint
uv run pytest tests/test_stage_response_contracts.py -m stage -k "TC-PERSONA-01" -rs
```

Если нужно проверить всю git-историю на секреты, используй:

```bash
make secret-scan-history
```

CI job `Security checks` устанавливает gitleaks v8.30.0 и запускает scan с
`.gitleaks.toml`. Новые ложноположительные срабатывания добавляй в allowlist
только точечно: по конкретному пути/placeholder regex, без исключения всего
документа или директории.

## Как пользоваться notesforllm

Для этих stage-проверок используй verification space:

- `space_id = 7bf6a253-8a7a-4137-ada9-643a5e54f961`
- `task_id = vkuswill_bot:verification/response-contract-stage-tests`

### Старт сессии

В начале новой сессии восстанови контекст:

```text
notes_resume_context(
  space_id="7bf6a253-8a7a-4137-ada9-643a5e54f961",
  task_id="vkuswill_bot:verification/response-contract-stage-tests"
)
```

### После прогона

После meaningful прогона сохрани checkpoint:

```text
notes_checkpoint_save(
  space_id="7bf6a253-8a7a-4137-ada9-643a5e54f961",
  title="Stage response contracts: smoke run",
  summary="Executed stage response-contract suite against debug API.",
  task_id="vkuswill_bot:verification/response-contract-stage-tests",
  agent_id="testing-agent",
  status="in_progress",
  findings=[
    "Stable cases passed: ...",
    "Known issues xfailed: ..."
  ],
  next_steps=[
    "Inspect failed stable cases",
    "Update contracts after behavior changes"
  ]
)
```

### Если принято новое правило

Когда меняется сам подход к валидации, сохрани decision:

```text
notes_decision_save(
  space_id="7bf6a253-8a7a-4137-ada9-643a5e54f961",
  title="Decision: update response contract rules",
  decision="...",
  why="...",
  task_id="vkuswill_bot:verification/response-contract-stage-tests",
  agent_id="testing-agent"
)
```

### Завершение

- Если работа завершена: финальный `notes_checkpoint_save(..., status="done")`
- Если работа не завершена: `notes_handoff_save(...)` с первым следующим шагом

## Практика обновления кейсов

Обновляй `src/vkuswill_bot/testing/response_contract_cases.py`, когда меняется одно из:

- ожидаемый `profile`
- допустимый объём ответа
- обязательные фразы
- список обязательных или запрещённых товаров
- статус кейса: `stable` или `known_issue`

`tests/stage_response_contract_cases.py` оставлен только как compatibility
wrapper; не добавляй туда новые сценарии.

Если продукт изменил поведение намеренно, сначала обнови контракт, потом зафиксируй это decision/checkpoint в `notesforllm`.
