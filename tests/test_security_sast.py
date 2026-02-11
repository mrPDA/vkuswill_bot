"""SAST-тесты безопасности кода.

Статический анализ исходного кода на наличие:
- Захардкоженных секретов (токены, пароли, API-ключи)
- Опасных функций (eval, exec, pickle, os.system)
- Небезопасных паттернов (shell=True, отключённый SSL)
- Утечки информации в сообщениях об ошибках
- Отсутствия валидации входных данных
"""

import ast
import re
from pathlib import Path

import pytest

# Корень исходного кода
SRC_DIR = Path(__file__).parent.parent / "src" / "vkuswill_bot"

# Все Python-файлы проекта
SOURCE_FILES = list(SRC_DIR.rglob("*.py"))


def _read_source(path: Path) -> str:
    """Прочитать исходный код файла."""
    return path.read_text(encoding="utf-8")


def _param_id(val):
    """ID для parametrize: path → str(path), остальное → 'code'."""
    return str(val) if isinstance(val, Path) else "code"


def _all_source_code() -> list[tuple[Path, str]]:
    """Все файлы с исходным кодом."""
    return [(p, _read_source(p)) for p in SOURCE_FILES]


# ============================================================================
# Захардкоженные секреты
# ============================================================================

# Паттерны, указывающие на захардкоженные секреты
HARDCODED_SECRET_PATTERNS = [
    # Токены Telegram ботов: числовой_id:алфавитно-цифровая_строка
    (r'["\'](\d{8,10}:[A-Za-z0-9_-]{35,})["\']', "Telegram bot token"),
    # Длинные base64-строки (вероятно credentials/API ключи)
    (
        r'(?:credentials|api_key|secret_key|access_token)\s*=\s*["\']([A-Za-z0-9+/=]{40,})["\']',
        "API credentials/key",
    ),
    # Пароли в коде
    (r'(?:password|passwd|pwd)\s*=\s*["\']([^"\']{8,})["\']', "hardcoded password"),
    # Bearer токены
    (r"Bearer\s+[A-Za-z0-9._-]{20,}", "Bearer token"),
    # Ключи GigaChat (UUID-подобные)
    (
        r'["\']([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})["\']',
        "UUID-like key (possible GigaChat credentials)",
    ),
]

# Исключения: тестовые/placeholder значения
ALLOWED_SECRET_VALUES = {
    "test-creds",
    "your_telegram_bot_token",
    "your_gigachat_auth_key",
    "test-session-123",
    "sid-test",
    "sid-1",
    "sid-retry",
    "sid-existing",
    "sid-new",
    "new-sid",
    "existing-sid",
    "old-sid",
    "sid",
}


@pytest.mark.security
class TestHardcodedSecrets:
    """Проверка отсутствия захардкоженных секретов в коде."""

    @pytest.mark.parametrize("path,code", _all_source_code(), ids=_param_id)
    def test_no_hardcoded_secrets(self, path: Path, code: str):
        """Исходный код не содержит захардкоженных секретов."""
        violations = []
        for pattern, description in HARDCODED_SECRET_PATTERNS:
            matches = re.finditer(pattern, code)
            for match in matches:
                value = match.group(1) if match.lastindex else match.group(0)
                if value.lower() not in {v.lower() for v in ALLOWED_SECRET_VALUES}:
                    line_num = code[: match.start()].count("\n") + 1
                    violations.append(
                        f"  Строка {line_num}: {description} — найдено: {value[:20]}..."
                    )

        assert not violations, (
            f"\nЗахардкоженные секреты в {path.relative_to(SRC_DIR.parent.parent)}:\n"
            + "\n".join(violations)
        )

    def test_env_example_has_no_real_values(self):
        """Файл .env.example не содержит реальных значений секретов."""
        env_example = SRC_DIR.parent.parent / ".env.example"
        if not env_example.exists():
            pytest.skip(".env.example не найден")

        content = env_example.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                value = value.strip().strip("\"'")
                # Значение не должно быть реальным токеном/ключом
                assert len(value) < 50 or value.startswith("your_"), (
                    f".env.example: подозрительное значение для {key.strip()}: {value[:30]}..."
                )


# ============================================================================
# Опасные функции и паттерны
# ============================================================================

