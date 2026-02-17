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


def _rel_path(path: Path) -> str:
    """Путь файла относительно корня репозитория."""
    return str(path.relative_to(SRC_DIR.parent.parent))


_USER_MESSAGE_METHODS = frozenset({"answer", "edit_text"})
_BROAD_EXCEPT_NAMES = frozenset({"Exception", "BaseException"})
_LEAK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\btraceback\b"), "traceback в ответе пользователю"),
    (re.compile(r"\bformat_exc\s*\("), "traceback.format_exc() в ответе пользователю"),
    (re.compile(r"\bexc_info\b"), "exc_info в ответе пользователю"),
    (
        re.compile(r"\b(?:str|repr)\s*\(\s*(?:e|exc|error|exception)\s*\)"),
        "str()/repr() от исключения в ответе пользователю",
    ),
    (
        re.compile(r"\{[^}]*\b(?:e|exc|error|exception)\b[^}]*\}"),
        "f-string с переменной исключения в ответе пользователю",
    ),
]

# Допустимые и документированные исключения SSL (точечный allowlist).
_SSL_FALSE_ALLOWLIST = {
    ("src/vkuswill_bot/services/gigachat_service.py", "verify_ssl", 114),
}


def _iter_user_message_text_expr(
    tree: ast.AST,
    code: str,
) -> list[tuple[int, str, ast.AST]]:
    """Найти выражения текста, отправляемого пользователю.

    Ищем вызовы ``*.answer(...)`` и ``*.edit_text(...)``.
    """
    result: list[tuple[int, str, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in _USER_MESSAGE_METHODS:
            continue

        text_arg: ast.AST | None = None
        if node.args:
            text_arg = node.args[0]
        else:
            for kw in node.keywords:
                if kw.arg == "text":
                    text_arg = kw.value
                    break

        if text_arg is None:
            continue

        expr = ast.get_source_segment(code, text_arg) or ast.unparse(text_arg)
        result.append((node.lineno, expr, text_arg))

    return result


def _extract_string_literals(node: ast.AST) -> list[str]:
    """Извлечь строковые литералы из выражения."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [
            part.value
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        ]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _extract_string_literals(node.left) + _extract_string_literals(node.right)
    return []


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    """True для bare except и except Exception/BaseException."""
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in _BROAD_EXCEPT_NAMES
    if isinstance(handler.type, ast.Tuple):
        names = {elt.id for elt in handler.type.elts if isinstance(elt, ast.Name)}
        return bool(names & _BROAD_EXCEPT_NAMES)
    return False


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
        """Проверка отключения SSL через AST (без ложных срабатываний regex)."""
        findings = []
        for path, code in _all_source_code():
            try:
                tree = ast.parse(code)
            except SyntaxError:
                pytest.skip(f"Не удалось распарсить {path}")

            rel_path = _rel_path(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                    if node.value.value is not False:
                        continue
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id in {
                            "verify",
                            "verify_ssl",
                            "verify_ssl_certs",
                        }:
                            key = (rel_path, target.id, node.lineno)
                            if key not in _SSL_FALSE_ALLOWLIST:
                                findings.append(
                                    f"  {rel_path}:{node.lineno}: SSL отключён — {target.id}=False"
                                )

                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if (
                            kw.arg in {"verify", "verify_ssl", "verify_ssl_certs"}
                            and isinstance(kw.value, ast.Constant)
                            and kw.value.value is False
                        ):
                            findings.append(
                                f"  {rel_path}:{node.lineno}: SSL отключён — {kw.arg}=False"
                            )

                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "ssl"
                    and node.attr == "CERT_NONE"
                ):
                    findings.append(
                        f"  {rel_path}:{node.lineno}: SSL отключён — ssl.CERT_NONE"
                    )

        assert not findings, "SSL-проверка отключена:\n" + "\n".join(findings)


# ============================================================================
# Утечка информации в ошибках
# ============================================================================


@pytest.mark.security
class TestInformationLeakage:
    """Проверка отсутствия утечки информации."""

    @pytest.mark.parametrize("path,code", _all_source_code(), ids=_param_id)
    def test_no_stack_trace_in_user_messages(self, path: Path, code: str):
        """Пользователь не видит traceback/exception details в ответах."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            pytest.skip(f"Не удалось распарсить {path}")

        violations = []
        for line_num, text_expr, _node in _iter_user_message_text_expr(tree, code):
            for pattern, description in _LEAK_PATTERNS:
                if pattern.search(text_expr):
                    violations.append(f"  Строка {line_num}: {description}")

        assert not violations, (
            f"\nУтечка информации в {_rel_path(path)}:\n"
            + "\n".join(violations)
        )

    def test_error_messages_are_generic(self):
        """Литералы в пользовательских сообщениях не содержат тех-деталей."""
        handlers_path = SRC_DIR / "bot" / "handlers.py"
        handlers_code = _read_source(handlers_path)
        tree = ast.parse(handlers_code)

        violations = []
        banned_terms = ("traceback", "exception", "stack trace", "exc_info")
        for line_num, _expr, text_node in _iter_user_message_text_expr(tree, handlers_code):
            for literal in _extract_string_literals(text_node):
                lowered = literal.lower()
                bad = next((term for term in banned_terms if term in lowered), None)
                if bad:
                    violations.append(
                        f"  Строка {line_num}: найдено '{bad}' в пользовательском сообщении"
                    )

        assert not violations, (
            f"\nНеобобщённые сообщения в {_rel_path(handlers_path)}:\n" + "\n".join(violations)
        )


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
                violations.append(f"  Строка {node.lineno}: bare except (без типа исключения)")

        assert not violations, (
            f"Bare except в {_rel_path(path)}:\n" + "\n".join(violations)
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
            if not isinstance(node, ast.ExceptHandler):
                continue
            if not node.body or not _is_broad_except(node):
                continue
            if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                violations.append(
                    f"  Строка {node.lineno}: broad except с только pass "
                    f"(ошибка глушится без логирования)"
                )

        assert not violations, (
            f"Глушение ошибок в {_rel_path(path)}:\n" + "\n".join(violations)
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
