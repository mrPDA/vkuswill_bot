# Stage Response Contracts

Контракты проверяют не только `items_count` и `trace_id`, а пользовательский результат в Telegram:
текст, кнопку корзины, разбиение на чанки и отсутствие служебного мусора.

## Что покрывается

### Источник сценариев (single source of truth)

- `src/vkuswill_bot/testing/response_contract_cases.py` - все кейсы `TC-*` и contract-правила.
- `tests/stage_response_contract_cases.py` - совместимый wrapper для старых импортов.

### Раннеры

- `tests/test_stage_response_contracts.py` - stage/debug API + проверка trace в Langfuse.
- `scripts/run_live_response_contracts.py` - прогон тех же контрактов на локальном live runtime `ShoppingAgent`.

### Что валидируется по кейсу

- `diagnostics.prompt_profile` (если задан в контракте);
- есть/нет inline-кнопки корзины;
- лимиты на размер ответа (`max_chars_total`, `max_lines_total`, `max_chunks`);
- обязательные/запрещённые фразы;
- обязательные/запрещённые товары в снимке корзины;
- корректная принадлежность trace к stage (для stage-раннера).

## Режим 1: Stage (через pytest)

Подготовка:

```bash
set -a
source .env
set +a
export RUN_STAGE_RESPONSE_CONTRACTS=1
export STAGE_BASE_URL=https://89.169.138.16
export STAGE_VERIFY_SSL=0
```

Обязательные переменные:

- `DEBUG_API_KEY_STG`
- `LANGFUSE_HOST`
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`

Запуск:

```bash
uv run pytest tests/test_stage_response_contracts.py -m stage -rs
```

Один кейс:

```bash
uv run pytest tests/test_stage_response_contracts.py -m stage -k TC-NLP-02 -rs
```

Примечание по TLS: `STAGE_VERIFY_SSL=0` используется для stage с self-signed сертификатом. При доверенном сертификате переключи на `STAGE_VERIFY_SSL=1`.

## Режим 2: Live runtime (локально/в контейнере)

`scripts/run_live_response_contracts.py` запускает те же `TC-*` кейсы против локального `create_chat_engine(...)` и печатает JSON summary.

Базовые команды:

```bash
uv run python scripts/run_live_response_contracts.py --status stable
uv run python scripts/run_live_response_contracts.py --status all --max-scenarios 10
uv run python scripts/run_live_response_contracts.py --case TC-QTY-05 --verbose
uv run python scripts/run_live_response_contracts.py --status stable --report-json /tmp/live-contracts.json
```

Полезные флаги:

- `--status stable|known_issue|all`
- `--case TC-...` (можно несколько раз)
- `--timeout-seconds 120`
- `--with-user-store` (подключить PostgreSQL UserStore при наличии `DATABASE_URL`)

## Интерпретация результата

В summary есть четыре статуса:

- `pass` - stable-кейс прошёл;
- `fail` - stable-кейс упал (требует реакции);
- `xfail` - known_issue ожидаемо упал;
- `xpass` - known_issue неожиданно прошёл (сигнал обновить статус кейса).

`known_issue` кейсы намеренно оставляются в прогоне, чтобы регрессии и неожиданные починки были видимы.

## Типовые проблемы и быстрые проверки

1. **Все кейсы skipped в stage**
   - проверь `RUN_STAGE_RESPONSE_CONTRACTS=1`;
   - убедись, что запуск явно включает `-m stage` или файл `test_stage_response_contracts.py`.

2. **Ошибка trace-проверки**
   - проверь `LANGFUSE_*` переменные;
   - убедись, что stage пишет нужные tags/metadata (см. `_is_stage_trace(...)` в тесте).

3. **Разъезд stage vs live**
   - сначала прогоняй `--status stable` локально;
   - затем stage-пакет, чтобы отделить runtime-проблемы от окружения.

4. **Контракт стабильно падает после ожидаемого продуктового изменения**
   - обнови сценарий в `src/vkuswill_bot/testing/response_contract_cases.py`;
   - если баг действительно устранён, переведи кейс из `known_issue` в `stable`.

## Как пользоваться notesforllm

Для этой верификации используй:

- `space_id = 7bf6a253-8a7a-4137-ada9-643a5e54f961`
- `task_id = vkuswill_bot:verification/response-contract-stage-tests`

### Старт сессии

```text
notes_resume_context(
  space_id="7bf6a253-8a7a-4137-ada9-643a5e54f961",
  task_id="vkuswill_bot:verification/response-contract-stage-tests"
)
```

### После meaningful прогона

```text
notes_checkpoint_save(
  space_id="7bf6a253-8a7a-4137-ada9-643a5e54f961",
  title="Stage response contracts: smoke run",
  summary="Executed response-contract suite (stage/live).",
  task_id="vkuswill_bot:verification/response-contract-stage-tests",
  agent_id="testing-agent",
  status="in_progress",
  findings=[
    "Stable cases passed: ...",
    "Known issues xfailed: ..."
  ],
  next_steps=[
    "Inspect failed stable cases",
    "Update contract statuses after fixes"
  ]
)
```

### Когда меняются правила валидации

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
