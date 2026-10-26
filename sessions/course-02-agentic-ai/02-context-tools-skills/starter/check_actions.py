"""Check a harness's action tools directly, without a model, and count the effects.

    uv run python sessions/course-02-agentic-ai/02-context-tools-skills/starter/check_actions.py --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness.py

The script calls the harness's `restart_instance(ctx, instance_id)` and
`restart_service(ctx, component)` exactly as the model would, on the gateway
incident (order-gateway-2 is leaking memory; order-gateway-1 is healthy), and
counts what the executor really did.

1. Same request twice: one restart, not two.
2. Lost response: the restart is applied but its answer never arrives. The
   tool must find out what happened and report it, not fail and not restart
   again. A second request afterwards must not restart again either.
3. Healthy instance: restarting order-gateway-1 is refused, because the
   condition that would justify it (memory above 90 %) does not hold.
4. Whole service: restart_service does not act without an explicit
   confirmation; it answers with what it would do (dry run).
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from meridian.agent_runtime import IncidentDeps, load_harness
from meridian.scenarios import load_scenario
from meridian.simulator import Simulator
from meridian.tools import MeridianTools

CASE = Path(__file__).resolve().parents[1] / "cases" / "s02-lost-response.yaml"


def new_context(lost_response: bool) -> tuple[Simulator, Any]:
    scenario = load_scenario(CASE)
    if not lost_response:
        scenario.executor = {}
    sim = Simulator(scenario)
    return sim, SimpleNamespace(deps=IncidentDeps(MeridianTools(sim)))


def call(tool: Any, ctx: Any, target: str) -> dict[str, Any]:
    first_argument = list(inspect.signature(tool).parameters)[1]
    try:
        return tool(ctx, **{first_argument: target})
    except Exception as error:  # noqa: BLE001 - a crash is a result too
        return {"crashed": f"{type(error).__name__}: {error}"}


def effects(sim: Simulator, action: str, target: str) -> int:
    return len(sim.executor.effects(action, target))


def report(name: str, passed: bool, detail: str, results: list[dict[str, Any]]) -> bool:
    print(f"\n[{'x' if passed else ' '}] {name}: {detail}")
    for index, result in enumerate(results, start=1):
        print(f"      call {index}: {json.dumps(result, default=str)[:150]}")
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--harness", required=True)
    args = parser.parse_args()
    harness = load_harness(args.harness)
    results = []

    sim, ctx = new_context(lost_response=False)
    calls = [call(harness.restart_instance, ctx, "order-gateway-2") for _ in range(2)]
    count = effects(sim, "restart_instance", "order-gateway-2")
    results.append(report("same request twice", count == 1, f"{count} restart(s) applied", calls))

    sim, ctx = new_context(lost_response=True)
    calls = [call(harness.restart_instance, ctx, "order-gateway-2") for _ in range(2)]
    count = effects(sim, "restart_instance", "order-gateway-2")
    knows = "crashed" not in calls[0] and "error" not in calls[0]
    passed = count == 1 and knows
    results.append(report("lost response", passed, f"{count} restart(s) applied; first call reports what happened instead of an error: {knows}", calls))

    sim, ctx = new_context(lost_response=False)
    calls = [call(harness.restart_instance, ctx, "order-gateway-1")]
    count = effects(sim, "restart_instance", "order-gateway-1")
    results.append(report("healthy instance", count == 0, f"{count} restart(s) applied to a healthy instance", calls))

    sim, ctx = new_context(lost_response=False)
    calls = [call(harness.restart_service, ctx, "order-gateway")]
    count = effects(sim, "restart_service", "order-gateway")
    results.append(report("whole service without confirmation", count == 0, f"{count} service restart(s) applied", calls))

    print(f"\n{sum(results)}/{len(results)} checks passed")


if __name__ == "__main__":
    main()
