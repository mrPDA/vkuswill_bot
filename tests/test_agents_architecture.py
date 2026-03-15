"""Архитектурные guard-тесты для `vkuswill_bot.agents`."""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path


AGENTS_DIR = Path(__file__).resolve().parents[1] / "src" / "vkuswill_bot" / "agents"

LAYER_BY_MODULE: dict[str, str] = {
    # Runtime
    "shopping_agent": "runtime",
    "shopping_turn_executor": "runtime",
    "shopping_turn_diagnostics": "shared",
    "shopping_turn_contracts": "runtime",
    "shopping_turn_types": "runtime",
    "shopping_tool_step_processor": "runtime",
    "shopping_tool_runtime_ops": "runtime",
    "shopping_tool_recovery": "runtime",
    "shopping_final_response_builder": "runtime",
    "shopping_agent_runtime_mixin": "runtime",
    "shopping_agent_service_mixin": "runtime",
    "shopping_agent_state_ops": "runtime",
    "meal_plan_executor": "runtime",
    "multi_course_executor": "runtime",
    "meal_plan_execution_helpers": "runtime",
    "meal_plan_trace_ops": "runtime",
    "meal_plan_recipe_search_ops": "runtime",
    "meal_plan_cart_ops": "runtime",
    "meal_plan_day_search_ops": "runtime",
    "meal_plan_phase2_ops": "runtime",
    "meal_plan_ingredient_collection": "runtime",
    "mcp_tool_gateway": "runtime",
    # Policy
    "prompt_helpers": "policy",
    "meal_plan_response_contract": "policy",
    "meal_plan_response_contract_builder": "policy",
    "meal_plan_response_contract_model": "policy",
    "meal_plan_response_utils": "policy",
    "meal_plan_prompt_context": "policy",
    "shopping_turn_message_ops": "policy",
    "meal_plan_runtime_policy": "policy",
    "response_analysis": "policy",
    "recovery_policy": "policy",
    "recovery_hints": "policy",
    "intent_markers": "policy",
    "intent_classifier": "policy",
    # Domain
    "recipe_pantry": "domain",
    "recipe_parsing": "domain",
    "recipe_matching": "domain",
    "recipe_runtime": "domain",
    "recipe_batch_fallback": "domain",
    "recipe_quantity_base": "domain",
    "recipe_search_vocabulary": "domain",
    "recipe_quantity_rules": "domain",
    "recipe_quantity_calculator": "domain",
    "recipe_cart_recovery": "domain",
    "meal_plan_types": "domain",
    "meal_plan_request_profile": "domain",
    "meal_plan_generator": "domain",
    "meal_plan_quality": "domain",
    "meal_plan_hard_constraints": "domain",
    "meal_plan_runtime_ops": "domain",
    "recipe_fallback": "domain",
    "product_index_manager": "domain",
    "cart_price_builder": "domain",
    "cart_output_renderer": "domain",
    # IO
    "tool_preprocessor": "io",
    "tool_preprocessor_cart": "io",
    "tool_preprocessor_search": "io",
    "mcp_response_parser": "io",
    "mcp_helpers": "io",
    # Shared
    "pantry_tags": "shared",
    "quantity_utils": "shared",
    "tool_result_compact_strategies": "shared",
    "tool_value_utils": "shared",
    "tool_result_compactor": "shared",
    "history_manager": "shared",
    "llm_helpers": "shared",
    "exceptions": "shared",
}

_LAYER_ORDER: dict[str, int] = {
    "runtime": 4,
    "policy": 3,
    "domain": 2,
    "io": 1,
    "shared": 0,
}

EXPECTED_LAYER_VIOLATIONS: set[tuple[str, str]] = set()
MAX_OUTDEGREE_ALLOWED = 8
MAX_MODULE_LOC_ALLOWED = 460  # recipe_search_vocabulary ~458 (data-only)


@lru_cache(maxsize=1)
def _agent_modules() -> dict[str, Path]:
    return {path.stem: path for path in AGENTS_DIR.glob("*.py") if path.name != "__init__.py"}


def _extract_internal_imports(module_name: str, module_path: Path) -> set[str]:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    internal_modules = set(_agent_modules())
    imports: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                dotted = alias.name
                if dotted.startswith("vkuswill_bot.agents."):
                    target = dotted.split(".")[-1]
                    if target in internal_modules and target != module_name:
                        imports.add(target)
        if isinstance(node, ast.ImportFrom):
            module = node.module
            if module and module.startswith("vkuswill_bot.agents."):
                target = module.split(".")[-1]
                if target in internal_modules and target != module_name:
                    imports.add(target)
                continue
            if module == "vkuswill_bot.agents":
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
    for module_name, module_path in _agent_modules().items():
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


def test_layer_mapping_covers_all_agent_modules() -> None:
    modules = set(_agent_modules())
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
    modules = _agent_modules()
    max_loc = 0
    for path in modules.values():
        with path.open("r", encoding="utf-8") as f:
            loc = sum(1 for _ in f)
        if loc > max_loc:
            max_loc = loc
    assert max_loc <= MAX_MODULE_LOC_ALLOWED