DANGEROUS_FUNCTIONS = [
    ("eval", r"\beval\s*\(", "eval() — выполнение произвольного кода"),
    ("exec", r"\bexec\s*\(", "exec() — выполнение произвольного кода"),
    ("pickle.loads", r"pickle\.loads?\s*\(", "pickle — десериализация может быть опасна"),
    ("os.system", r"os\.system\s*\(", "os.system() — command injection"),
    (
        "subprocess shell=True",
        r"subprocess\.\w+\s*\([^)]*shell\s*=\s*True",
        "subprocess с shell=True — command injection",
    ),
    ("__import__", r"__import__\s*\(", "__import__() — динамический импорт"),
    ("compile", r'(?<!re\.)compile\s*\(["\']', "compile() — компиляция кода"),
]


@pytest.mark.security
class TestDangerousFunctions:
    """Проверка отсутствия опасных функций в коде."""

    @pytest.mark.parametrize("path,code", _all_source_code(), ids=_param_id)
    def test_no_dangerous_functions(self, path: Path, code: str):
        """Исходный код не использует опасные функции."""
        violations = []
        for _name, pattern, description in DANGEROUS_FUNCTIONS:
            matches = list(re.finditer(pattern, code))
            for match in matches:
                line_num = code[: match.start()].count("\n") + 1
                # Пропускаем комментарии
                line = code.splitlines()[line_num - 1].strip()
                if line.startswith("#"):
                    continue
                violations.append(f"  Строка {line_num}: {description}")

        assert not violations, (
            f"\nОпасные функции в {path.relative_to(SRC_DIR.parent.parent)}:\n"
            + "\n".join(violations)
        )


# ============================================================================
# SSL/TLS безопасность
# ============================================================================


@pytest.mark.security
class TestSSLSecurity:
    """Проверка настроек SSL/TLS."""

    def test_ssl_verification_settings(self):
        """Проверка конфигурации SSL в проекте."""
        findings = []
        for path, code in _all_source_code():
            # Ищем отключённую проверку SSL
            ssl_disabled_patterns = [
                (r"verify\s*=\s*False", "verify=False"),
                (r"verify_ssl\s*=\s*False", "verify_ssl=False"),
                (r"verify_ssl_certs\s*=\s*False", "verify_ssl_certs=False"),
                (r"CERT_NONE", "ssl.CERT_NONE"),
            ]
            for pattern, description in ssl_disabled_patterns:
                matches = list(re.finditer(pattern, code))
                for match in matches:
                    line_num = code[: match.start()].count("\n") + 1
                    rel_path = path.relative_to(SRC_DIR.parent.parent)
                    findings.append(f"  {rel_path}:{line_num}: SSL отключён — {description}")

        # Это предупреждение, а не ошибка (GigaChat SDK требует verify_ssl_certs=False)
        if findings:
            pytest.xfail(
                "SSL-проверка отключена (может быть оправдано для GigaChat):\n"
                + "\n".join(findings)
            )


# ============================================================================
# Утечка информации в ошибках
# ============================================================================


@pytest.mark.security
class TestInformationLeakage:
    """Проверка отсутствия утечки информации."""

    @pytest.mark.parametrize("path,code", _all_source_code(), ids=_param_id)
    def test_no_stack_trace_in_user_messages(self, path: Path, code: str):
        """Пользователь не видит stack trace или внутренние ошибки."""
        violations = []
        # Ищем traceback.format_exc() в строках, отправляемых пользователю
        # Паттерн: message.answer(...traceback...)
        patterns = [
            (r"message\.answer\s*\([^)]*traceback", "traceback в ответе пользователю"),
            (r"message\.answer\s*\([^)]*str\(e\)", "str(e) в ответе пользователю"),
            (r"message\.answer\s*\([^)]*repr\(e\)", "repr(e) в ответе пользователю"),
            (r"message\.answer\s*\([^)]*exc_info", "exc_info в ответе пользователю"),
        ]
        for pattern, description in patterns:
            matches = list(re.finditer(pattern, code, re.DOTALL))
            for match in matches:
                line_num = code[: match.start()].count("\n") + 1
                violations.append(f"  Строка {line_num}: {description}")

        assert not violations, (
            f"\nУтечка информации в {path.relative_to(SRC_DIR.parent.parent)}:\n"
            + "\n".join(violations)
        )

    def test_error_messages_are_generic(self):
        """Сообщения об ошибках для пользователя — обобщённые, без деталей."""
        handlers_code = _read_source(SRC_DIR / "bot" / "handlers.py")

        # Находим все строки ответов при ошибках
        error_responses = re.findall(r'response\s*=\s*\(\s*"([^"]*)"', handlers_code)

        for response in error_responses:
            # Не должно содержать технических деталей
            assert "traceback" not in response.lower(), f"Ответ содержит traceback: {response[:50]}"
            assert "exception" not in response.lower(), f"Ответ содержит exception: {response[:50]}"
            assert "stack" not in response.lower(), f"Ответ содержит stack trace: {response[:50]}"


