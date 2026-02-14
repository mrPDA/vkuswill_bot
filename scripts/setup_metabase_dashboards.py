#!/usr/bin/env python3
"""
Автоматическая настройка дашбордов Metabase для VkusVill Bot.

Создаёт три дашборда:
1. Обзор за день — ключевые метрики из daily_stats
2. Воронка конверсий — по user_events
3. Источники трафика — по bot_start events

Использование:
    python scripts/setup_metabase_dashboards.py \
        --url http://YOUR_SERVER_IP:3001 \
        --email admin@example.com \
        --password secret
"""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ─── Metabase API helpers ──────────────────────────────────────────

METABASE_URL = ""
SESSION_TOKEN = ""


def api(method: str, path: str, data: dict | None = None) -> dict | list:
    """Вызов Metabase API."""
    url = f"{METABASE_URL}/api/{path}"
    body = json.dumps(data).encode() if data else None
    headers = {"Content-Type": "application/json"}
    if SESSION_TOKEN:
        headers["X-Metabase-Session"] = SESSION_TOKEN

    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        print(f"  API error {e.code}: {path} → {body_text[:200]}")
        raise


def login(email: str, password: str) -> str:
    """Получить session token."""
    result = api("POST", "session", {"username": email, "password": password})
    return result["id"]


def get_database_id(db_name: str = "vkuswill") -> int | None:
    """Найти ID базы данных по имени или по таблице daily_stats."""
    databases = api("GET", "database")
    for db in databases.get("data", databases if isinstance(databases, list) else []):
        name = db.get("name", "").lower()
        if db_name.lower() in name or "vkusvill" in name or "bot" in name:
            return db["id"]
    # Fallback: найти БД с таблицей daily_stats
    for db in databases.get("data", databases if isinstance(databases, list) else []):
        return db["id"]  # первая пользовательская БД
    return None


def create_card(name: str, sql: str, db_id: int, display: str = "table",
                visualization: dict | None = None,
                collection_id: int | None = None) -> int:
    """Создать saved question (карточку)."""
    payload: dict = {
        "name": name,
        "dataset_query": {
            "type": "native",
            "native": {"query": sql},
            "database": db_id,
        },
        "display": display,
        "visualization_settings": visualization or {},
    }
    if collection_id:
        payload["collection_id"] = collection_id

    result = api("POST", "card", payload)
    return result["id"]


def create_dashboard(name: str, description: str = "",
                     collection_id: int | None = None) -> int:
    """Создать дашборд."""
    payload: dict = {"name": name, "description": description}
    if collection_id:
        payload["collection_id"] = collection_id
    result = api("POST", "dashboard", payload)
    return result["id"]


def add_card_to_dashboard(dashboard_id: int, card_id: int,
                          row: int, col: int,
                          size_x: int = 6, size_y: int = 4) -> None:
    """Добавить карточку на дашборд."""
    # Получить текущие карточки
    dash = api("GET", f"dashboard/{dashboard_id}")
    cards = dash.get("dashcards", dash.get("ordered_cards", []))
    new_card = {
        "id": -1,
        "card_id": card_id,
        "row": row,
        "col": col,
        "size_x": size_x,
        "size_y": size_y,
    }
    cards.append(new_card)
    api("PUT", f"dashboard/{dashboard_id}", {"dashcards": cards})


def create_collection(name: str) -> int:
    """Создать коллекцию (папку) для дашбордов."""
    result = api("POST", "collection", {"name": name, "color": "#509EE3"})
    return result["id"]


# ─── SQL-запросы для карточек ──────────────────────────────────────

