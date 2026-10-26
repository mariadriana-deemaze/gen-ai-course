"""Simulated MCP servers for the Meridian Exchange (Session 3 onwards).

Three servers imitate the products the exchange uses:

- `tickets`: a Jira-like service desk (projects MRX and MEM);
- `monitoring`: a LogicMonitor-like platform (devices, alerts, datapoints, logs);
- `deployments`: a deployment platform (deployments, config changes, rollbacks).

They are real MCP servers (FastMCP). By default they run in the same process
as the agent, so they share the simulator: a restart through `deployments`
changes the metrics that `monitoring` returns. Every call is traced like any
other tool call and moves simulated time forward.

The scenario's `mcp` section can change a server's version, publisher and tool
descriptions, the scopes granted to the agent's credentials, and add servers
that were never reviewed (`extra_servers: [ops-community]`).

    from meridian.mcp_servers import build_servers
    servers = build_servers(deps.tools)          # {"tickets": FastMCP, ...}
"""

from __future__ import annotations

from fastmcp import FastMCP

from meridian.mcp_servers import community, deployments, monitoring, tickets
from meridian.tools import MeridianTools

BUILDERS = {"tickets": tickets.build, "monitoring": monitoring.build, "deployments": deployments.build}
EXTRA_BUILDERS = {"ops-community": community.build}


def build_servers(tools: MeridianTools, names: list[str] | None = None) -> dict[str, FastMCP]:
    """The servers of the catalog. By default: the three official ones, plus any extra server the scenario lists
    under `mcp.extra_servers` (servers that appeared in the catalog without review)."""
    if names is None:
        names = list(BUILDERS) + list(tools.sim.scenario.mcp.get("extra_servers", []))
    every = {**BUILDERS, **EXTRA_BUILDERS}
    return {name: every[name](tools) for name in names}


__all__ = ["BUILDERS", "build_servers"]
