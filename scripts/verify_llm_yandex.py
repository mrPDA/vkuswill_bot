#!/usr/bin/env python3
"""Проверка доступа к Yandex Cloud AI Studio (OpenAI-compatible) из .env.

Запуск из корня репозитория:
  python3 scripts/verify_llm_yandex.py

Ожидаются LLM_API_KEY, LLM_MODEL; LLM_BASE_URL по умолчанию — llm.api.cloud.yandex.net/v1.
Таймаут запроса 120 с (у `yc`/API бывают долгие ответы).

Секреты в stdout не выводятся.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ[key.strip()] = val.strip()


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if not env_path.is_file():
        print("Нет файла .env в корне репозитория", file=sys.stderr)
        return 1
    _load_dotenv(env_path)

    key = os.environ.get("LLM_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    base = (os.environ.get("LLM_BASE_URL") or "https://llm.api.cloud.yandex.net/v1").rstrip("/")
    if not key or not model:
        print("Задайте LLM_API_KEY и LLM_MODEL в .env", file=sys.stderr)
        return 1

    url = f"{base}/chat/completions"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": "Ответь одним словом: ок"}],
            "max_tokens": 32,
            "temperature": 0,
        }
    ).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Authorization": f"Api-Key {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode())
            text = body["choices"][0]["message"]["content"]
            print("HTTP", resp.status)
            print("LLM:", text.strip()[:500])
    except urllib.error.HTTPError as e:
        snippet = e.read().decode(errors="replace")[:600]
        print("HTTP", e.code, file=sys.stderr)
        print(snippet, file=sys.stderr)
        return 1
    except Exception as e:
        print(type(e).__name__, str(e)[:400], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
