"""Тесты PromptRegistry — 4-tier fallback загрузки промптов (ADR-007)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vkuswill_bot.services.prompt_registry import (
    PromptRegistry,
    get_registry,
    init_registry,
    PromptResolution,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset module-level singleton before/after each test."""
    reset_registry()
    yield
    reset_registry()


class _FakePromptObj:
    """Imitate Langfuse prompt object returned by get_prompt()."""

    def __init__(self, text: str) -> None:
        self.prompt = text

    def compile(self, **variables: str) -> str:
        result = self.prompt
        for key, value in variables.items():
            result = result.replace("{{" + key + "}}", str(value))
        return result


def _make_langfuse_mock(prompts: dict[str, str] | None = None) -> MagicMock:
    """Create a mock Langfuse client with get_prompt()."""
    client = MagicMock()
    store = prompts or {}

    def fake_get_prompt(name: str, **_kwargs: object) -> _FakePromptObj:
        if name in store:
            return _FakePromptObj(store[name])
        raise ValueError(f"Prompt '{name}' not found")

    client.get_prompt.side_effect = fake_get_prompt
    return client


# ============================================================================
# Tier 4: Fallback stubs (no Langfuse, no env, no file)
# ============================================================================


class TestFallbackStubs:
    def test_returns_registered_stub(self):
        registry = PromptRegistry()
        registry.register_fallback("test-prompt", "Hello world")
        assert registry.get("test-prompt") == "Hello world"

    def test_returns_empty_for_unknown(self):
        registry = PromptRegistry()
        assert registry.get("nonexistent") == ""

    def test_register_fallbacks_bulk(self):
        registry = PromptRegistry()
        registry.register_fallbacks({"a": "alpha", "b": "beta"})
        assert registry.get("a") == "alpha"
        assert registry.get("b") == "beta"

    def test_variable_substitution_in_stub(self):
        registry = PromptRegistry()
        registry.register_fallback("greet", "Hello {name}, you are {role}")
        assert registry.get("greet", name="Alice", role="admin") == "Hello Alice, you are admin"

    def test_variable_substitution_graceful_on_missing_key(self):
        registry = PromptRegistry()
        registry.register_fallback("greet", "Hello {name}")
        result = registry.get("greet", wrong_key="value")
        assert result == "Hello {name}"

    def test_registered_names(self):
        registry = PromptRegistry()
        registry.register_fallbacks({"x": "X", "y": "Y"})
        assert registry.registered_names == frozenset({"x", "y"})


# ============================================================================
# Tier 3: File-based prompts
# ============================================================================


class TestFilePrompts:
    def test_loads_from_file(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system_prompt.txt").write_text("File prompt content", encoding="utf-8")

        registry = PromptRegistry(prompts_dir=prompts_dir)
        registry.register_fallback("system-prompt", "Stub content")

        assert registry.get("system-prompt") == "File prompt content"

    def test_file_overrides_stub(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "profile_core.txt").write_text("From file", encoding="utf-8")

        registry = PromptRegistry(prompts_dir=prompts_dir)
        registry.register_fallback("profile-core", "From stub")

        assert registry.get("profile-core") == "From file"

    def test_falls_through_when_no_file(self, tmp_path: Path):
        registry = PromptRegistry(prompts_dir=tmp_path)
        registry.register_fallback("missing", "Stub")
        assert registry.get("missing") == "Stub"

    def test_name_to_filename_mapping(self, tmp_path: Path):
        """Dashes in name are converted to underscores for filename."""
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "classify_intent.txt").write_text("Classify text", encoding="utf-8")

        registry = PromptRegistry(prompts_dir=prompts_dir)
        assert registry.get("classify-intent") == "Classify text"

    def test_file_with_variables(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "recipe.txt").write_text("Cook {dish} for {servings}", encoding="utf-8")

        registry = PromptRegistry(prompts_dir=prompts_dir)
        result = registry.get("recipe", dish="борщ", servings="4")
        assert result == "Cook борщ for 4"


# ============================================================================
# Tier 2: Environment overrides
# ============================================================================


