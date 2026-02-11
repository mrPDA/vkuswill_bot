# AppSec Audit Report — VkusVill Bot

## Метаданные

- **Дата:** 2026-02-09
- **Версия:** v0.3.0
- **Ветка:** `feature/async-cart-and-cleanup`
- **Scope:** Full (SAST + OSS + OWASP + AI Safety + DAST-паттерны)
- **Инструменты:** bandit 1.7, ruff 0.15, pip-audit 2.7, custom pytest security tests
- **Код:** 3 381 строка (src/), 21 модуль

## Executive Summary

| Категория | Critical | High | Medium | Low | Info |
|-----------|:--------:|:----:|:------:|:---:|:----:|
| SAST      | 0        | 0    | 1      | 1   | 1    |
| OSS       | 0        | 0    | 0      | 0   | 0    |
| OWASP     | 0        | 0    | 0      | 1   | 2    |
| AI Safety | 0        | 0    | 0      | 0   | 1    |
| DAST      | 0        | 0    | 0      | 0   | 1    |
| **Итого** | **0**    | **0**| **1**  | **2**| **5**|

## Общая оценка: ✅ PASS

**Критерии:** 0 Critical, 0 High — проект проходит security gate.

Проект показывает **зрелый уровень безопасности** для стадии v0.3.0:
- Bandit: 0 findings по 3 381 строке кода
- Ruff (flake8-bandit): All checks passed
- pip-audit: No known vulnerabilities
- Тесты безопасности: **315 passed, 5 xfailed** (xfail — ожидаемые ограничения)
- Кастомные паттерны: 0 опасных функций, 0 захардкоженных секретов

---

## Findings

### Medium

#### [MEDIUM] SSL-верификация отключена для GigaChat API

**CWE:** CWE-295 — Improper Certificate Validation
**OWASP:** A02 — Cryptographic Failures
**CVSS:** 5.9 (CVSS:3.1/AV:N/AC:H/PR:N/UI:N/S:U/C:H/I:N/A:N)
**Файл:** `src/vkuswill_bot/services/gigachat_service.py`, строка 80
**Категория:** SAST

**Описание:**
`verify_ssl_certs=False` отключает проверку TLS-сертификата при подключении к GigaChat API.
Это делает соединение уязвимым к MITM-атакам. В коде есть TODO-комментарий (строка 77),
объясняющий причину — GigaChat SDK пока не поддерживает CA Минцифры.

**Доказательство:**
```python
# TODO: verify_ssl_certs=True + ca_bundle_file когда SDK поддержит CA Минцифры
self._client = GigaChat(
    credentials=credentials, model=model, scope=scope,
    verify_ssl_certs=False, timeout=60,
)
```

**Рекомендация:**
```python
# Установить сертификат Минцифры и включить верификацию
self._client = GigaChat(
    credentials=credentials, model=model, scope=scope,
    verify_ssl_certs=True,
    ca_bundle_file="/path/to/russian_trusted_root_ca.crt",
    timeout=60,
)
```

**Workaround (до поддержки SDK):**
Можно установить CA Минцифры в системное хранилище сертификатов:
```bash
# Скачать и установить корневой сертификат Минцифры
wget https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt
cp russian_trusted_root_ca_pem.crt /usr/local/share/ca-certificates/
update-ca-certificates
```

**Ссылки:**
- https://cwe.mitre.org/data/definitions/295.html

---

### Low

#### [LOW] Полные stack trace в серверных логах MCP-клиента

**CWE:** CWE-209 — Generation of Error Message Containing Sensitive Information
**OWASP:** A09 — Security Logging and Monitoring Failures
**CVSS:** 2.0
**Файл:** `src/vkuswill_bot/services/mcp_client.py`, строки 267, 340
**Категория:** SAST

**Описание:**
`traceback.format_exc()` записывает полный stack trace в логи при ошибках MCP-клиента.
Это допустимо для серверных логов, но может раскрыть внутреннюю структуру приложения
при утечке логов. Важно: информация **не попадает** к пользователю — user-facing
ошибки генерические.

**Доказательство:**
```python
logger.warning(
    "MCP get_tools попытка %d/%d: %r\n%s",
    attempt + 1, MAX_RETRIES, e,
    traceback.format_exc(),
)
```

**Рекомендация:**
Ограничить детальность stack trace уровнем `DEBUG`:
```python
logger.warning("MCP get_tools попытка %d/%d: %r", attempt + 1, MAX_RETRIES, e)
logger.debug("MCP get_tools traceback:\n%s", traceback.format_exc())
```

---

#### [LOW] CI security checks не блокируют мёрж

**CWE:** N/A
**OWASP:** A05 — Security Misconfiguration
**CVSS:** 2.0
**Файл:** `.github/workflows/ci.yml`, строки 98, 102
**Категория:** OWASP

**Описание:**
Security-job в CI использует `continue-on-error: true`, что позволяет
мёржить PR даже при провалившихся security-тестах. Это снижает эффективность
security gate.

