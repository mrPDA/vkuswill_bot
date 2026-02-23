# Phase 0 Baseline (Variant E)

Дата фиксации (UTC): `2026-02-21T22:02:23Z`  
Окно baseline: последние 24 часа.

## 1. Freeze интерфейса

Зафиксирован контракт chat engine:

- `/Users/denispukinov/Downloads/vkuswill_bot/src/vkuswill_bot/services/chat_engine.py`
  - `process_message(user_id, text, on_progress=None) -> str`
  - `reset_conversation(user_id) -> None`
  - `close() -> None`
  - `get_last_cart_snapshot(user_id) -> dict | None`

Назначение: единый DI-контракт для `handlers` и `voice_link_api` перед внедрением `ShoppingAgent`.

## 2. Smoke-сценарии baseline (эталон)

Сценарии, которые должны проходить в legacy и в новом контуре:

1. `паста карбонара на двоих`
2. `молоко без лактозы`
3. `собери завтрак`
4. `КБЖУ у банана`
5. Voice-flow: `start -> status -> ссылка на корзину`
6. Voice-link flow: `link code -> заказ -> проверка статуса`

## 3. Метрики baseline (24h)

### 3.1. Продуктовые агрегаты (PostgreSQL: `daily_stats`, `user_events`)

- sessions: `4`
- errors: `0`
- carts_created: `14`
- searches: `73`
- cart_limits_hit: `8`
- error_rate: `0.00%`

Топ событий за 24h:

1. `product_search`: `73`
2. `cart_created`: `14`
3. `cart_limit_reached`: `8`
4. `session_start`: `4`
5. `voice_link_code_issued`: `3`
6. `bonus_carts_granted`: `1`
7. `survey_completed`: `1`

### 3.2. Технические метрики (Langfuse API)

Для trace `name=chat` (Telegram legacy):

- traces (24h): `30`
- p95 latency: `22.269s` (`22269.0ms`)
- tool calls: `100`
- tool failures: `8`
- tool_call_failure_rate: `8.0%`

Примечание:

1. `tool_call_failure_rate` рассчитан эвристически по `tool:*` span:
   - `level=ERROR` или `statusMessage` непустой,
   - либо `output` содержит `{"ok": false}` / `error`.
2. Это baseline для относительного сравнения после включения `shopping_agent`.

## 4. Критерий завершения Phase 0

1. Контракт chat engine зафиксирован в коде.
2. Базовые smoke-сценарии зафиксированы.
3. Baseline-метрики сняты и сохранены для последующего сравнения rollout.
