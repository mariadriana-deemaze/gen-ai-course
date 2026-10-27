"""Score a harness's select_tools on a set of incidents, without running the agent.

    uv run python sessions/course-02-agentic-ai/02-context-tools-skills/starter/check_routing.py --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness.py

For each incident in routing/incidents.yaml the script calls
`select_tools(incident, tool_catalog(), model)` with the whole catalog, as the
harness does at the start of a run, and compares the selection with what the
incident needs.

- coverage: the share of needs covered by the selection (a need is covered when
  any of its tools is selected). Target: at least 0.95.
- size: the average number of tools selected. Target: at most 10.
- never: tools that must never be offered (legacy or dangerous). Target: 0.

If your select_tools asks a model, the script makes one model request per
incident (20 small requests).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic_ai import Tool

from meridian.agent_runtime import default_model, load_harness

INCIDENTS = Path(__file__).resolve().parents[1] / "routing" / "incidents.yaml"
COVERAGE_TARGET = 0.95
SIZE_TARGET = 10


def definition_tokens(tools: dict[str, Any], names: set[str]) -> int:
    """Approximate tokens of the tool definitions sent with every model request."""
    total = 0
    for name in names:
        if name in tools:
            definition = Tool(tools[name]).tool_def
            total += len(json.dumps({"name": definition.name, "description": definition.description, "parameters": definition.parameters_json_schema})) // 4
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--harness", required=True)
    parser.add_argument("--incidents", default=str(INCIDENTS))
    parser.add_argument("--model", default=None, help="model passed to select_tools (default: the model of the runs)")
    args = parser.parse_args()

    harness = load_harness(args.harness)
    harness.selection.USE_FULL_CATALOG = True
    data = yaml.safe_load(Path(args.incidents).read_text(encoding="utf-8"))
    never = set(data.get("never", []))
    tools = harness.available_tools()
    catalog = harness.tool_catalog()
    harness.selection.CATALOG.clear()
    harness.selection.CATALOG.update(catalog)
    model = args.model or default_model()

    covered_needs = total_needs = selected_total = never_total = 0
    for incident in data["incidents"]:
        selected = set(harness.select_tools(incident["text"], dict(catalog), model))
        missed = [need for need in incident["needs"] if not selected & set(need)]
        forbidden = sorted(selected & never)
        unknown = sorted(selected - set(catalog))
        covered_needs += len(incident["needs"]) - len(missed)
        total_needs += len(incident["needs"])
        selected_total += len(selected)
        never_total += len(forbidden)
        mark = "x" if not missed and not forbidden else " "
        print(f"[{mark}] {incident['id']}  {len(selected):>2} tools  {incident['text'][:70]}")
        for need in missed:
            print(f"        missing one of: {', '.join(need)}")
        if forbidden:
            print(f"        never offer: {', '.join(forbidden)}")
        if unknown:
            print(f"        not in the catalog (ignored by the harness): {', '.join(unknown)}")

    count = len(data["incidents"])
    coverage = covered_needs / total_needs
    size = selected_total / count
    every = definition_tokens(tools, set(catalog))
    print(f"\ncoverage {coverage:.2f} (target >= {COVERAGE_TARGET})   size {size:.1f} tools (target <= {SIZE_TARGET})   never offered {never_total} (target 0)")
    print(f"tool definitions: the whole catalog is ~{every} tokens in every model request; your average selection ~{round(every * size / len(catalog))}")
    passed = coverage >= COVERAGE_TARGET and size <= SIZE_TARGET and never_total == 0
    print("PASS" if passed else "FAIL")


if __name__ == "__main__":
    main()
