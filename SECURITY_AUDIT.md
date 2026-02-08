# AppSec Audit Report — VkusVill Bot

## Метаданные
- **Дата:** 2026-02-08
- **Версия:** v0.3.0
- **Scope:** Full (SAST + OSS + OWASP + AI Safety)
- **Инструменты:** ruff (--select S), ручной анализ паттернов, grep, AST-анализ
- **Файлы проанализированы:** 12 модулей (src/), 7 тест-файлов безопасности

---

## Executive Summary

| Категория  | Critical | High | Medium | Low | Info |
|------------|:--------:|:----:|:------:|:---:|:----:|
| SAST       | 0        | 1    | 1      | 0   | 2    |
| OSS        | 0        | 0    | 0      | 0   | 1    |
| OWASP      | 0        | 0    | 2      | 1   | 1    |
| AI Safety  | 0        | 0    | 1      | 0   | 2    |
| CI/CD      | 0        | 0    | 2      | 0   | 0    |
| **Итого**  | **0**    | **1**| **6**  | **1**| **6**|

## Общая оценка: ⚠️ CONDITIONAL PASS

**Критерий:** 0 Critical, 1 High — допустимо с планом исправления.

Проект демонстрирует **зрелую культуру безопасности**: есть тесты SAST, AI Safety, конфигурации; rate limiting; input validation; изоляция пользователей; LRU-вытеснение. Однако найдены точки для улучшения.

---

## Findings

### HIGH

---

### [HIGH] F-01: SSL-верификация отключена для GigaChat API

**CWE:** CWE-295 — Improper Certificate Validation
**OWASP:** A02 — Cryptographic Failures
**CVSS:** 7.4 (Network/High/None/Changed/High/None)
**Файл:** `src/vkuswill_bot/services/gigachat_service.py`, строка 154
**Категория:** SAST

**Описание:**
Параметр `verify_ssl_certs=False` отключает проверку TLS-сертификатов при обращении к GigaChat API. Это позволяет проводить Man-in-the-Middle (MiTM) атаки — перехватывать credentials и данные пользователей. В коде есть TODO-комментарий о необходимости включения, но фикс не реализован.

**Доказательство:**

```python
self._client = GigaChat(
    credentials=credentials,
    model=model,
    scope=scope,
    verify_ssl_certs=False,  # <-- MiTM уязвимость
    timeout=60,
)
```

**Рекомендация:**

```python
# Вариант 1: Использовать CA-bundle Минцифры
self._client = GigaChat(
    credentials=credentials,
    model=model,
    scope=scope,
    verify_ssl_certs=True,
    ca_bundle_file="certs/russian_trusted_root_ca.pem",
    timeout=60,
)

# Вариант 2: Установить сертификат Минцифры системно
# и использовать verify_ssl_certs=True без ca_bundle_file
```

**Workaround:** Установить CA-сертификат Минцифры системно на сервере развёртывания.

**Ссылки:**
- https://cwe.mitre.org/data/definitions/295.html
- https://www.gosuslugi.ru/crt

---

### MEDIUM

---

### [MEDIUM] F-02: Отсутствует санитизация HTML в ответах GigaChat

**CWE:** CWE-79 — Improper Neutralization of Input During Web Page Generation
**OWASP:** A03 — Injection
**CVSS:** 5.4
**Файл:** `src/vkuswill_bot/bot/handlers.py`, строка 105
**Категория:** SAST

**Описание:**
Бот использует `ParseMode.HTML` (строка 46, `__main__.py`). Ответы GigaChat передаются напрямую в `message.answer(chunk)` без экранирования HTML-тегов. Если GigaChat сгенерирует невалидный или вредоносный HTML (например, через indirect prompt injection в данных MCP), Telegram может отклонить сообщение или отобразить его некорректно.

**Рекомендация:**

