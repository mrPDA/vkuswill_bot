"""Централизованный реестр промптов с 4-tier fallback (ADR-007).

Приоритет загрузки:
  1. Langfuse Prompt Management (если подключён)
  2. Переменные окружения / Yandex Lockbox (через Config)
  3. Локальные файлы prompts/*.txt (gitignored)
  4. Минимальные fallback-stubs в коде

Промпты с бизнес-логикой НЕ хранятся в публичном репозитории.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PROMPTS_DIR = _PROJECT_ROOT / "prompts"

_registry: PromptRegistry | None = None


def _safe_format(text: str, variables: dict[str, str]) -> str:
    """Apply Python str.format() with graceful fallback on errors."""
    if not variables:
        return text
    try:
        return text.format(**variables)
    except (KeyError, IndexError, ValueError):
        return text


class PromptRegistry:
    """Централизованный реестр промптов с 4-tier fallback.

    Tier 1: Langfuse Prompt Management (web UI, versioning, A/B)
    Tier 2: ENV / Yandex Lockbox (переопределение через env vars)
    Tier 3: Локальные файлы prompts/*.txt (gitignored)
    Tier 4: Код-stubs (минимальные, видны в публичном репо)
    """

    def __init__(
        self,
        *,
        langfuse_client: Any | None = None,
        cache_ttl_seconds: int = 300,
        label: str = "production",
        env_overrides: dict[str, str] | None = None,
        prompts_dir: Path | None = None,
    ) -> None:
        self._langfuse = langfuse_client
        self._cache_ttl = cache_ttl_seconds
        self._label = label
        self._env_overrides: dict[str, str] = env_overrides or {}
        self._prompts_dir = prompts_dir or _DEFAULT_PROMPTS_DIR
        self._fallbacks: dict[str, str] = {}

    def register_fallback(self, name: str, fallback: str) -> None:
        """Register a code-level fallback stub for a prompt."""
        self._fallbacks[name] = fallback

    def register_fallbacks(self, mapping: dict[str, str]) -> None:
        """Register multiple fallback stubs at once."""
        self._fallbacks.update(mapping)

    @property
    def registered_names(self) -> frozenset[str]:
        """All registered prompt names."""
        return frozenset(self._fallbacks)

    def get(self, name: str, /, **variables: str) -> str:
        """Get prompt text with 4-tier fallback.

        Variables are substituted via Langfuse compile() (``{{var}}``)
        or Python str.format() (``{var}``) for env/file/stub tiers.
        """
        # Tier 1: Langfuse Prompt Management
        if self._langfuse is not None:
            try:
                prompt_obj = self._langfuse.get_prompt(
                    name,
                    label=self._label,
                    cache_ttl_seconds=self._cache_ttl,
                )
                return prompt_obj.compile(**variables)
            except Exception:
                logger.debug(
                    "Langfuse prompt '%s' (label=%s) unavailable, falling through",
                    name,
                    self._label,
                )

        # Tier 2: Environment override (Config / Lockbox)
        env_text = self._env_overrides.get(name)
        if env_text:
            return _safe_format(env_text, variables)

        # Tier 3: Local file (prompts/*.txt, gitignored)
        file_text = self._try_file(name)
        if file_text:
            return _safe_format(file_text, variables)

        # Tier 4: Code fallback stub
        stub = self._fallbacks.get(name, "")
        return _safe_format(stub, variables) if stub else ""

    def _try_file(self, name: str) -> str | None:
        """Attempt to load prompt from prompts/{name}.txt file."""
        filename = name.replace("-", "_") + ".txt"
        path = self._prompts_dir / filename
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                logger.warning("Failed to read prompt file %s", path)
        return None


def init_registry(
    *,
    langfuse_client: Any | None = None,
    cache_ttl_seconds: int = 300,
    label: str = "production",
    env_overrides: dict[str, str] | None = None,
    prompts_dir: Path | None = None,
) -> PromptRegistry:
    """Initialize the module-level prompt registry singleton."""
    global _registry
    _registry = PromptRegistry(
        langfuse_client=langfuse_client,
        cache_ttl_seconds=cache_ttl_seconds,
        label=label,
        env_overrides=env_overrides,
        prompts_dir=prompts_dir,
    )
    return _registry


def get_registry() -> PromptRegistry | None:
    """Get the current prompt registry (None if not initialized)."""
    return _registry


def reset_registry() -> None:
    """Reset the registry to None (for testing)."""
    global _registry
    _registry = None
