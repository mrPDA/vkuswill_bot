#!/usr/bin/env python3
"""Регистрация моделей GigaChat в Langfuse для расчёта стоимости токенов.

Создаёт определения моделей (model definitions) в Langfuse проекте,
чтобы стоимость вызовов рассчитывалась автоматически на основе usage.

Тарифы GigaChat API (с 1 февраля 2026):
  - GigaChat-2-Lite:  65 ₽ / 1 млн токенов
  - GigaChat-2-Pro:  500 ₽ / 1 млн токенов
  - GigaChat-2-Max:  650 ₽ / 1 млн токенов
Источник: https://developers.sber.ru/docs/ru/gigachat/tariffs/legal-tariffs

ВАЖНО: Langfuse отображает стоимость как "$", но для этого проекта
все цены указаны в РУБЛЯХ (₽). Читайте "$" как "₽" в дашборде.

Использование:
    # Из .env (LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST)
    python scripts/setup_langfuse_models.py

    # Явно через аргументы
    python scripts/setup_langfuse_models.py \\
        --host http://localhost:3000 \\
        --public-key pk-lf-... \\
        --secret-key sk-lf-...

    # Только просмотр (без создания)
    python scripts/setup_langfuse_models.py --dry-run

    # Удалить существующие модели и создать заново
    python scripts/setup_langfuse_models.py --force
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request

# ── Тарифы GigaChat API ──────────────────────────────────────────────────────
# Цены в ₽ за 1 токен (= ₽ за 1М токенов / 1_000_000)
# GigaChat не разделяет input/output — единая цена за токен.

GIGACHAT_MODELS: list[dict] = [
    {
        "modelName": "GigaChat-2-Max",
        "matchPattern": r"(?i)^(GigaChat[\-\s]?2[\-\s]?Max)$",
        "description": "GigaChat 2 Max — 650₽/1M токенов",
        "unit": "TOKENS",
        # GigaChat: единая цена за токен (input = output).
        # Используем totalPrice вместо inputPrice/outputPrice,
        # т.к. Langfuse Server v2 не считает calculatedInputCost.
        "inputPrice": None,
        "outputPrice": None,
        "totalPrice": 650 / 1_000_000,  # 0.00065 ₽/token
    },
    {
        "modelName": "GigaChat-2-Pro",
        "matchPattern": r"(?i)^(GigaChat[\-\s]?2[\-\s]?Pro)$",
        "description": "GigaChat 2 Pro — 500₽/1M токенов",
        "unit": "TOKENS",
        "inputPrice": None,
        "outputPrice": None,
        "totalPrice": 500 / 1_000_000,  # 0.0005 ₽/token
    },
    {
        "modelName": "GigaChat-2-Lite",
        "matchPattern": r"(?i)^(GigaChat[\-\s]?2[\-\s]?Lite|GigaChat)$",
        "description": "GigaChat 2 Lite — 65₽/1M токенов (включает GigaChat без версии)",
        "unit": "TOKENS",
        "inputPrice": None,
        "outputPrice": None,
        "totalPrice": 65 / 1_000_000,  # 0.000065 ₽/token
    },
]


def _make_auth_header(public_key: str, secret_key: str) -> str:
    """Basic auth header для Langfuse API."""
    credentials = f"{public_key}:{secret_key}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def _api_request(
    method: str,
    url: str,
    auth_header: str,
    data: dict | None = None,
) -> dict:
    """HTTP-запрос к Langfuse API."""
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(  # noqa: S310
        url,
        data=body,
        method=method,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        raise RuntimeError(f"Langfuse API {method} {url} → {e.code}: {error_body}") from e


def get_existing_models(host: str, auth_header: str) -> list[dict]:
    """Получить список существующих model definitions."""
    url = f"{host.rstrip('/')}/api/public/models"
    result = _api_request("GET", url, auth_header)
    return result.get("data", [])


def delete_model(host: str, auth_header: str, model_id: str) -> None:
    """Удалить model definition по ID."""
    url = f"{host.rstrip('/')}/api/public/models/{model_id}"
    _api_request("DELETE", url, auth_header)


def create_model(host: str, auth_header: str, model_def: dict) -> dict:
    """Создать model definition в Langfuse."""
    url = f"{host.rstrip('/')}/api/public/models"
    # Убираем description — Langfuse API v2 его не принимает
    payload = {k: v for k, v in model_def.items() if k != "description"}
    return _api_request("POST", url, auth_header, payload)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Регистрация моделей GigaChat в Langfuse",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("LANGFUSE_HOST", "http://localhost:3000"),
        help="URL Langfuse (default: LANGFUSE_HOST или http://localhost:3000)",
    )
    parser.add_argument(
        "--public-key",
        default=os.getenv("LANGFUSE_PUBLIC_KEY", ""),
        help="Langfuse public key (default: LANGFUSE_PUBLIC_KEY)",
    )
    parser.add_argument(
        "--secret-key",
        default=os.getenv("LANGFUSE_SECRET_KEY", ""),
        help="Langfuse secret key (default: LANGFUSE_SECRET_KEY)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать что будет создано, без записи",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Удалить существующие GigaChat модели и создать заново",
    )
    args = parser.parse_args()

    if not args.public_key or not args.secret_key:
        # Попробовать загрузить из .env
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("\"'")
                    if key == "LANGFUSE_PUBLIC_KEY" and not args.public_key:
                        args.public_key = value
                    elif key == "LANGFUSE_SECRET_KEY" and not args.secret_key:
                        args.secret_key = value
                    elif key == "LANGFUSE_HOST" and args.host == "http://localhost:3000":
                        args.host = value

    if not args.public_key or not args.secret_key:
        print(
            "ОШИБКА: Укажите LANGFUSE_PUBLIC_KEY и LANGFUSE_SECRET_KEY "
            "(через env или аргументы --public-key / --secret-key)",
            file=sys.stderr,
        )
        sys.exit(1)

    auth = _make_auth_header(args.public_key, args.secret_key)
    host = args.host.rstrip("/")

    print(f"Langfuse: {host}")
    print(f"Моделей для регистрации: {len(GIGACHAT_MODELS)}")
    print()

    # ── Показать тарифы ──
    print("Тарифы GigaChat API (₽ за 1М токенов):")
    print("-" * 50)
    for m in GIGACHAT_MODELS:
        price = m.get("totalPrice") or m.get("inputPrice") or 0
        price_per_m = price * 1_000_000
        print(f"  {m['modelName']:20s}  {price_per_m:>8.0f} ₽/1M токенов")
    print()

    if args.dry_run:
        print("[DRY RUN] Модели НЕ будут созданы.")
        print()
        for m in GIGACHAT_MODELS:
            price = m.get("totalPrice") or m.get("inputPrice") or 0
            print(f"  Модель: {m['modelName']}")
            print(f"  Match:  {m['matchPattern']}")
            print(f"  Total:  {price:.10f} ₽/token")
            print(f"  Описание: {m.get('description', '-')}")
            print()
        return

    # ── Проверить существующие модели ──
    print("Проверяю существующие модели в Langfuse...")
    try:
        existing = get_existing_models(host, auth)
    except RuntimeError as e:
        print(f"ОШИБКА при получении моделей: {e}", file=sys.stderr)
        sys.exit(1)

    existing_names = {m["modelName"] for m in existing}
    gigachat_existing = [m for m in existing if m["modelName"].startswith("GigaChat")]

    if gigachat_existing:
        print(f"Найдено {len(gigachat_existing)} GigaChat модел(ей):")
        for m in gigachat_existing:
            print(f"  - {m['modelName']} (id: {m['id']})")

        if args.force:
            print("\n--force: удаляю существующие модели...")
            for m in gigachat_existing:
                try:
                    delete_model(host, auth, m["id"])
                    print(f"  Удалена: {m['modelName']}")
                except RuntimeError as e:
                    print(f"  Ошибка удаления {m['modelName']}: {e}")
            existing_names -= {m["modelName"] for m in gigachat_existing}
        else:
            print("\nДля перезаписи используйте --force. Пропускаю существующие модели.\n")

    # ── Создать модели ──
    created = 0
    skipped = 0
    for model_def in GIGACHAT_MODELS:
        name = model_def["modelName"]
        if name in existing_names:
            print(f"  SKIP  {name} (уже существует)")
            skipped += 1
            continue

        try:
            result = create_model(host, auth, model_def)
            model_id = result.get("id", "?")
            print(f"  OK    {name} (id: {model_id})")
            created += 1
        except RuntimeError as e:
            print(f"  FAIL  {name}: {e}", file=sys.stderr)

    print()
    print(f"Итого: создано {created}, пропущено {skipped}")
    print()
    print('ВАЖНО: Langfuse показывает стоимость как "$", но все цены в РУБЛЯХ (₽).')
    print(
        "Стоимость рассчитывается автоматически для НОВЫХ traces. Старые traces не пересчитываются."
    )


if __name__ == "__main__":
    main()