```python
from aiogram.utils.markdown import html_decoration as hd

# Перед отправкой — экранировать, если ответ не ожидаемо содержит HTML
# Или использовать безопасный маркдаун:
async def handle_text(...):
    ...
    for chunk in chunks:
        try:
            await message.answer(chunk, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            # Fallback: отправить без форматирования
            await message.answer(chunk, parse_mode=None)
```

---

### [MEDIUM] F-03: RecipeStore и PreferencesStore используют один путь к БД

**CWE:** CWE-400 — Uncontrolled Resource Consumption
**OWASP:** A04 — Insecure Design
**CVSS:** 4.3
**Файл:** `src/vkuswill_bot/__main__.py`, строки 58-61
**Категория:** SAST

**Описание:**
`PreferencesStore` и `RecipeStore` создаются с одним `config.database_path`, но это разные SQLite-базы с разными таблицами. Оба модуля открывают независимые соединения к одному файлу. Это может привести к конфликтам блокировок SQLite (database is locked) под нагрузкой.

**Рекомендация:**

```python
# Использовать разные пути
prefs_store = PreferencesStore(config.database_path)
recipe_store = RecipeStore(config.database_path.replace(".db", "_recipes.db"))

# Или лучше: добавить отдельный параметр в Config
recipe_database_path: str = "data/recipes.db"
```

---

### [MEDIUM] F-04: Нет ограничения длины входных данных для preferences

**CWE:** CWE-400 — Uncontrolled Resource Consumption
**OWASP:** A03 — Injection
**CVSS:** 4.0
**Файл:** `src/vkuswill_bot/services/preferences_store.py`, строки 78-103
**Категория:** OWASP

**Описание:**
Методы `set()` и `delete()` принимают `category` и `preference` строки без ограничения длины. GigaChat формирует аргументы, и теоретически может передать сверхдлинную строку (через prompt injection или hallucination), что приведёт к раздуванию SQLite-базы.

**Рекомендация:**

```python
MAX_CATEGORY_LENGTH = 100
MAX_PREFERENCE_LENGTH = 500

async def set(self, user_id: int, category: str, preference: str) -> str:
    category = category.strip().lower()[:MAX_CATEGORY_LENGTH]
    preference = preference.strip()[:MAX_PREFERENCE_LENGTH]
    if not category or not preference:
        return json.dumps({"ok": False, "error": "Пустая категория или предпочтение"}, ...)
    ...
```

---

### [MEDIUM] F-05: ThrottlingMiddleware хранит timestamps в памяти без лимита

**CWE:** CWE-770 — Allocation of Resources Without Limits or Throttling
**OWASP:** A04 — Insecure Design
**CVSS:** 4.0
**Файл:** `src/vkuswill_bot/bot/middlewares.py`, строка 37
**Категория:** OWASP

**Описание:**
`_user_timestamps` — это `defaultdict(list)`, который растёт с каждым новым `user_id`. При DDoS-атаке с множеством уникальных пользователей (Telegram user_id) словарь будет расти бесконечно, потребляя память. Очистка устаревших записей происходит только при повторном обращении того же пользователя.

**Рекомендация:**

```python
from collections import OrderedDict

MAX_TRACKED_USERS = 10_000

class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit=5, period=60.0):
        self.rate_limit = rate_limit
        self.period = period
        self._user_timestamps: OrderedDict[int, list[float]] = OrderedDict()

    def _cleanup_old_users(self) -> None:
        """Удалить записи пользователей, которые давно не обращались."""
        while len(self._user_timestamps) > MAX_TRACKED_USERS:
            self._user_timestamps.popitem(last=False)
```

---

### [MEDIUM] F-06: Security jobs в CI используют continue-on-error: true

**CWE:** CWE-693 — Protection Mechanism Failure
**OWASP:** A05 — Security Misconfiguration
**CVSS:** 4.0
**Файл:** `.github/workflows/ci.yml`, строки 98, 102
**Категория:** CI/CD

