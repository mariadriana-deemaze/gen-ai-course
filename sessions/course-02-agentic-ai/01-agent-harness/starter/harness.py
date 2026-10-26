"""Session 1 solution: the first-line incident agent, after exercises 1 and 2.

Exercise 1 adds the tools to investigate: metrics (per instance) and runbooks.
Exercise 2 adds one action (restart one instance) and a check in code: a
"resolved" report is only accepted if the metrics confirm recovery.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext

from meridian.agent_runtime import IncidentDeps

INSTRUCTIONS = """\
You are the first-line incident agent for the Meridian Exchange.
1. Read the alert, then read the metrics of the affected component per instance, to find which instance has a problem.
2. Find the runbook for that component with list_runbooks and read it.
3. If the runbook says to restart a single instance and the metrics show which one, restart only that instance.
   Never act without data. If a tool fails with retryable=true, try once more; if you still have no data,
   report insufficient_information.
4. After any action, read the metrics again. Report "resolved" only if the incident is over.
"""

# Recovery criterion from the order-gateway runbook (v2).
MAX_LATENCY_MS = 20
MAX_ERROR_RATE = 0.01
READINGS = 3


class IncidentReport(BaseModel):
    incident_id: str
    status: Literal["resolved", "unresolved", "insufficient_information"]
    diagnosis: str
    proposed_mitigation: str | None = None
    evidence: list[str]


# Tools -------------------------------------------------------------------
# A tool is a Python function. The model sees its name, its arguments and its
# docstring, and receives what it returns. `ctx.deps.tools` gives access to
# the simulator (see meridian/tools.py for what it offers).


def read_alert(ctx: RunContext[IncidentDeps], alert_id: str) -> dict[str, Any]:
    """Read an alert: rule, component, instance, state (open or resolved) and when it was opened."""
    return ctx.deps.tools.read_alert(alert_id)


# Exercise 1: tools to investigate


def read_metrics(ctx: RunContext[IncidentDeps], component: str, window_ticks: int = 5, per_instance: bool = False) -> dict[str, Any]:
    """Recent metrics of a component, one reading per minute: latency_ms, error_rate, queue_depth, cpu_pct, memory_pct.

    window_ticks: number of readings (1 to 30). per_instance=true adds memory, CPU, latency and status for each
    instance, which shows which instance has a problem. Cite source_ref as evidence.
    Components: order-gateway, matching-engine, market-data, settlement, reference-db.
    """
    return ctx.deps.tools.read_metrics(component, window_ticks, per_instance)


def list_runbooks(ctx: RunContext[IncidentDeps]) -> dict[str, Any]:
    """List the runbooks: name, version and the component versions they apply to."""
    return ctx.deps.tools.list_runbooks()


def read_runbook(ctx: RunContext[IncidentDeps], name: str) -> dict[str, Any]:
    """Read the latest version of a runbook, by the name given by list_runbooks."""
    return ctx.deps.tools.read_runbook(name)


# Exercise 2: one action, and a check that it worked


def restart_instance(ctx: RunContext[IncidentDeps], instance_id: str) -> dict[str, Any]:
    """Restart one instance of a non-critical component, e.g. order-gateway-2. It is back after about 2 minutes."""
    return ctx.deps.tools.restart_instance(instance_id)


def verify_recovery(ctx: RunContext[IncidentDeps], report: IncidentReport) -> IncidentReport:
    """Output validator: a "resolved" report is accepted only if the metrics confirm recovery."""
    if report.status != "resolved":
        return report
    # The component of the incident, from its alert
    component = ctx.deps.tools.read_alert(ctx.deps.sim.incident_brief()["alert_id"])["component"]
    metrics = ctx.deps.tools.read_metrics(component, READINGS)
    if "error" in metrics:
        raise ModelRetry(f"Recovery cannot be confirmed: {metrics['error']}. Do not report resolved.")
    # Reject if any of the last readings breaks the runbook's recovery criterion
    bad = [r for r in metrics["readings"] if r["latency_ms"] >= MAX_LATENCY_MS or r["error_rate"] >= MAX_ERROR_RATE]
    if bad:
        raise ModelRetry(
            f"Recovery not confirmed on {component}: at {bad[-1]['time']} latency_ms={bad[-1]['latency_ms']}, "
            f"error_rate={bad[-1]['error_rate']}. Recovery needs latency < {MAX_LATENCY_MS} ms and error rate < "
            f"{MAX_ERROR_RATE:.0%} in the last {READINGS} readings. Do not report resolved. "
            "If you have just acted, read the metrics again first."
        )
    return report


def build_agent(model: Any) -> Agent[IncidentDeps, IncidentReport]:
    # retries=3: the model gets a few attempts when the validator rejects a report
    agent = Agent(model, deps_type=IncidentDeps, output_type=IncidentReport, instructions=INSTRUCTIONS, retries=3)
    agent.tool(read_alert)
    agent.tool(read_metrics)
    agent.tool(list_runbooks)
    agent.tool(read_runbook)
    agent.tool(restart_instance)
    agent.output_validator(verify_recovery)
    return agent