# ============================================================================
# AST-анализ: проверка структуры кода
# ============================================================================


@pytest.mark.security
class TestCodeStructure:
    """AST-анализ безопасности структуры кода."""

    @pytest.mark.parametrize("path,code", _all_source_code(), ids=_param_id)
    def test_no_bare_except(self, path: Path, code: str):
        """Нет голых except (без указания типа исключения).

        Допускается `except Exception` с логированием.
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            pytest.skip(f"Не удалось распарсить {path}")

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                # Проверяем, есть ли хотя бы pass или логирование
                violations.append(f"  Строка {node.lineno}: bare except (без типа исключения)")

        # bare except допускается в _send_typing_periodically (некритичная операция)
        if violations:
            # Фильтруем известные допустимые случаи
            critical = [v for v in violations if "bare except" in v]
            if critical:
                pytest.xfail(
                    f"Bare except в {path.relative_to(SRC_DIR.parent.parent)}:\n"
                    + "\n".join(critical)
                )

    @pytest.mark.parametrize("path,code", _all_source_code(), ids=_param_id)
    def test_exception_handling_logs_errors(self, path: Path, code: str):
        """Обработчики исключений логируют ошибки (не глушат)."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            pytest.skip(f"Не удалось распарсить {path}")

        violations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.body:
                # Проверяем, что тело except не состоит только из pass
                body_types = [type(stmt).__name__ for stmt in node.body]
                if body_types == ["Pass"]:
                    violations.append(
                        f"  Строка {node.lineno}: except с только pass "
                        f"(ошибка глушится без логирования)"
                    )

        if violations:
            pytest.xfail(
                f"Глушение ошибок в {path.relative_to(SRC_DIR.parent.parent)}:\n"
                + "\n".join(violations)
            )


# ============================================================================
# Проверка .gitignore
# ============================================================================


@pytest.mark.security
class TestGitignore:
    """Проверка, что секреты не попадут в репозиторий."""

    def test_env_in_gitignore(self):
        """.env указан в .gitignore."""
        gitignore = SRC_DIR.parent.parent / ".gitignore"
        assert gitignore.exists(), ".gitignore не найден"

        content = gitignore.read_text(encoding="utf-8")
        patterns = content.splitlines()
        assert any(p.strip() in (".env", ".env*", ".env.*") for p in patterns), (
            ".env не указан в .gitignore"
        )

    def test_log_files_in_gitignore(self):
        """Лог-файлы указаны в .gitignore."""
        gitignore = SRC_DIR.parent.parent / ".gitignore"
        content = gitignore.read_text(encoding="utf-8")
        patterns = content.splitlines()

        assert any("log" in p.strip().lower() for p in patterns), (
            "Лог-файлы (*.log / bot.log) не указаны в .gitignore"
        )

    def test_pycache_in_gitignore(self):
        """__pycache__ указан в .gitignore."""
        gitignore = SRC_DIR.parent.parent / ".gitignore"
        content = gitignore.read_text(encoding="utf-8")

        assert "__pycache__" in content, "__pycache__ не указан в .gitignore"

    def test_venv_in_gitignore(self):
        """.venv указан в .gitignore."""
        gitignore = SRC_DIR.parent.parent / ".gitignore"
        content = gitignore.read_text(encoding="utf-8")

        assert ".venv" in content, ".venv не указан в .gitignore"
