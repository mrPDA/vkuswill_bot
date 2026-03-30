# Stage Response Contracts

Контракты response layer проверяют не только `items_count` и `trace_id`, но и то, **что реально увидит пользователь** в Telegram: итоговый текст, inline-кнопку корзины, разбиение на чанки и отсутствие служебного мусора.

## Покрываемые codepath

- `tests/test_stage_response_contracts.py`  
  Stage-раннер поверх `/debug/*` API + верификация trace в Langfuse.
- `scripts/run_live_response_contracts.py`  
  Локальный live-раннер, который поднимает `ShoppingAgent` в текущем runtime и прогоняет те же `TC-*` кейсы.
- `src/vkuswill_bot/testing/response_contract_cases.py`  
  Единый источник сценариев `SCENARIOS` и dataclass-контрактов.
- `tests/stage_response_contract_cases.py`  
  Совместимый импорт-обёртка для legacy путей в тестах.
- `src/vkuswill_bot/bot/telegram_delivery.py`  
  Боевая логика Telegram-доставки (чанки, кнопка корзины, очистка текста), на которую завязан контракт.

## Что проверяет контракт

Для каждого сценария валидируются:

- ожидаемый `diagnostics.prompt_profile`
- наличие или отсутствие inline-кнопки корзины
- лимиты по объёму ответа (`chars`, `lines`, `chunks`)
- обязательные и запрещённые фразы
- обязательные и запрещённые товары в `cart_snapshot` (или в финальном тексте как fallback)
- отсутствие служебных артефактов (`<tool_call>`, JSON tool payload и т.д.)
- для stage-режима: подтверждение, что trace относится к `stage`

## Режимы запуска

| Режим | Когда использовать | Точка входа |
|---|---|---|
| Stage contract run | Проверка реального stage окружения и trace provenance | `tests/test_stage_response_contracts.py` |
| Live local contract run | Быстрый регресс на текущем коде/промптах без обращения к stage | `scripts/run_live_response_contracts.py` |

### 1) Stage contract run (через debug API)

Подготовь окружение:

```bash
set -a
source .env
set +a
export RUN_STAGE_RESPONSE_CONTRACTS=1
export STAGE_BASE_URL="https://89.169.138.16"
export STAGE_VERIFY_SSL=0
```

Обязательные переменные:

- `DEBUG_API_KEY_STG`
- `LANGFUSE_HOST`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`

`STAGE_VERIFY_SSL=0` подходит для self-signed TLS на debug API. После перевода stage на доверенный сертификат включи `STAGE_VERIFY_SSL=1`.

Запуск полного набора:

```bash
uv run pytest tests/test_stage_response_contracts.py -m stage -rs
```

Запуск одного кейса:

```bash
uv run pytest tests/test_stage_response_contracts.py -m stage -k "TC-NLP-02" -rs
```

### 2) Live local contract run (без stage)

Запуск stable кейсов:

```bash
uv run python scripts/run_live_response_contracts.py --status stable --verbose
```

Точечный запуск:

```bash
uv run python scripts/run_live_response_contracts.py --case TC-NLP-02 --case TC-MULTI-01
```

Сохранить JSON-отчёт:

```bash
uv run python scripts/run_live_response_contracts.py --status all --report-json reports/response-contracts.json
```

Ключевые флаги:

- `--status stable|known_issue|all`
- `--case TC-...` (можно несколько раз)
- `--max-scenarios N`
- `--timeout-seconds N`
- `--with-user-store` (подключить PostgreSQL `UserStore`, если нужен runtime с лимитами/логированием)

Выходной код live-раннера:

- `0` — нет `fail`
- `1` — есть хотя бы один `fail`
- `2` — после фильтрации не осталось сценариев

`known_issue` сценарии возвращают `xfail`/`xpass` и не должны ломать pipeline как обычные регрессы.

## Частые проблемы и ограничения

- Stage-тесты намеренно скипаются, если запуск неявный (нужно явно вызывать `-m stage` или файл `test_stage_response_contracts.py`).
- Для live-раннера должен быть доступен тот же runtime-конфиг, что и для `ShoppingAgent` (LLM/MCP/env); иначе будут ложные `fail`.
- `tests/stage_response_contract_cases.py` не хранит собственные кейсы, а только реэкспортирует `SCENARIOS` из `src/vkuswill_bot/testing/response_contract_cases.py`. Обновлять нужно shared-модуль.

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
