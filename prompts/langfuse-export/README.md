# Langfuse Export — 2026-03-18

Экспорт данных перед удалением инфраструктуры Yandex Cloud.

## Содержимое

### Промпты (14 шт., label=production)

| Файл | Промпт | Версия |
|------|--------|--------|
| `system-prompt.production.json` | Главный системный промпт бота | v10 |
| `recipe-extraction.production.json` | Извлечение рецептов | — |
| `classify-intent.production.json` | Классификация интентов | — |
| `meal-plan-generation.production.json` | Генерация meal plan | — |
| `profile-cart.production.json` | Профиль: корзина | — |
| `profile-core.production.json` | Профиль: ядро | — |
| `profile-general.production.json` | Профиль: общий | — |
| `profile-linking.production.json` | Профиль: linking | — |
| `profile-meal_plan` | Профиль meal plan — в Langfuse заливается из `prompts/profile_meal_plan.txt` (`migrate_prompts_to_langfuse.py`) | — |
| `profile-recipe.production.json` | Профиль: рецепты | — |
| `profile-status.production.json` | Профиль: статус | — |
| `mode-compact.production.json` | Режим: компактный | — |
| `mode-finalize.production.json` | Режим: финализация | — |
| `mode-start.production.json` | Режим: старт | — |

### Трейсы (1474 шт.)

Каталог `traces/` — 15 файлов по 100 трейсов, отсортированных от новых к старым.
Период: с начала использования по 2026-03-17.

## Восстановление на новом Langfuse (Raspberry Pi)

После `docker compose -f docker-compose.pi.yml up` и создания API-ключей в UI:

```bash
make docker-pi-langfuse-import-prompts
# Профиль meal_plan и любые *.txt на хосте:
docker compose -f docker-compose.pi.yml exec bot uv run python scripts/migrate_prompts_to_langfuse.py --label production
```

Ранее (ручной curl к API):

```bash
curl -X POST http://localhost:3000/api/public/prompts \
  -u "PUBLIC_KEY:SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d @system-prompt.production.json
```

Трейсы импорту не подлежат (read-only история).
