# Stage Response Contracts

Response contracts проверяют не только `items_count` и `trace_id`, но и то, **что реально увидит пользователь** в Telegram: текст ответа, наличие кнопки корзины, разбивку на чанки и отсутствие служебного мусора.

## Источник сценариев

Единый источник TC-кейсов:

- `src/vkuswill_bot/testing/response_contract_cases.py` — dataclass-модели `StageScenario`, `ResponseContract` и список `SCENARIOS`.
- `tests/stage_response_contract_cases.py` — compatibility wrapper для старых импортов; новые сценарии добавлять сюда не нужно.
- `src/vkuswill_bot/bot/telegram_delivery.py` — preview user-visible доставки: HTML-санитизация, вынос ссылки корзины в inline-кнопку, разбивка длинного текста.
- `tests/test_stage_response_contracts.py` — pytest-раннер против stage debug API + Langfuse.
- `scripts/run_live_response_contracts.py` — live runner против локального `ShoppingAgent` runtime с тем же набором TC-кейсов.

## Что проверяет контракт

Для каждого сценария можно задать:

- ожидаемый `diagnostics.prompt_profile`;
- наличие или отсутствие inline-кнопки корзины;
- максимальный объём ответа: символы, строки, Telegram-чанки;
- минимальное и максимальное число товаров;
- обязательные и запрещённые фразы в ответе;
- обязательные и запрещённые товары в `cart_snapshot` или в отрендеренном списке;
- отсутствие служебного мусора: `<tool_call>`, JSON-аргументов tool calls, имён MCP-инструментов;
- для pytest stage — подтверждение, что Langfuse trace относится к stage.

Статусы сценариев:

- `stable` — должен проходить;
- `known_issue` — известная продуктовая проблема. В pytest такие кейсы помечены `xfail`, а live runner возвращает `xfail` или `xpass`.

## Запуск против stage debug API

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

Stage runner вызывает:

1. `/debug/reset-history` для `scenario.user_id`;
2. `/debug/run-shopping` для каждого turn-а сценария;
3. Langfuse Public API `/api/public/traces/{trace_id}` для проверки stage trace.

## Локальный live runner

`scripts/run_live_response_contracts.py` нужен, когда нужно проверить те же TC-кейсы без stage debug API: например, внутри контейнера или на машине с production-like env. Он создаёт `ShoppingAgent` через `create_chat_engine()`, использует текущий prompt registry и MCP-настройки, затем применяет те же assertions.

Минимально нужны обычные переменные runtime:

- `BOT_TOKEN` может быть фиктивным, если Telegram не запускается;
- `LLM_API_KEY`;
- `LLM_MODEL`;
- `MCP_SERVER_URL`;
- `MCP_SERVER_API_KEY`, если целевой MCP требует ключ;
- опционально `LANGFUSE_*`, если нужно писать traces.

Примеры:

```bash
# Только stable-кейсы (режим по умолчанию)
uv run python scripts/run_live_response_contracts.py --verbose

# Один кейс
uv run python scripts/run_live_response_contracts.py --case TC-NLP-02 --verbose

# Все кейсы, включая known_issue, с JSON-отчётом
uv run python scripts/run_live_response_contracts.py \
  --status all \
  --report-json response-contract-report.json \
  --verbose
```

Exit code:

- `0` — нет `fail`;
- `1` — есть хотя бы один `fail`;
- `2` — фильтры не выбрали ни одного сценария.

## Как пользоваться notesforllm

Для этих проверок используй verification space из `NOTESFORLLM_VERIFICATION_SPACE_ID` и один task id:

- `task_id = vkuswill_bot:verification/response-contract-stage-tests`

### Старт сессии

В начале новой сессии восстанови контекст:

```text
notes_resume_context(
  space_id="<NOTESFORLLM_VERIFICATION_SPACE_ID>",
  task_id="vkuswill_bot:verification/response-contract-stage-tests"
)
```

### После прогона

После meaningful прогона сохрани checkpoint:

```text
notes_checkpoint_save(
  space_id="<NOTESFORLLM_VERIFICATION_SPACE_ID>",
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
  space_id="<NOTESFORLLM_VERIFICATION_SPACE_ID>",
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

Не добавляй отдельные копии сценариев в `tests/`: pytest и live runner должны расходиться только раннером, а не содержанием TC-кейсов.