CARDS_OVERVIEW = [
    {
        "name": "DAU (сегодня)",
        "display": "scalar",
        "sql": """
SELECT dau FROM daily_stats
WHERE date = CURRENT_DATE
""",
        "pos": (0, 0, 4, 3),
    },
    {
        "name": "Новые пользователи (сегодня)",
        "display": "scalar",
        "sql": """
SELECT new_users FROM daily_stats
WHERE date = CURRENT_DATE
""",
        "pos": (4, 0, 4, 3),
    },
    {
        "name": "Корзины (сегодня)",
        "display": "scalar",
        "sql": """
SELECT carts_created FROM daily_stats
WHERE date = CURRENT_DATE
""",
        "pos": (8, 0, 4, 3),
    },
    {
        "name": "GMV (сегодня)",
        "display": "scalar",
        "sql": """
SELECT total_gmv FROM daily_stats
WHERE date = CURRENT_DATE
""",
        "pos": (12, 0, 6, 3),
        "viz": {"prefix": "₽"},
    },
    {
        "name": "DAU — тренд 30 дней",
        "display": "line",
        "sql": """
SELECT date, dau, new_users
FROM daily_stats
WHERE date >= CURRENT_DATE - 30
ORDER BY date
""",
        "pos": (0, 3, 9, 5),
    },
    {
        "name": "Корзины и GMV — тренд 30 дней",
        "display": "bar",
        "sql": """
SELECT date, carts_created, total_gmv
FROM daily_stats
WHERE date >= CURRENT_DATE - 30
ORDER BY date
""",
        "pos": (9, 3, 9, 5),
    },
    {
        "name": "Средний чек — тренд 30 дней",
        "display": "line",
        "sql": """
SELECT date, avg_cart_value
FROM daily_stats
WHERE date >= CURRENT_DATE - 30
ORDER BY date
""",
        "pos": (0, 8, 9, 5),
    },
    {
        "name": "Сессии, поиски, ошибки — тренд 30 дней",
        "display": "line",
        "sql": """
SELECT date, sessions, searches, errors
FROM daily_stats
WHERE date >= CURRENT_DATE - 30
ORDER BY date
""",
        "pos": (9, 8, 9, 5),
    },
    {
        "name": "Все метрики (таблица)",
        "display": "table",
        "sql": """
SELECT date AS "Дата",
       dau AS "DAU",
       new_users AS "Новые",
       sessions AS "Сессии",
       carts_created AS "Корзины",
       total_gmv AS "GMV ₽",
       avg_cart_value AS "Ср. чек ₽",
       searches AS "Поиски",
       errors AS "Ошибки",
       cart_limits_hit AS "Лимиты",
       surveys_completed AS "Опросы"
FROM daily_stats
ORDER BY date DESC
LIMIT 30
""",
        "pos": (0, 13, 18, 6),
    },
]