**Описание:**
Security-тесты (`test_security_sast.py`, `test_config_security.py`) запускаются с `continue-on-error: true`. Это значит, что **даже при провале security-тестов CI будет зелёным**. Аналогично ruff check тоже не блокирует пайплайн.

**Рекомендация:**
Убрать `continue-on-error: true` для security-джобов, чтобы провал блокировал merge:

```yaml
  security:
    name: Security checks
    runs-on: ubuntu-latest
    steps:
      - name: Run security tests (bandit)
        run: uv run pytest tests/test_security_sast.py -v --tb=short
        # Убрать continue-on-error: true

      - name: Run config security tests
        run: uv run pytest tests/test_config_security.py -v --tb=short
        # Убрать continue-on-error: true
```

---

### [MEDIUM] F-07: Отсутствует отдельный Bandit/pip-audit шаг в CI

**CWE:** CWE-1104 — Use of Unmaintained Third Party Components
**OWASP:** A06 — Vulnerable and Outdated Components
**CVSS:** 4.0
**Файл:** `.github/workflows/ci.yml`
**Категория:** CI/CD

**Описание:**
В CI нет прямого запуска `bandit -r src/` и `pip-audit --strict` как отдельных шагов. Bandit запускается только через pytest-тесты (которые проверяют паттерны, но не всё, что проверяет bandit). pip-audit не запускается вообще.

**Рекомендация:**
Добавить шаги в секцию `security`:

```yaml
      - name: SAST — Bandit
        run: uv run bandit -r src/ -ll -ii

      - name: OSS — pip-audit
        run: uv run pip-audit --strict --desc
```

---

### LOW

---

### [LOW] F-08: user_id логируется в open text

**CWE:** CWE-532 — Insertion of Sensitive Information into Log File
**OWASP:** A09 — Security Logging and Monitoring Failures
**CVSS:** 2.0
**Файл:** `src/vkuswill_bot/services/gigachat_service.py`, множественные строки
**Категория:** SAST

**Описание:**
`user_id` Telegram логируется в открытом виде (строки 219, 779, 803). Это персональные данные (можно связать с аккаунтом Telegram). В production-логах лучше использовать хэш или маску.

**Рекомендация:**
Для production использовать хэшированный user_id:

```python
import hashlib

def _mask_user_id(user_id: int) -> str:
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:8]
```

---

### INFO

---

### [INFO] F-09: Хорошо — нет опасных функций

**Категория:** SAST

Grep по `eval()`, `exec()`, `pickle.loads()`, `os.system()`, `subprocess...shell=True`, `__import__()` — **0 результатов**. Код не использует опасные функции.

---

### [INFO] F-10: Хорошо — секреты загружаются только из env

**Категория:** SAST

Все секреты (`bot_token`, `gigachat_credentials`) загружаются через `pydantic-settings` из `.env`. Нет захардкоженных значений в коде.

---

### [INFO] F-11: Хорошо — .gitignore покрывает все чувствительные файлы

**Категория:** SAST

`.env`, `*.log`, `data/`, `*.db`, `.venv/`, `__pycache__/` — всё в `.gitignore`.

---

### [INFO] F-12: Хорошо — изоляция данных между пользователями

**Категория:** AI Safety

Каждый пользователь имеет отдельную историю диалога (`OrderedDict` по `user_id`). Тесты подтверждают отсутствие cross-contamination между пользователями.

---

### [INFO] F-13: Хорошо — комплексная защита от AI DoS

**Категория:** AI Safety

- `MAX_USER_MESSAGE_LENGTH = 4096` — обрезка длинных сообщений
- `max_tool_calls` + `max_total_steps` — двойная защита от бесконечных циклов
- `MAX_IDENTICAL_TOOL_CALLS = 2` — детекция зацикливания
- `MAX_CONVERSATIONS = 1000` — LRU-вытеснение старых диалогов
- `max_history` — обрезка длинных историй
- `ThrottlingMiddleware` — rate limiting 5 msg/60 sec

