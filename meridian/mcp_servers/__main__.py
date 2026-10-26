"""Inspect the MCP catalog a scenario exposes, without a model.

    uv run python -m meridian.mcp_servers inspect [--scenario case.yaml] [--server monitoring] [--full]

Shows each server's version, publisher, scopes granted to the agent and tools,
with the size of every tool definition (name, description and input schema) as
the model would receive it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from fastmcp import Client

from meridian.mcp_servers import build_servers
from meridian.mcp_servers._base import settings_for
from meridian.mcp_servers import community, deployments, monitoring, tickets
from meridian.simulator import Simulator
from meridian.tools import MeridianTools

DEFAULTS = {"tickets": tickets.DEFAULTS, "monitoring": monitoring.DEFAULTS, "deployments": deployments.DEFAULTS, "ops-community": community.DEFAULTS}
DEFAULT_SCENARIO = "sessions/course-02-agentic-ai/03-mcp-integration/cases/s03-alert-and-ticket.yaml"


async def describe(tools: MeridianTools, names: list[str] | None) -> list[dict]:
    catalog = []
    for name, server in build_servers(tools, names).items():
        settings = settings_for(tools, name, DEFAULTS[name])
        async with Client(server) as client:
            listed = await client.list_tools()
        entries = []
        for tool in listed:
            definition = json.dumps({"name": tool.name, "description": tool.description, "parameters": tool.input_schema})
            entries.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "chars": len(definition),
                    "approx_tokens": len(definition) // 4,
                    "description_sha256": hashlib.sha256((tool.description or "").encode()).hexdigest()[:12],
                }
            )
        catalog.append({"server": name, "version": settings.version, "publisher": settings.publisher, "scopes": settings.scopes, "tools": entries})
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the simulated MCP catalog.")
    parser.add_argument("command", choices=["inspect"])
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--server", action="append")
    parser.add_argument("--full", action="store_true", help="Print full descriptions.")
    parser.add_argument("--json", action="store_true", help="Print the catalog as JSON.")
    parser.add_argument("--out", help="Write the catalog as JSON (UTF-8) to this file, e.g. a lock file.")
    args = parser.parse_args()
    tools = MeridianTools(Simulator(args.scenario))
    catalog = asyncio.run(describe(tools, args.server))
    if args.out:
        Path(args.out).write_text(json.dumps(catalog, indent=2), encoding="utf-8")
        print(f"Catalog written to {args.out}")
        return
    if args.json:
        print(json.dumps(catalog, indent=2))
        return
    total = 0
    for server in catalog:
        size = sum(tool["approx_tokens"] for tool in server["tools"])
        total += size
        print(f"\n{server['server']} {server['version']} (publisher: {server['publisher']}) - {len(server['tools'])} tools, ~{size} tokens")
        print(f"  agent scopes: {', '.join(server['scopes'])}")
        for tool in server["tools"]:
            text = tool["description"] if args.full else (tool["description"] or "")[:70]
            print(f"  {tool['name']:<26} ~{tool['approx_tokens']:>4} tok  sha {tool['description_sha256']}  {text}")
    print(f"\nWhole catalog: ~{total} tokens of tool definitions in every model request.")


if __name__ == "__main__":
    main()
