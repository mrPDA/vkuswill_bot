#!/usr/bin/env python3
"""Миграция промптов из файлов в Langfuse Prompt Management.

Использование:
    # Сухой запуск (показать, что будет создано):
    uv run python scripts/migrate_prompts_to_langfuse.py --dry-run

    # Создать промпты для staging (по умолчанию):
    uv run python scripts/migrate_prompts_to_langfuse.py --label staging

    # Промоутить в production:
    uv run python scripts/migrate_prompts_to_langfuse.py --label production

Workflow:
    1. Положить полные тексты промптов в prompts/*.txt
    2. Запустить скрипт с --label staging
    3. Деплоить на stage с PROMPT_LABEL=staging, проверить
    4. Повторно запустить с --label production
    5. Деплоить на prod с PROMPT_LABEL=production (дефолт)

Файлы промптов (prompts/*.txt):
    system_prompt.txt           — основной системный промпт
    recipe_extraction.txt       — промпт извлечения рецептов
    profile_core.txt            — базовые правила бота
    profile_general.txt         — профиль: general
    profile_cart.txt            — профиль: cart
    profile_recipe.txt          — профиль: recipe
    profile_status.txt          — профиль: status
    profile_linking.txt         — профиль: linking
    mode_start.txt              — режим: start
    mode_compact.txt            — режим: compact
    mode_finalize.txt           — режим: finalize
    classify_intent.txt         — промпт классификации интента

Переменные окружения:
    LANGFUSE_PUBLIC_KEY  — публичный ключ Langfuse
    LANGFUSE_SECRET_KEY  — секретный ключ Langfuse
    LANGFUSE_HOST        — хост (по умолчанию https://cloud.langfuse.com)

ADR-007: Externalization промптов через Langfuse Prompt Management.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = PROJECT_ROOT / "prompts"

PROMPT_FILES: dict[str, str] = {
    "system-prompt": "system_prompt.txt",
    "recipe-extraction": "recipe_extraction.txt",
    "profile-core": "profile_core.txt",
    "profile-general": "profile_general.txt",
    "profile-cart": "profile_cart.txt",
    "profile-recipe": "profile_recipe.txt",
    "profile-status": "profile_status.txt",
    "profile-linking": "profile_linking.txt",
    "mode-start": "mode_start.txt",
    "mode-compact": "mode_compact.txt",
    "mode-finalize": "mode_finalize.txt",
    "classify-intent": "classify_intent.txt",
}


def _collect_prompts() -> dict[str, str]:
    """Собрать промпты из файлов prompts/*.txt."""
    prompts: dict[str, str] = {}
    missing: list[str] = []

    for langfuse_name, filename in PROMPT_FILES.items():
        path = PROMPTS_DIR / filename
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                prompts[langfuse_name] = text
            else:
                missing.append(f"{langfuse_name} ({filename}) — файл пуст")
        else:
            missing.append(f"{langfuse_name} ({filename}) — файл не найден")

    # Обратная совместимость: system_prompt.txt (старое имя)
    if "system-prompt" not in prompts:
        legacy = PROMPTS_DIR / "system_prompt.txt"
        if legacy.is_file():
            text = legacy.read_text(encoding="utf-8").strip()
            if text:
                prompts["system-prompt"] = text
                missing = [m for m in missing if not m.startswith("system-prompt")]

    if "recipe-extraction" not in prompts:
        legacy = PROMPTS_DIR / "recipe_extraction_prompt.txt"
        if legacy.is_file():
            text = legacy.read_text(encoding="utf-8").strip()
            if text:
                prompts["recipe-extraction"] = text
                missing = [m for m in missing if not m.startswith("recipe-extraction")]

    if missing:
        print(f"WARNING: {len(missing)} prompts not found in {PROMPTS_DIR}/:")
        for m in missing:
            print(f"  - {m}")
        print()

    return prompts


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate prompts to Langfuse")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created")
    parser.add_argument(
        "--label",
        default="staging",
        help="Langfuse label: staging (default) or production",
    )
    args = parser.parse_args()

    prompts = _collect_prompts()
    label = args.label

    if not prompts:
        print(f"ERROR: No prompt files found in {PROMPTS_DIR}/")
        print("Create prompt files first. See docstring for expected filenames.")
        sys.exit(1)

    if args.dry_run:
        print(f"Found {len(prompts)}/{len(PROMPT_FILES)} prompts to migrate (label={label}):\n")
        for name, text in prompts.items():
            preview = text[:120].replace("\n", "\\n")
            print(f"  {name:25s} ({len(text):5d} chars)  {preview}...")
        print(f"\nRun without --dry-run to create prompts in Langfuse with label={label}.")
        return

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        print("ERROR: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required.")
        print("Set them in environment or .env file.")
        sys.exit(1)

    from langfuse import Langfuse

    client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    print(f"Connected to Langfuse at {host} (label={label})")

    created = 0
    for name, text in prompts.items():
        try:
            client.create_prompt(
                name=name,
                type="text",
                prompt=text,
                labels=[label],
            )
            print(f"  [OK] {name} ({len(text)} chars) [{label}]")
            created += 1
        except Exception as exc:
            print(f"  [ERR] {name}: {exc}")

    client.flush()
    print(f"\nDone: {created}/{len(prompts)} prompts created with label={label}.")


if __name__ == "__main__":
    main()