CARDS_FUNNEL = [
    {
        "name": "Воронка конверсий (30 дней)",
        "display": "bar",
        "sql": """
SELECT stage, users FROM (
  SELECT 1 AS sort, 'Старт бота' AS stage,
         COUNT(DISTINCT user_id) AS users
  FROM user_events
  WHERE event_type = 'bot_start'
    AND created_at >= CURRENT_DATE - 30

  UNION ALL

  SELECT 2, 'Сессия',
         COUNT(DISTINCT user_id)
  FROM user_events
  WHERE event_type = 'session_start'
    AND created_at >= CURRENT_DATE - 30

  UNION ALL

  SELECT 3, 'Поиск товара',
         COUNT(DISTINCT user_id)
  FROM user_events
  WHERE event_type = 'product_search'
    AND created_at >= CURRENT_DATE - 30

  UNION ALL

  SELECT 4, 'Корзина создана',
         COUNT(DISTINCT user_id)
  FROM user_events
  WHERE event_type = 'cart_created'
    AND created_at >= CURRENT_DATE - 30

  UNION ALL

  SELECT 5, 'Лимит достигнут',
         COUNT(DISTINCT user_id)
  FROM user_events
  WHERE event_type = 'cart_limit_reached'
    AND created_at >= CURRENT_DATE - 30

  UNION ALL

  SELECT 6, 'Опрос пройден',
         COUNT(DISTINCT user_id)
  FROM user_events
  WHERE event_type = 'survey_completed'
    AND created_at >= CURRENT_DATE - 30
) t
ORDER BY sort
""",
        "pos": (0, 0, 12, 7),
    },
    {
        "name": "Конверсии между этапами (30 дней)",
        "display": "table",
        "sql": """
WITH stages AS (
  SELECT 'bot_start' AS event_type, 1 AS sort, 'Старт бота' AS stage
  UNION ALL SELECT 'session_start', 2, 'Сессия'
  UNION ALL SELECT 'product_search', 3, 'Поиск товара'
  UNION ALL SELECT 'cart_created', 4, 'Корзина создана'
  UNION ALL SELECT 'cart_limit_reached', 5, 'Лимит достигнут'
  UNION ALL SELECT 'survey_completed', 6, 'Опрос пройден'
),
counts AS (
  SELECT s.sort, s.stage,
         COUNT(DISTINCT ue.user_id) AS users
  FROM stages s
  LEFT JOIN user_events ue
    ON ue.event_type = s.event_type
    AND ue.created_at >= CURRENT_DATE - 30
  GROUP BY s.sort, s.stage
)
SELECT stage AS "Этап",
       users AS "Пользователи",
       CASE WHEN LAG(users) OVER (ORDER BY sort) > 0
            THEN ROUND(100.0 * users / LAG(users) OVER (ORDER BY sort), 1)
            ELSE NULL
       END AS "Конверсия %"
FROM counts
ORDER BY sort
""",
        "pos": (12, 0, 6, 7),
    },
    {
        "name": "Воронка по дням (7 дней)",
        "display": "line",
        "sql": """
SELECT created_at::date AS date,
       COUNT(DISTINCT CASE WHEN event_type = 'bot_start' THEN user_id END) AS "Старты",
       COUNT(DISTINCT CASE WHEN event_type = 'session_start' THEN user_id END) AS "Сессии",
       COUNT(DISTINCT CASE WHEN event_type = 'product_search' THEN user_id END) AS "Поиски",
       COUNT(DISTINCT CASE WHEN event_type = 'cart_created' THEN user_id END) AS "Корзины"
FROM user_events
WHERE created_at >= CURRENT_DATE - 7
GROUP BY created_at::date
ORDER BY date
""",
        "pos": (0, 7, 18, 6),
    },
]

CARDS_TRAFFIC = [
    {
        "name": "Источники трафика (30 дней)",
        "display": "pie",
        "sql": """
SELECT COALESCE(metadata->>'source', 'unknown') AS "Источник",
       COUNT(*) AS "Пользователи"
FROM user_events
WHERE event_type = 'bot_start'
  AND created_at >= CURRENT_DATE - 30
GROUP BY metadata->>'source'
ORDER BY COUNT(*) DESC
""",
        "pos": (0, 0, 8, 6),
    },
    {
        "name": "Конверсия по источникам (30 дней)",
        "display": "table",
        "sql": """
WITH starts AS (
  SELECT user_id,
         COALESCE(metadata->>'source', 'unknown') AS source
  FROM user_events
  WHERE event_type = 'bot_start'
    AND created_at >= CURRENT_DATE - 30
),
carts AS (
  SELECT DISTINCT user_id
  FROM user_events
  WHERE event_type = 'cart_created'
    AND created_at >= CURRENT_DATE - 30
)
SELECT s.source AS "Источник",
       COUNT(DISTINCT s.user_id) AS "Пришли",
       COUNT(DISTINCT c.user_id) AS "Создали корзину",
       CASE WHEN COUNT(DISTINCT s.user_id) > 0
            THEN ROUND(100.0 * COUNT(DISTINCT c.user_id) / COUNT(DISTINCT s.user_id), 1)
            ELSE 0
       END AS "Конверсия %"
FROM starts s
LEFT JOIN carts c ON c.user_id = s.user_id
GROUP BY s.source
ORDER BY COUNT(DISTINCT s.user_id) DESC
""",
        "pos": (8, 0, 10, 6),
    },
    {
        "name": "Источники трафика по дням",
        "display": "bar",
        "sql": """
SELECT created_at::date AS "Дата",
       COALESCE(metadata->>'source', 'unknown') AS "Источник",
       COUNT(*) AS "Пользователи"
FROM user_events
WHERE event_type = 'bot_start'
  AND created_at >= CURRENT_DATE - 30
GROUP BY created_at::date, metadata->>'source'
ORDER BY created_at::date
""",
        "pos": (0, 6, 18, 6),
        "viz": {"stackable.stack_type": "stacked"},
    },
    {
        "name": "Топ-пользователи по корзинам",
        "display": "table",
        "sql": """
SELECT u.user_id AS "Пользователь",
       u.carts_created AS "Корзины",
       u.cart_limit AS "Лимит",
       u.survey_completed AS "Опрос",
       u.message_count AS "Сообщения",
       u.created_at::date AS "Регистрация"
FROM users u
WHERE u.status = 'active'
ORDER BY u.carts_created DESC
LIMIT 20
""",
        "pos": (0, 12, 18, 6),
    },
]