class TestEnvOverrides:
    def test_env_overrides_file_and_stub(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system_prompt.txt").write_text("From file", encoding="utf-8")

        registry = PromptRegistry(
            env_overrides={"system-prompt": "From env"},
            prompts_dir=prompts_dir,
        )
        registry.register_fallback("system-prompt", "From stub")

        assert registry.get("system-prompt") == "From env"

    def test_env_with_variables(self):
        registry = PromptRegistry(
            env_overrides={"greet": "Hi {name}!"},
        )
        assert registry.get("greet", name="Bob") == "Hi Bob!"


# ============================================================================
# Tier 1: Langfuse Prompt Management
# ============================================================================


class TestLangfuseIntegration:
    def test_langfuse_takes_precedence(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "system_prompt.txt").write_text("From file", encoding="utf-8")

        langfuse = _make_langfuse_mock({"system-prompt": "From Langfuse"})
        registry = PromptRegistry(
            langfuse_client=langfuse,
            env_overrides={"system-prompt": "From env"},
            prompts_dir=prompts_dir,
        )
        registry.register_fallback("system-prompt", "From stub")

        assert registry.get("system-prompt") == "From Langfuse"

    def test_langfuse_with_variables(self):
        langfuse = _make_langfuse_mock({"greet": "Hello {{name}} from Langfuse"})
        registry = PromptRegistry(langfuse_client=langfuse)
        assert registry.get("greet", name="Alice") == "Hello Alice from Langfuse"

    def test_langfuse_error_falls_through_to_env(self):
        langfuse = _make_langfuse_mock({})
        registry = PromptRegistry(
            langfuse_client=langfuse,
            env_overrides={"test": "From env"},
        )
        assert registry.get("test") == "From env"

    def test_langfuse_error_falls_through_to_stub(self):
        langfuse = _make_langfuse_mock({})
        registry = PromptRegistry(langfuse_client=langfuse)
        registry.register_fallback("test", "From stub")
        assert registry.get("test") == "From stub"

    def test_langfuse_exception_is_caught(self):
        langfuse = MagicMock()
        langfuse.get_prompt.side_effect = RuntimeError("Network error")

        registry = PromptRegistry(langfuse_client=langfuse)
        registry.register_fallback("test", "Fallback")

        assert registry.get("test") == "Fallback"

    def test_langfuse_none_skips_tier1(self):
        registry = PromptRegistry(langfuse_client=None)
        registry.register_fallback("test", "Stub")
        assert registry.get("test") == "Stub"

    def test_langfuse_passes_label_to_get_prompt(self):
        langfuse = _make_langfuse_mock({"test": "From Langfuse"})
        registry = PromptRegistry(langfuse_client=langfuse, label="staging")
        registry.get("test")

        langfuse.get_prompt.assert_called_once_with(
            "test",
            label="staging",
            cache_ttl_seconds=300,
        )

    def test_label_defaults_to_production(self):
        langfuse = _make_langfuse_mock({"test": "From Langfuse"})
        registry = PromptRegistry(langfuse_client=langfuse)
        registry.get("test")

        langfuse.get_prompt.assert_called_once_with(
            "test",
            label="production",
            cache_ttl_seconds=300,
        )

    def test_resolve_returns_provenance_metadata(self):
        langfuse = _make_langfuse_mock({"test": "From Langfuse"})
        registry = PromptRegistry(langfuse_client=langfuse, label="staging")

        resolution = registry.resolve("test")

        assert isinstance(resolution, PromptResolution)
        assert resolution.text == "From Langfuse"
        assert resolution.source == "langfuse"
        assert resolution.label == "staging"
        assert resolution.as_dict()["sha256"]


# ============================================================================
# Module-level singleton API
# ============================================================================


class TestSingletonAPI:
    def test_get_registry_returns_none_before_init(self):
        assert get_registry() is None

    def test_init_registry_creates_singleton(self):
        reg = init_registry(cache_ttl_seconds=60)
        assert get_registry() is reg

    def test_init_registry_replaces_previous(self):
        reg1 = init_registry(cache_ttl_seconds=60)
        reg2 = init_registry(cache_ttl_seconds=120)
        assert get_registry() is reg2
        assert reg1 is not reg2

    def test_reset_registry_clears_singleton(self):
        init_registry()
        reset_registry()
        assert get_registry() is None

    def test_init_with_langfuse_and_overrides(self):
        langfuse = _make_langfuse_mock({"a": "langfuse-a"})
        reg = init_registry(
            langfuse_client=langfuse,
            env_overrides={"b": "env-b"},
            cache_ttl_seconds=120,
        )
        reg.register_fallback("c", "stub-c")

        assert reg.get("a") == "langfuse-a"
        assert reg.get("b") == "env-b"
        assert reg.get("c") == "stub-c"


# ============================================================================
# Full 4-tier cascade
# ============================================================================


class TestFullCascade:
    """Verify each tier correctly shadows the lower tiers."""

    def test_all_tiers_present_langfuse_wins(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.txt").write_text("File", encoding="utf-8")

        langfuse = _make_langfuse_mock({"test": "Langfuse"})
        registry = PromptRegistry(
            langfuse_client=langfuse,
            env_overrides={"test": "Env"},
            prompts_dir=prompts_dir,
        )
        registry.register_fallback("test", "Stub")

        assert registry.get("test") == "Langfuse"

    def test_no_langfuse_env_wins(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.txt").write_text("File", encoding="utf-8")

        registry = PromptRegistry(
            env_overrides={"test": "Env"},
            prompts_dir=prompts_dir,
        )
        registry.register_fallback("test", "Stub")

        assert registry.get("test") == "Env"

    def test_no_langfuse_no_env_file_wins(self, tmp_path: Path):
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.txt").write_text("File", encoding="utf-8")

        registry = PromptRegistry(prompts_dir=prompts_dir)
        registry.register_fallback("test", "Stub")

        assert registry.get("test") == "File"

    def test_nothing_returns_stub(self):
        registry = PromptRegistry()
        registry.register_fallback("test", "Stub")
        assert registry.get("test") == "Stub"

    def test_nothing_returns_empty(self):
        registry = PromptRegistry()
        assert registry.get("unknown") == ""
