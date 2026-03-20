#!/usr/bin/env python3
"""Импорт промптов из prompts/langfuse-export/*.production.json в Langfuse.

Файлы экспорта — снимки production из Langfuse (поле ``name``, ``prompt``).
Пропускает JSON без текстового ``prompt`` (например ответы об ошибке API).

Использование (на Pi после ``docker compose -f docker-compose.pi.yml up``):

    docker compose -f docker-compose.pi.yml exec bot \\
      uv run python scripts/import_prompts_from_langfuse_export.py --label production

Затем при необходимости добейте пробелы из файлов:

    docker compose -f docker-compose.pi.yml exec bot \\
      uv run python scripts/migrate_prompts_to_langfuse.py --label production

Переменные: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST
(для self-hosted на Pi в compose боту задаётся LANGFUSE_HOST=http://langfuse:3000).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = PROJECT_ROOT / "prompts" / "langfuse-export"


def _load_export_prompts() -> dict[str, str]:
    if not EXPORT_DIR.is_dir():
        print(f"ERROR: directory not found: {EXPORT_DIR}")
        sys.exit(1)

    out: dict[str, str] = {}
    for path in sorted(EXPORT_DIR.glob("*.production.json")):
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            print(f"  [SKIP] {path.name}: empty file")
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"  [SKIP] {path.name}: invalid JSON ({exc})")
            continue
        if not isinstance(data, dict):
            print(f"  [SKIP] {path.name}: root is not an object")
            continue
        prompt = data.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            print(f"  [SKIP] {path.name}: no non-empty string 'prompt'")
            continue
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            print(f"  [SKIP] {path.name}: no string 'name'")
            continue
        out[name.strip()] = prompt.strip()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import prompts from prompts/langfuse-export into Langfuse",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List prompts that would be uploaded",
    )
    parser.add_argument(
        "--label",
        default="production",
        help="Langfuse label (default: production)",
    )
    args = parser.parse_args()

    prompts = _load_export_prompts()
    if not prompts:
        print(f"ERROR: no valid prompts in {EXPORT_DIR}/*.production.json")
        sys.exit(1)

    if args.dry_run:
        print(f"Would upload {len(prompts)} prompt(s) with label={args.label!r}:\n")
        for name, text in sorted(prompts.items()):
            prev = text[:100].replace("\n", "\\n")
            print(f"  {name:30s}  {len(text):5d} chars  {prev!s}...")
        return

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")

    if not public_key or not secret_key:
        print("ERROR: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are required.")
        sys.exit(1)

    from langfuse import Langfuse

    client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
    print(f"Connected to Langfuse at {host} (label={args.label})")

    created = 0
    for name, text in sorted(prompts.items()):
        try:
            client.create_prompt(
                name=name,
                type="text",
                prompt=text,
                labels=[args.label],
            )
            print(f"  [OK] {name} ({len(text)} chars)")
            created += 1
        except Exception as exc:
            print(f"  [ERR] {name}: {exc}")

    client.flush()
    print(f"\nDone: {created}/{len(prompts)} prompts created with label={args.label}.")


if __name__ == "__main__":
    main()
