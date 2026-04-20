# Stage Response Contracts

Этот набор нужен, чтобы проверять не только `items_count` и `trace_id`, но и то, **что реально увидит пользователь** в Telegram: текст ответа, наличие кнопки корзины, разбивку на чанки и отсутствие служебного мусора.

Сценарии и контрактные правила переиспользуются между stage pytest и live runtime-раннером, чтобы проверки не расходились между окружениями.

## Что добавлено

- `src/vkuswill_bot/bot/telegram_delivery.py`
  Общая логика доставки ответа в Telegram:
  HTML-санитизация, вынос ссылки корзины в inline-кнопку, разбивка длинного текста.
- `src/vkuswill_bot/testing/response_contract_cases.py`
  Единый источник `TC-*` кейсов и контрактов ответа.
- `tests/stage_response_contract_cases.py`
  Совместимый wrapper-импорт для legacy пути (реэкспорт из `vkuswill_bot.testing.response_contract_cases`).
- `tests/test_stage_response_contracts.py`
  Интеграционный pytest-раннер для stage/debug API + Langfuse-проверки.
- `scripts/run_live_response_contracts.py`
  Раннер тех же `TC-*` сценариев против локального runtime `ShoppingAgent` (без stage API).

## Что проверяет контракт

Для каждого сценария проверяются:

- ожидаемый `diagnostics.prompt_profile`
- наличие или отсутствие inline-кнопки корзины
- максимальный объём ответа
- число Telegram-чанков
- обязательные и запрещённые фразы в ответе
- обязательные и запрещённые товары в `cart_snapshot`
- подтверждение, что trace реально относится к `stage`

Проверка Telegram-представления делается через `build_telegram_delivery_preview(...)` из `src/vkuswill_bot/bot/telegram_delivery.py` (тот же код, что используется при runtime-доставке ответа в Telegram).

## Как запускать

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

### Запуск live runtime-раннера (без stage API)

Если нужно проверить те же контракты в локальном runtime (тот же `ShoppingAgent`, тот же PromptRegistry), используй:

```bash
uv run python scripts/run_live_response_contracts.py --status stable --verbose
```

Полезные флаги:

- `--case TC-NLP-02` — выбрать конкретный кейс (можно повторять);
- `--max-scenarios 5` — ограничить число кейсов;
- `--timeout-seconds 120` — таймаут на turn;
- `--with-user-store` — подключить PostgreSQL `UserStore`, если задан `DATABASE_URL`;
- `--report-json /tmp/contracts.json` — сохранить JSON-отчёт.

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

Если продукт изменил поведение намеренно, сначала обнови контракт, потом зафиксируй это decision/checkpoint в `notesforllm`.
