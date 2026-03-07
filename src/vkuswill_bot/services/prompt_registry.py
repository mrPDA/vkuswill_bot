"""Централизованный реестр промптов с 4-tier fallback (ADR-007).

Приоритет загрузки:
  1. Langfuse Prompt Management (если подключён)
  2. Переменные окружения / Yandex Lockbox (через Config)
  3. Локальные файлы prompts/*.txt (gitignored)
  4. Минимальные fallback-stubs в коде

Промпты с бизнес-логикой НЕ хранятся в публичном репозитории.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_PROMPTS_DIR = _PROJECT_ROOT / "prompts"

_registry: PromptRegistry | None = None


@dataclass(frozen=True, slots=True)
class PromptResolution:
    """Resolved prompt text plus provenance metadata for observability."""

    name: str
    text: str
    source: str
    label: str | None = None
    version: str | None = None
    path: str | None = None

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "source": self.source,
            "sha256": self.sha256,
        }
        if self.label:
            payload["label"] = self.label
        if self.version:
            payload["version"] = self.version
        if self.path:
            payload["path"] = self.path
        return payload


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
        return self.resolve(name, **variables).text

    def resolve(self, name: str, /, **variables: str) -> PromptResolution:
        """Resolve prompt text and provenance metadata."""
        # Tier 1: Langfuse Prompt Management
        if self._langfuse is not None:
            try:
                prompt_obj = self._langfuse.get_prompt(
                    name,
                    label=self._label,
                    cache_ttl_seconds=self._cache_ttl,
                )
                text = prompt_obj.compile(**variables)
                version = str(
                    getattr(prompt_obj, "version", "")
                    or getattr(prompt_obj, "version_id", "")
                    or ""
                ).strip() or None
                return PromptResolution(
                    name=name,
                    text=text,
                    source="langfuse",
                    label=self._label,
                    version=version,
                )
            except Exception:
                logger.debug(
                    "Langfuse prompt '%s' (label=%s) unavailable, falling through",
                    name,
                    self._label,
                )

        # Tier 2: Environment override (Config / Lockbox)
        env_text = self._env_overrides.get(name)
        if env_text:
            return PromptResolution(
                name=name,
                text=_safe_format(env_text, variables),
                source="env",
                label=self._label,
            )

        # Tier 3: Local file (prompts/*.txt, gitignored)
        file_path, file_text = self._try_file(name)
        if file_text:
            return PromptResolution(
                name=name,
                text=_safe_format(file_text, variables),
                source="file",
                label=self._label,
                path=str(file_path) if file_path is not None else None,
            )

        # Tier 4: Code fallback stub
        stub = self._fallbacks.get(name, "")
        if stub:
            return PromptResolution(
                name=name,
                text=_safe_format(stub, variables),
                source="stub",
                label=self._label,
            )
        return PromptResolution(name=name, text="", source="missing", label=self._label)

    def describe(self, name: str, /, **variables: str) -> dict[str, Any]:
        """Return serializable resolution metadata for tracing/debugging."""
        return self.resolve(name, **variables).as_dict()

    def _try_file(self, name: str) -> tuple[Path | None, str | None]:
        """Attempt to load prompt from prompts/{name}.txt file."""
        filename = name.replace("-", "_") + ".txt"
        path = self._prompts_dir / filename
        if path.is_file():
            try:
                return path, path.read_text(encoding="utf-8").strip()
            except OSError:
                logger.warning("Failed to read prompt file %s", path)
        return None, None


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