**Доказательство:**
```yaml
- name: Run security tests (bandit)
  run: uv run pytest tests/test_security_sast.py -v --tb=short
  continue-on-error: true

- name: Run config security tests
  run: uv run pytest tests/test_config_security.py -v --tb=short
  continue-on-error: true
```

**Рекомендация:**
Убрать `continue-on-error: true` для security steps, чтобы провал
security-тестов блокировал мёрж:
```yaml
- name: Run security tests (bandit)
  run: uv run pytest tests/test_security_sast.py -v --tb=short

- name: Run config security tests
  run: uv run pytest tests/test_config_security.py -v --tb=short
```

---

### Info

#### [INFO] Файл bot.log создаётся при запуске

**Файл:** `src/vkuswill_bot/__main__.py`, строка 30
**Категория:** SAST

**Описание:**
Лог-файл `bot.log` создаётся на диске при запуске. В `.gitignore` правило `*.log` присутствует (хорошо).
В production (Kubernetes) рекомендуется использовать stdout-only логирование
вместо файлов, чтобы избежать заполнения диска.

**Рекомендация для production:**
```python
handlers = [logging.StreamHandler()]
if config.debug:
    handlers.append(logging.FileHandler(LOG_FILE, encoding="utf-8"))
```

---

#### [INFO] Ruff security rules не включены в pyproject.toml

**Файл:** `pyproject.toml`
**Категория:** SAST

**Описание:**
В `pyproject.toml` отсутствует секция `[tool.ruff.lint]` с правилами безопасности.
Рекомендуется добавить для автоматической проверки при разработке.

**Рекомендация:**
```toml
[tool.ruff.lint]
select = [
    "E", "W",    # pycodestyle
    "F",          # pyflakes
    "S",          # flake8-bandit (security)
    "B",          # flake8-bugbear
    "UP",         # pyupgrade
    "RUF",        # ruff-specific
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101"]  # assert в тестах OK
```

---

#### [INFO] Отсутствуют тесты AI Safety и Input Validation в CI security job

**Файл:** `.github/workflows/ci.yml`
**Категория:** AI Safety / DAST

**Описание:**
CI запускает `test_security_sast.py` и `test_config_security.py`, но не включает
`test_ai_safety.py` и `test_input_validation.py` в security-job. Эти тесты запускаются
только в общем `pytest` (job test), но не отдельно в security gate.

**Рекомендация:**
```yaml
- name: Run AI safety tests
  run: uv run pytest tests/test_ai_safety.py tests/test_input_validation.py -v --tb=short
```

---

#### [INFO] Нет Bandit как отдельного шага в CI

**Файл:** `.github/workflows/ci.yml`
**Категория:** SAST

**Описание:**
Bandit запускается только через pytest (кастомный тест), но не как отдельный шаг CI.
Рекомендуется добавить прямой запуск для полного отчёта.

**Рекомендация:**
```yaml
- name: SAST — Bandit
  run: uv run bandit -r src/ -ll -ii
```

---

#### [INFO] Нет pip-audit в CI

**Файл:** `.github/workflows/ci.yml`
**Категория:** OSS

**Описание:**
Аудит зависимостей (pip-audit) не входит в CI pipeline. CVE в зависимостях
могут появиться после публикации пакета и не будут обнаружены автоматически.

**Рекомендация:**
```yaml
- name: OSS — pip-audit
  run: uv run pip-audit --desc
```

---

## OWASP Top 10 (2021) — маппинг

| # | OWASP Category | Статус | Комментарий |
|---|---------------|:------:|-------------|
| A01 | Broken Access Control | ✅ | Rate limiting (ThrottlingMiddleware), admin role check, user isolation, блокировка пользователей |
| A02 | Cryptographic Failures | ⚠️ | `verify_ssl_certs=False` для GigaChat (обоснованное исключение — CA Минцифры). Секреты из .env, не в коде. Redis URL маскируется. |
| A03 | Injection | ✅ | SQL: параметризованные запросы (asyncpg $1, aiosqlite ?). Prompt injection: защита в system prompt + тесты. HTML: whitelist-санитизация. |
| A04 | Insecure Design | ✅ | Принцип минимальных привилегий. max_tool_calls лимит. Duplicate detection. Session isolation. |
| A05 | Security Misconfiguration | ✅ | Конфиг через pydantic-settings. debug=False по умолчанию. .env в .gitignore. HTTPS для MCP. |
| A06 | Vulnerable Components | ✅ | pip-audit: 0 CVE. Зависимости pinned в uv.lock. |
| A07 | Auth Failures | ✅ | Telegram ID как идентификатор. Admin role в PostgreSQL. Rate limiting per-user. |
| A08 | Data Integrity Failures | ✅ | Cart verification (проверка xml_id из поиска). Validated function_call arguments. JSON parse safety. |
| A09 | Logging & Monitoring | ⚠️ | traceback.format_exc() в логах WARNING (рекомендуется → DEBUG). Redis URL маскируется (хорошо). Секреты не логируются. |
| A10 | SSRF | ✅ | MCP URL из .env конфига, не из пользовательского ввода. HTTPS only. |

