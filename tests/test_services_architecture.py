"""Архитектурные guard-тесты для `vkuswill_bot.services`."""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path


SERVICES_DIR = Path(__file__).resolve().parents[1] / "src" / "vkuswill_bot" / "services"

LAYER_BY_MODULE: dict[str, str] = {
    # Runtime
    "chat_engine_factory": "runtime",
    "stats_aggregator": "runtime",
    "tool_executor": "runtime",
    "tool_executor_pipeline": "runtime",
    "voice_link_api": "runtime",
    # Domain
    "cart_processor": "domain",
    "dialog_manager": "domain",
    "nutrition_service": "domain",
    "preferences_parser": "domain",
    "recipe_search": "domain",
    "search_processor": "domain",
    # IO
    "cart_snapshot_store": "io",
    "mcp_client": "io",
    "migration_runner": "io",
    "preferences_store": "io",
    "price_cache": "io",
    "recipe_store": "io",
    "redis_client": "io",
    "redis_dialog_manager": "io",
    "s3_log_handler": "io",
    "user_store": "io",
    # Shared
    "chat_engine": "shared",
    "dialog_history_utils": "shared",
    "dialog_types": "shared",
    "langfuse_tracing": "shared",
    "llm_adapters": "shared",
    "pii_utils": "shared",
    "prompts": "shared",
    "tool_input_normalizers": "shared",
}

_LAYER_ORDER: dict[str, int] = {
    "runtime": 3,
    "domain": 2,
    "io": 1,
    "shared": 0,
}

EXPECTED_LAYER_VIOLATIONS: set[tuple[str, str]] = set()
MAX_OUTDEGREE_ALLOWED = 11  # после Phase 4 (migration) → 8 (ADR-006)
MAX_MODULE_LOC_ALLOWED = 750  # после Phase 4 (legacy removal) → 600 (ADR-006)


@lru_cache(maxsize=1)
def _service_modules() -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in SERVICES_DIR.glob("*.py"):
        if path.name != "__init__.py":
            result[path.stem] = path
    for d in SERVICES_DIR.iterdir():
        if d.is_dir() and (d / "__init__.py").exists():
            result[d.name] = d / "__init__.py"
    return result


def _extract_internal_imports(module_name: str, module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    internal_modules = set(_service_modules())
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                dotted = alias.name
                if dotted.startswith("vkuswill_bot.services."):
                    target = dotted.split(".")[-1]
                    if target in internal_modules and target != module_name:
                        imports.add(target)
        if isinstance(node, ast.ImportFrom):
            module = node.module
            if module and module.startswith("vkuswill_bot.services."):
                target = module.split(".")[-1]
                if target in internal_modules and target != module_name:
                    imports.add(target)
                continue
            if module == "vkuswill_bot.services":
                for alias in node.names:
                    target = alias.name.split(".")[0]
                    if target in internal_modules and target != module_name:
                        imports.add(target)
                continue
            if node.level >= 1:
                if module:
                    target = module.split(".")[-1]
                    if target in internal_modules and target != module_name:
                        imports.add(target)
                else:
                    for alias in node.names:
                        target = alias.name.split(".")[0]
                        if target in internal_modules and target != module_name:
                            imports.add(target)

    return imports


@lru_cache(maxsize=1)
def _collect_import_graph() -> dict[str, set[str]]:
    graph: dict[str, set[str]] = {}
    for module_name, module_path in _service_modules().items():
        graph[module_name] = _extract_internal_imports(module_name, module_path)
    return graph


def _collect_layer_violations() -> set[tuple[str, str]]:
    violations: set[tuple[str, str]] = set()
    graph = _collect_import_graph()
    for source, targets in graph.items():
        src_layer = LAYER_BY_MODULE[source]
        src_rank = _LAYER_ORDER[src_layer]
        for target in targets:
            target_layer = LAYER_BY_MODULE[target]
            target_rank = _LAYER_ORDER[target_layer]
            if src_rank < target_rank:
                violations.add((source, target))
    return violations


def test_layer_mapping_covers_all_services_modules() -> None:
    modules = set(_service_modules())
    mapping_modules = set(LAYER_BY_MODULE)
    assert mapping_modules == modules


def test_layer_violations_match_baseline() -> None:
    actual = _collect_layer_violations()
    assert actual == EXPECTED_LAYER_VIOLATIONS


def test_max_module_outdegree_does_not_regress() -> None:
    graph = _collect_import_graph()
    max_outdegree = max(len(targets) for targets in graph.values())
    assert max_outdegree <= MAX_OUTDEGREE_ALLOWED


def test_max_module_loc_does_not_regress() -> None:
    modules = _service_modules()
    max_loc = max(sum(1 for _ in path.open("r", encoding="utf-8")) for path in modules.values())
    assert max_loc <= MAX_MODULE_LOC_ALLOWED
