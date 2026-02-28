#!/usr/bin/env python3
"""Collect agents architecture KPI and export report/badge artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _module_loc(path: Path) -> int:
    return sum(1 for _ in path.open("r", encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_badge(*, label: str, message: str, color: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "label": label,
        "message": message,
        "color": color,
    }


def _collect_kpi() -> dict[str, Any]:
    from tests import test_agents_architecture as arch

    modules = arch._agent_modules()
    graph = arch._collect_import_graph()
    violations = sorted(
        f"{source}->{target}" for source, target in arch._collect_layer_violations()
    )

    outdegree_by_module = {module: len(targets) for module, targets in graph.items()}
    max_outdegree = max(outdegree_by_module.values(), default=0)
    worst_outdegree_modules = sorted(
        module for module, outdegree in outdegree_by_module.items() if outdegree == max_outdegree
    )

    loc_by_module = {module: _module_loc(path) for module, path in modules.items()}
    max_loc = max(loc_by_module.values(), default=0)
    worst_loc_modules = sorted(module for module, loc in loc_by_module.items() if loc == max_loc)

    limits = {
        "layer_violations": len(arch.EXPECTED_LAYER_VIOLATIONS),
        "max_outdegree": arch.MAX_OUTDEGREE_ALLOWED,
        "max_module_loc": arch.MAX_MODULE_LOC_ALLOWED,
    }
    metrics = {
        "layer_violations": len(violations),
        "max_outdegree": max_outdegree,
        "max_module_loc": max_loc,
        "module_count": len(modules),
    }

    checks = {
        "layer_violations_ok": metrics["layer_violations"] <= limits["layer_violations"],
        "max_outdegree_ok": metrics["max_outdegree"] <= limits["max_outdegree"],
        "max_module_loc_ok": metrics["max_module_loc"] <= limits["max_module_loc"],
    }
    checks["overall_ok"] = all(checks.values())

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "limits": limits,
        "metrics": metrics,
        "checks": checks,
        "details": {
            "layer_violations": violations,
            "worst_outdegree_modules": worst_outdegree_modules,
            "worst_loc_modules": worst_loc_modules,
            "top_outdegree": sorted(
                (
                    {"module": module, "outdegree": value}
                    for module, value in outdegree_by_module.items()
                ),
                key=lambda row: row["outdegree"],
                reverse=True,
            )[:10],
            "top_loc": sorted(
                ({"module": module, "loc": value} for module, value in loc_by_module.items()),
                key=lambda row: row["loc"],
                reverse=True,
            )[:10],
        },
    }


def _build_markdown(kpi: dict[str, Any]) -> str:
    metrics = kpi["metrics"]
    limits = kpi["limits"]
    checks = kpi["checks"]
    details = kpi["details"]
    status = "PASS" if checks["overall_ok"] else "FAIL"

    lines = [
        "# Agents Architecture KPI",
        "",
        f"- Generated (UTC): `{kpi['generated_at_utc']}`",
        f"- Status: **{status}**",
        "",
        "| KPI | Value | Limit | Status |",
        "|---|---:|---:|---|",
        (
            f"| Layer violations | {metrics['layer_violations']} | "
            f"<= {limits['layer_violations']} | "
            f"{'OK' if checks['layer_violations_ok'] else 'FAIL'} |"
        ),
        (
            f"| Max outdegree | {metrics['max_outdegree']} | <= {limits['max_outdegree']} | "
            f"{'OK' if checks['max_outdegree_ok'] else 'FAIL'} |"
        ),
        (
            f"| Max module LOC | {metrics['max_module_loc']} | <= {limits['max_module_loc']} | "
            f"{'OK' if checks['max_module_loc_ok'] else 'FAIL'} |"
        ),
        f"| Module count | {metrics['module_count']} | - | - |",
        "",
        "## Worst Modules",
        "",
        f"- Outdegree: `{', '.join(details['worst_outdegree_modules'])}`",
        f"- LOC: `{', '.join(details['worst_loc_modules'])}`",
    ]

    if details["layer_violations"]:
        lines.extend(
            [
                "",
                "## Layer Violations",
                "",
                *[f"- `{item}`" for item in details["layer_violations"]],
            ]
        )

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default="ci-artifacts/architecture-kpi",
        help="Directory for KPI report and badge artifacts.",
    )
    parser.add_argument(
        "--summary-file",
        default="",
        help="Optional path to GitHub step summary file.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    kpi = _collect_kpi()
    markdown = _build_markdown(kpi)

    _write_json(output_dir / "kpi.json", kpi)
    (output_dir / "kpi.md").write_text(markdown, encoding="utf-8")

    checks = kpi["checks"]
    metrics = kpi["metrics"]
    limits = kpi["limits"]
    _write_json(
        output_dir / "badge_architecture_health.json",
        _build_badge(
            label="architecture",
            message="pass" if checks["overall_ok"] else "fail",
            color="brightgreen" if checks["overall_ok"] else "red",
        ),
    )
    _write_json(
        output_dir / "badge_outdegree.json",
        _build_badge(
            label="outdegree",
            message=f"{metrics['max_outdegree']}/{limits['max_outdegree']}",
            color="brightgreen" if checks["max_outdegree_ok"] else "red",
        ),
    )
    _write_json(
        output_dir / "badge_loc.json",
        _build_badge(
            label="max-loc",
            message=f"{metrics['max_module_loc']}/{limits['max_module_loc']}",
            color="brightgreen" if checks["max_module_loc_ok"] else "red",
        ),
    )
    _write_json(
        output_dir / "badge_violations.json",
        _build_badge(
            label="violations",
            message=f"{metrics['layer_violations']}/{limits['layer_violations']}",
            color="brightgreen" if checks["layer_violations_ok"] else "red",
        ),
    )

    summary_file = args.summary_file.strip()
    if summary_file:
        summary_path = Path(summary_file)
        if summary_path.exists():
            summary_path.write_text(
                summary_path.read_text(encoding="utf-8") + "\n" + markdown,
                encoding="utf-8",
            )
        else:
            summary_path.write_text(markdown, encoding="utf-8")

    print(f"KPI artifacts written to: {output_dir}")


if __name__ == "__main__":
    main()