# ─── Main ──────────────────────────────────────────────────────────

def setup_dashboards(url: str, email: str, password: str) -> None:
    """Основная функция: создать все дашборды."""
    global METABASE_URL, SESSION_TOKEN
    METABASE_URL = url.rstrip("/")

    print(f"Подключение к Metabase: {METABASE_URL}")
    SESSION_TOKEN = login(email, password)
    print("  Авторизация OK")

    # Найти базу данных
    db_id = get_database_id()
    if not db_id:
        print("ОШИБКА: база данных не найдена в Metabase")
        sys.exit(1)
    print(f"  База данных ID: {db_id}")

    # Создать коллекцию
    print("\nСоздание коллекции 'VkusVill Bot Analytics'...")
    try:
        coll_id = create_collection("VkusVill Bot Analytics")
        print(f"  Коллекция ID: {coll_id}")
    except HTTPError:
        coll_id = None
        print("  Коллекция уже существует, используем root")

    # Дашборд 1: Обзор за день
    dashboards_config = [
        ("Обзор за день", "Ключевые метрики бота: DAU, корзины, GMV, тренды", CARDS_OVERVIEW),
        ("Воронка конверсий", "Конверсия по этапам: старт → сессия → поиск → корзина", CARDS_FUNNEL),
        ("Источники трафика", "Откуда приходят пользователи и как конвертируются", CARDS_TRAFFIC),
    ]

    for dash_name, dash_desc, cards_config in dashboards_config:
        print(f"\n{'='*50}")
        print(f"Дашборд: {dash_name}")
        print(f"{'='*50}")

        dash_id = create_dashboard(dash_name, dash_desc, coll_id)
        print(f"  Дашборд ID: {dash_id}")

        for card_cfg in cards_config:
            name = card_cfg["name"]
            print(f"  + {name}...", end=" ")
            try:
                card_id = create_card(
                    name=name,
                    sql=card_cfg["sql"].strip(),
                    db_id=db_id,
                    display=card_cfg["display"],
                    visualization=card_cfg.get("viz"),
                    collection_id=coll_id,
                )
                col, row, size_x, size_y = card_cfg["pos"]
                add_card_to_dashboard(dash_id, card_id, row, col, size_x, size_y)
                print(f"OK (card={card_id})")
            except Exception as e:
                print(f"ОШИБКА: {e}")

    print(f"\n{'='*50}")
    print("Готово! Дашборды созданы.")
    print(f"Открой: {METABASE_URL}/collection/{coll_id or 'root'}")
    print(f"{'='*50}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Setup Metabase dashboards for VkusVill Bot")
    parser.add_argument("--url", default="http://YOUR_SERVER_IP:3001", help="Metabase URL")
    parser.add_argument("--email", required=True, help="Metabase admin email")
    parser.add_argument("--password", required=True, help="Metabase admin password")
    args = parser.parse_args()

    try:
        setup_dashboards(args.url, args.email, args.password)
    except URLError as e:
        print(f"Не удалось подключиться к Metabase: {e}")
        sys.exit(1)
    except KeyError as e:
        print(f"Ошибка авторизации: проверьте email и пароль ({e})")
        sys.exit(1)