---

### [INFO] F-14: pip-audit — пакет не на PyPI

**Категория:** OSS

pip-audit показал, что `vkuswill-bot` не опубликован на PyPI (ожидаемо для private-пакета). Все зависимости из PyPI не показали CVE при сканировании. Рекомендуется периодический пересчёт.

---

### [INFO] F-15: Хорошо — MCP-клиент с защитными механизмами

**Категория:** AI Safety

- Таймауты настроены: `CONNECT_TIMEOUT=15`, `READ_TIMEOUT=120`
- Retry с ограничением: `MAX_RETRIES=3` с экспоненциальной задержкой
- Сессия сбрасывается при ошибках
- Аргументы корзины фиксируются (`_fix_cart_args`)
- Поисковые запросы очищаются (`_clean_search_query`)

---

## Рекомендации по приоритету

### P0 — Исправить до релиза

| # | Finding | Сложность | Действие |
|---|---------|-----------|----------|
| 1 | F-01: SSL отключён для GigaChat | Средняя | Установить CA Минцифры, включить `verify_ssl_certs=True` |
| 2 | F-06: Security CI не блокирует merge | Лёгкая | Убрать `continue-on-error: true` для security jobs |

### P1 — Исправить в ближайшем спринте

| # | Finding | Сложность | Действие |
|---|---------|-----------|----------|
| 3 | F-02: HTML-санитизация ответов | Лёгкая | Добавить try/except с fallback на `parse_mode=None` |
| 4 | F-03: Общая БД для preferences и recipes | Лёгкая | Разделить на два файла SQLite |
| 5 | F-04: Нет лимита длины preferences | Лёгкая | Добавить `[:MAX_LENGTH]` |
| 6 | F-05: ThrottlingMiddleware без лимита памяти | Лёгкая | Добавить `MAX_TRACKED_USERS` с LRU-вытеснением |
| 7 | F-07: Нет Bandit/pip-audit в CI | Лёгкая | Добавить шаги в `ci.yml` |

### P2 — Бэклог

| # | Finding | Сложность | Действие |
|---|---------|-----------|----------|
| 8 | F-08: user_id в логах | Лёгкая | Маскировать user_id в production |

---

## Метрики безопасности

| Метрика | Значение | Цель |
|---------|:--------:|:----:|
| Опасные функции (eval/exec/pickle) | 0 | 0 |
| Захардкоженные секреты | 0 | 0 |
| SSL-верификация | Отключена (1 место) | Включена |
| Ruff security rules (S) | 0 findings | 0 |
| OWASP Top 10 покрытие | 8/10 | 10/10 |
| Тесты безопасности | 7 файлов, ~50 тестов | Поддерживать |
| Prompt Injection тесты | 11+ payload-ов | 20+ |
| Jailbreak тесты | 9+ payload-ов | 15+ |
| Rate limiting | 5 msg/60 sec | Настроено |
| Input validation | MAX 4096 символов | Настроено |
| LRU-вытеснение диалогов | MAX 1000 | Настроено |
| Tool call лимит | MAX 20 (с double-guard) | Настроено |

---

## Сильные стороны проекта

1. **Зрелая тестовая база безопасности** — 7 тест-файлов, покрывающих SAST, AI Safety, конфигурацию, input validation
2. **Многоуровневая защита от AI DoS** — лимиты на сообщения, tool calls, историю, диалоги
3. **Правильная архитектура секретов** — pydantic-settings, `.env`, `.gitignore`
4. **Изоляция пользователей** — отдельные истории, тесты на cross-contamination
5. **Rate limiting** — ThrottlingMiddleware из коробки
6. **CI с секцией security** — пусть и с `continue-on-error`, но уже настроено
7. **Чистый код без опасных функций** — 0 результатов по всем SAST-паттернам
8. **Параметризованные SQL-запросы** — все запросы к SQLite используют `?` placeholders
