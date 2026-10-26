"""Check a gatekeeper (gateway.py) directly, without a model.

    uv run python sessions/course-02-agentic-ai/03-mcp-integration/starter/check_gateway.py --gateway sessions/course-02-agentic-ai/03-mcp-integration/starter/gateway.py

The script builds the real catalogs of three situations (the normal one, the
monitoring server upgraded overnight, and an unreviewed community server added
to the catalog) and asks your gateway what it approves and what it blocks.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
from pathlib import Path
from typing import Any

from meridian.mcp_servers.__main__ import describe
from meridian.simulator import Simulator
from meridian.tools import MeridianTools

CASES = Path(__file__).resolve().parents[1] / "cases"


def load_gateway(path: str) -> Any:
    spec = importlib.util.spec_from_file_location("student_gateway", Path(path).resolve())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalog(case: str) -> dict[tuple[str, str], str]:
    servers = asyncio.run(describe(MeridianTools(Simulator(CASES / f"{case}.yaml")), None))
    return {(server["server"], tool["name"]): tool["description"] for server in servers for tool in server["tools"]}


def check(name: str, passed: bool, detail: str) -> bool:
    print(f"  [{'x' if passed else ' '}] {name}: {detail}")
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gateway", required=True, help="Your gateway.py")
    args = parser.parse_args()
    gateway = load_gateway(args.gateway)
    if not gateway.LOCK_FILE.is_file():
        print(f"No lock file at {gateway.LOCK_FILE}. Create it with: uv run python -m meridian.mcp_servers inspect --out {gateway.LOCK_FILE}")
    normal = catalog("s03-alert-and-ticket")
    changed = catalog("s03-description-change")
    shadow = catalog("s03-shadow-server")
    results = []

    print("\n== what the model may see")
    gateway.reset()
    needed = [("tickets", "search_issues"), ("monitoring", "get_device_datapoints"), ("deployments", "list_deployments")]
    missing = [f"{server}.{name}" for server, name in needed if not gateway.approve_tool(server, name, normal[(server, name)])]
    results.append(check("approves the tools an investigation needs", not missing, f"refused: {missing}" if missing else "ok"))
    risky = ("monitoring", "create_sdt")
    results.append(check("refuses a tool this agent should not have (monitoring.create_sdt silences alerts)", not gateway.approve_tool(*risky, normal[risky]), "ok" if not gateway.approve_tool(*risky, normal[risky]) else "approved"))
    tool = ("monitoring", "get_device_datapoints")
    results.append(check("refuses a tool whose description changed since it was approved", not gateway.approve_tool(*tool, changed[tool]), "ok" if not gateway.approve_tool(*tool, changed[tool]) else "approved the changed description"))
    unknown = [name for (server, name), description in shadow.items() if server == "ops-community" and gateway.approve_tool(server, name, description)]
    results.append(check("refuses every tool of a server that was never reviewed", not unknown, f"approved: {unknown}" if unknown else "ok"))

    print("\n== what the model may do")
    gateway.reset()
    whole = gateway.check_call("deployments", "restart_workload", {"service": "order-gateway"})
    one = gateway.check_call("deployments", "restart_workload", {"service": "order-gateway", "instance": "order-gateway-2"})
    results.append(check("blocks a restart of a whole service", bool(whole), whole or "let through"))
    results.append(check("lets a restart of one instance through", one is None, "ok" if one is None else f"blocked: {one}"))
    gateway.reset()
    refusal = {"error": "insufficient_scope", "required_scope": "tickets:comment", "granted_scopes": ["tickets:read"]}
    first = gateway.check_call("tickets", "add_comment", {"key": "MRX-2240", "body": "x"})
    gateway.after_call("tickets", "add_comment", {"key": "MRX-2240", "body": "x"}, refusal)
    again = gateway.check_call("tickets", "add_comment", {"key": "MRX-2240", "body": "y"})
    results.append(check("after a refusal for lack of scope, blocks the same write instead of letting the agent insist", first is None and bool(again), again or "let through again"))

    print(f"\n{sum(results)}/{len(results)} checks passed")


if __name__ == "__main__":
    main()
