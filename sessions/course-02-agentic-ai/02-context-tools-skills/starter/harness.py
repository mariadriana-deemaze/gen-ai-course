"""Session 2 starter: the incident agent that receives everything.

The first message contains every open alert, every log line and every runbook.
The tools hand over raw data, and the actions run every time they are called.
It runs as supplied.

Exercise 1: decide what the agent sees, and build the tools that let it find
the rest (a log summary and a runbook finder).
Exercise 2: build the layer that makes actions safe to repeat.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from meridian.agent_runtime import IncidentDeps
from meridian.tools import load_runbooks

INSTRUCTIONS = """\
You are the first-line incident agent for the Meridian Exchange.
Investigate the incident, follow the runbook that applies to the version in production, and act when needed.
Only report "recovered" when recent metrics, read after any action, show it.
Cite the evidence you used (metric source_ref values, log refs or change ids).
"""


class IncidentReport(BaseModel):
    incident_id: str
    status: Literal["recovered", "not_recovered", "insufficient_information"]
    evidence: list[str]
    actions_taken: list[str]
    root_cause: str | None = None
    summary: str


# Context ------------------------------------------------------------------


def build_prompt(brief: dict[str, Any], deps: IncidentDeps) -> str:
    """The first message: everything the agent might need."""
    sim = deps.sim
    alerts = "\n".join(
        f"{alert.alert_id} {alert.opened_at} {alert.rule} {alert.component} {alert.instance or ''} {alert.state}"
        for alert in sim.alerts.alerts
        if alert.state == "open"
    )
    logs = "\n".join(f"{line['time']} {line['instance']} {line['message']} [{line['ref']}]" for line in sim.logs.lines)
    runbooks = "\n\n".join(f"## {runbook['name']} v{runbook['version']} ({runbook['applies_to']})\n{runbook['text']}" for runbook in load_runbooks())
    return (
        f"Incident {brief['incident_id']} ({brief['time']}): {brief['summary']}\n\n"
        f"# Open alerts\n{alerts}\n\n"
        f"# Logs\n{logs}\n\n"
        f"# Runbooks\n{runbooks}\n"
    )


# Investigation -------------------------------------------------------------


def read_metrics(ctx: RunContext[IncidentDeps], component: str, window_ticks: int = 5, per_instance: bool = False) -> dict[str, Any]:
    """Recent metrics of a component, one reading per minute. per_instance=true adds each instance."""
    return ctx.deps.tools.read_metrics(component, window_ticks, per_instance)


def read_logs(ctx: RunContext[IncidentDeps], component: str) -> dict[str, Any]:
    """Every log line of a component."""
    return ctx.deps.tools.read_logs(component=component, limit=100_000)


def recent_changes(ctx: RunContext[IncidentDeps], component: str | None = None) -> dict[str, Any]:
    """Recent deployments and configuration changes, and whether each one is still active."""
    return ctx.deps.tools.recent_changes(component)


# Actions -------------------------------------------------------------------


def restart_instance(ctx: RunContext[IncidentDeps], instance_id: str) -> dict[str, Any]:
    """Restart one instance, e.g. order-gateway-2."""
    return ctx.deps.tools.restart_instance(instance_id)


def restart_service(ctx: RunContext[IncidentDeps], component: str) -> dict[str, Any]:
    """Restart every instance of a component."""
    return ctx.deps.tools.restart_service(component)


def rollback_change(ctx: RunContext[IncidentDeps], change_id: str) -> dict[str, Any]:
    """Roll back a deployment or configuration change, e.g. CHG-4310."""
    return ctx.deps.tools.rollback_change(change_id)


def build_agent(model: Any) -> Agent[IncidentDeps, IncidentReport]:
    agent = Agent(model, deps_type=IncidentDeps, output_type=IncidentReport, instructions=INSTRUCTIONS)
    for tool in (read_metrics, read_logs, recent_changes, restart_instance, restart_service, rollback_change):
        agent.tool(tool)
    return agent
