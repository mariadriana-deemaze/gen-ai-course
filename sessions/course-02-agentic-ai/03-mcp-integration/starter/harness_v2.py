"""Session 3 starter: the incident agent connected to the exchange's systems through MCP.

The agent is connected to:
- the MCP servers of other teams (tickets, monitoring, deployments, and any
  other server that appears in the catalog), through the gatekeeper in
  gateway.py;
- your own MCP server, `incidents` (incident_server.py), where it reports
  every event it handles.

This file wires the pieces together. Your work is in incident_server.py
(exercise 1) and gateway.py (exercise 2). It runs as supplied.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.mcp import MCPToolset

from meridian.agent_runtime import IncidentDeps
from meridian.mcp_servers import build_servers

HERE = Path(__file__).resolve().parent


def _load(name: str) -> Any:
    """Load a module that sits next to this file."""
    module_name = f"{name}_{abs(hash(HERE)) % 100_000}"
    spec = importlib.util.spec_from_file_location(module_name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


gateway = _load("gateway_v2")
incident_server = _load("incident_server_v2")

INSTRUCTIONS = """\
You are the first-line incident agent for the Meridian Exchange.
Report every event you handle (the alert of the incident, tickets and complaints you find) with incidents_report_event,
and record there what you found and did.
Investigate with monitoring data, follow the applicable runbook and act when needed.
Only report "recovered" when recent datapoints, read after any action, show it.
Cite the evidence you used.
"""


class IncidentReport(BaseModel):
    incident_id: str
    status: Literal["recovered", "not_recovered", "insufficient_information"]
    ticket_keys: list[str]
    evidence: list[str]
    actions_taken: list[str]
    summary: str


def _as_data(result: Any) -> Any:
    if isinstance(result, str):
        try:
            return json.loads(result)
        except ValueError:
            return result
    return result


def _through_gateway(server_name: str) -> Any:
    async def process_tool_call(ctx: RunContext[IncidentDeps], call_tool: Any, name: str, args: dict[str, Any]) -> Any:
        reason = gateway.check_call(server_name, name, args)
        if reason:
            return {"error": "blocked_by_gateway", "reason": reason}
        result = await call_tool(name, args)
        gateway.after_call(server_name, name, args, _as_data(result))
        return result

    return process_tool_call


def build_toolsets(deps: IncidentDeps) -> list[Any]:
    gateway.reset()
    toolsets = []
    for name, server in build_servers(deps.tools).items():
        toolset = MCPToolset(server, process_tool_call=_through_gateway(name))
        approved = toolset.filtered(lambda ctx, tool, server_name=name: gateway.approve_tool(server_name, tool.name, tool.description or ""))
        toolsets.append(approved.prefixed(name))
    toolsets.append(MCPToolset(incident_server.build_server(deps.tools)).prefixed("incidents"))
    return toolsets


def list_runbooks(ctx: RunContext[IncidentDeps]) -> dict[str, Any]:
    """List runbooks: name, version and the component versions they apply to."""
    return ctx.deps.tools.list_runbooks()


def read_runbook(ctx: RunContext[IncidentDeps], name: str) -> dict[str, Any]:
    """Read the latest version of a runbook."""
    return ctx.deps.tools.read_runbook(name)


def _chat_completions(model: Any) -> Any:
    """Anthropic's OpenAI-compatible endpoint serves Chat Completions, not Responses.

    In pydantic-ai 2.x the `openai:` prefix means the Responses API (`/v1/responses`),
    which that endpoint answers with 404; `openai-chat:` means Chat Completions
    (`/v1/chat/completions`), which it serves. Rewriting it here keeps `meridian/` as
    supplied.
    """
    if isinstance(model, str) and model.startswith("openai:"):
        return f"openai-chat:{model.removeprefix('openai:')}"
    return model


def build_agent(model: Any) -> Agent[IncidentDeps, IncidentReport]:
    agent = Agent(_chat_completions(model), deps_type=IncidentDeps, output_type=IncidentReport, instructions=INSTRUCTIONS)
    agent.tool(list_runbooks)
    agent.tool(read_runbook)
    return agent