---

## AI Safety (OWASP LLM Top 10)

| # | Угроза | Статус | Контроли |
|---|--------|:------:|----------|
| LLM01 | Prompt Injection | ✅ | System prompt с секцией безопасности. 11 тестовых payload'ов — все pass. Сообщения строго через USER role. |
| LLM02 | Insecure Output Handling | ✅ | HTML-санитизация (whitelist). _sanitize_telegram_html() в handlers.py. Длинные сообщения разбиваются. |
| LLM03 | Training Data Poisoning | N/A | Используется hosted GigaChat, не fine-tuned. |
| LLM04 | Model DoS | ✅ | max_tool_calls=20 лимит. max_total_steps = max_tool_calls*2. Duplicate call detection. Семафор на API (15 concurrent). |
| LLM05 | Supply Chain | ✅ | pip-audit 0 CVE. uv.lock для детерминированных сборок. |
| LLM06 | Sensitive Info Disclosure | ✅ | System prompt не раскрывается (12 тестов extraction). Ответ-заглушка: "Я бот ВкусВилл...". |
| LLM07 | Insecure Plugin Design | ✅ | MCP tools — фиксированный набор (search, details, cart). Аргументы валидируются. Timeout/retry настроены. |
| LLM08 | Excessive Agency | ✅ | max_tool_calls=20. Нет инструментов для удаления/модификации данных сервера. Только чтение + cart link. |
| LLM09 | Overreliance | ✅ | Дисклеймер в каждом ответе с корзиной. Верификация xml_id из результатов поиска. |
| LLM10 | Model Theft | N/A | Hosted GigaChat, не self-hosted модель. |

---

## Положительные практики (что сделано хорошо)

1. **Параметризованные SQL-запросы** — asyncpg (`$1, $2`), aiosqlite (`?`) — нет SQL injection
2. **HTML-санитизация по whitelist** — `_sanitize_telegram_html()` экранирует опасные теги
3. **Валидация входных данных** — лимит длины сообщений (4096), лимит предпочтений (50 на юзера)
4. **Rate limiting с защитой памяти** — ThrottlingMiddleware с max_tracked_users, periodic cleanup
5. **Секреты только из .env** — pydantic-settings, .env в .gitignore
6. **Redis URL маскируется** — `_mask_url()` скрывает пароль в логах
7. **System prompt защищён** — секция безопасности + отказ раскрывать инструкции
8. **Изоляция сессий** — per-user lock, отдельные истории диалогов
9. **Duplicate call detection** — предотвращение зацикливания tool calls
10. **Graceful shutdown** — обработка SIGTERM/SIGINT, закрытие всех ресурсов
11. **Генерические ошибки** — пользователь видит "Произошла ошибка", не stack trace
12. **Комплексный test suite** — 315 security-тестов: SAST, AI Safety, Input Validation, Config

---

## Рекомендации по приоритету

### P0 — Исправить до релиза

Нет блокирующих находок.

### P1 — Исправить в ближайшем спринте

1. **Включить SSL-верификацию для GigaChat** — установить CA Минцифры в систему
   или дождаться поддержки в GigaChat SDK. Это единственный MEDIUM finding.

2. **Убрать `continue-on-error: true`** из security steps в CI — сделать security gate
   блокирующим для мёржа.

3. **Добавить pip-audit и bandit в CI** как отдельные шаги для раннего обнаружения CVE
   и регрессий безопасности.

### P2 — Бэклог

4. **Перенести stack trace на DEBUG уровень** — `mcp_client.py`, строки 267, 340.
5. **Добавить test_ai_safety.py и test_input_validation.py** в security-job CI.
6. **Настроить Ruff security rules** в pyproject.toml (`select = ["S", "B", ...]`).
7. **Отключить файловое логирование в production** — использовать только stdout для K8s.
8. **Рассмотреть Content Security Policy** для webhook-режима (если будет использоваться).

---

## Метрики безопасности

| Метрика | Значение | Цель |
|---------|:--------:|:----:|
| Bandit findings (High) | 0 | 0 |
| Ruff security findings | 0 | 0 |
| CVE в зависимостях (Critical/High) | 0 | 0 |
| OWASP Top 10 покрытие | 9/10 | 10/10 |
| OWASP LLM Top 10 покрытие | 8/8 (applicable) | 8/8 |
| Prompt Injection resistance | 100% (11/11 payloads) | 100% |
| Jailbreak resistance | 100% (9/9 payloads) | 100% |
| System prompt extraction resistance | 100% (12/12 payloads) | 100% |
| Security test suite | 315 passed, 5 xfailed | all pass |
| Захардкоженные секреты | 0 | 0 |
| Опасные функции (eval/exec/pickle) | 0 | 0 |
| SQL Injection vectors | 0 (parameterized) | 0 |
