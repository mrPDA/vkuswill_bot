# Production Prompts

Эта папка содержит полные production-промпты, которые **не публикуются** в репозитории (`prompts/` в `.gitignore`).

## Файлы

- `system_prompt.txt` — системный промпт для текущего LLM runtime (роль, правила, безопасность)
- `recipe_extraction_prompt.txt` — промпт извлечения ингредиентов рецепта

## Приоритет загрузки

Код в `prompts.py` загружает промпты по цепочке:

1. **env** (`SYSTEM_PROMPT` / `RECIPE_EXTRACTION_PROMPT`) — приоритет, используется в production (Yandex Lockbox)
2. **файл** из `prompts/*.txt` — локальная разработка
3. **fallback-stub** в коде — минимальный промпт для запуска без файлов/env

## Локальная разработка

Скопируйте файлы промптов в эту папку. Бот подхватит их автоматически.

Или добавьте в `.env`:
```
SYSTEM_PROMPT="содержимое system_prompt.txt"
RECIPE_EXTRACTION_PROMPT="содержимое recipe_extraction_prompt.txt"
```

## Production (Yandex Lockbox)

```bash
yc lockbox secret add-version \
  --name vkuswill-bot-secrets \
  --payload '[{"key": "SYSTEM_PROMPT", "text_value": "..."}, {"key": "RECIPE_EXTRACTION_PROMPT", "text_value": "..."}]'
```
