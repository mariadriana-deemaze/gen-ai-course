"""Exercise 1 — Completed context policy.

The first message contains only the incident and a list of components.
Tools provide metrics, grouped logs, recent changes and the applicable runbook.
Log grouping uses Python, not an additional model call.
"""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict
from typing import Any

from pydantic_ai import RunContext

from meridian.agent_runtime import IncidentDeps

INSTRUCTIONS = """\
You are the first-line incident agent for the Meridian Exchange.
1. Investigate the alerted component with read_metrics (per_instance=true) and summarize_logs.
2. If it does not explain the cause, follow its dependencies: order-gateway depends on reference-db and
   matching-engine. Consult their logs and recent_changes as needed.
3. Use find_runbook to load the procedure that applies to the version in production.
4. Act only when the evidence justifies it. If search_tools is available and a tool is missing, use it.
Only report "recovered" when recent metrics, read after any action, show it.
Cite the evidence you used (metric source_ref values, log refs or change ids).
"""


def build_prompt(brief: dict[str, Any], deps: IncidentDeps) -> str:
    """Start small; logs, metrics, changes and runbooks are fetched with tools."""
    return (
        f"Incident {brief['incident_id']} ({brief['time']}): {brief['summary']}\n"
        f"Related alert: {brief['alert_id']}.\n"
        "Components: order-gateway, matching-engine, market-data, settlement, reference-db."
    )


def read_metrics(ctx: RunContext[IncidentDeps], component: str, window_ticks: int = 5, per_instance: bool = False) -> dict[str, Any]:
    """Recent metrics of a component, one reading per minute. per_instance=true adds each instance."""
    return ctx.deps.tools.read_metrics(component, window_ticks, per_instance)


def recent_changes(ctx: RunContext[IncidentDeps], component: str | None = None) -> dict[str, Any]:
    """Recent deployments and configuration changes, and whether each one is still active."""
    return ctx.deps.tools.recent_changes(component, limit=5)


# Replace variable values only for grouping; original examples keep their values.
VARIABLES = [
    (re.compile(r"\b\d+\.\d+\.\d+\.\d+\b"), "<ip>"),
    (re.compile(r"=\S+"), "=<*>"),
    (re.compile(r"\b[A-Z]+-\d+\b"), "<id>"),
    (re.compile(r"\d+"), "<n>"),
]


def template_of(message: str) -> str:
    """Group messages with the same shape despite different IDs, addresses or values."""
    for pattern, placeholder in VARIABLES:
        message = pattern.sub(placeholder, message)
    return message


def summarize_logs(ctx: RunContext[IncidentDeps], component: str, recent_minutes: int = 30) -> dict[str, Any]:
    """Summarize a component's logs, highlighting rare or recently introduced messages.

    Returns template counts and original examples with log refs. Use this to
    investigate unusual messages without loading thousands of repeated lines.
    Rare messages are clues, not proof of the cause.
    """
    result = ctx.deps.tools.read_logs(component=component, limit=1_000_000)
    if "error" in result:
        return result
    lines = result["lines"]
    if not lines:
        return {"component": component, "total_lines": 0, "distinct_templates": 0, "rare_or_new": [], "most_frequent": []}

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in lines:
        groups[template_of(line["message"])].append(line)

    now = lines[-1]["tick"]
    rare_or_new, frequent = [], []
    for template, members in groups.items():
        first, last = members[0], members[-1]
        entry = {"template": template, "count": len(members), "level": first["level"], "first_seen": first["time"]}
        if len(members) <= 2 or first["tick"] >= now - recent_minutes:
            rare_or_new.append({**entry, "example": last["message"], "instance": last["instance"], "ref": last["ref"]})
        else:
            frequent.append(entry)

    rare_or_new.sort(key=lambda entry: (entry["level"] not in ("ERROR", "WARN"), entry["count"]))
    frequent.sort(key=lambda entry: entry["count"], reverse=True)
    return {
        "component": component,
        "total_lines": len(lines),
        "distinct_templates": len(groups),
        "rare_or_new": rare_or_new[:10],
        "most_frequent": frequent[:8],
    }


def find_runbook(ctx: RunContext[IncidentDeps], component: str) -> dict[str, Any]:
    """Load only the runbook that applies to the component's version in production."""
    metrics = ctx.deps.tools.read_metrics(component, 1)
    if "error" in metrics:
        return metrics
    version = metrics["readings"][-1]["version"]
    catalogue = ctx.deps.tools.list_runbooks()["runbooks"]
    for entry in catalogue:
        applies_component, _, applies_version = entry["applies_to"].partition(" ")
        if applies_component == component and fnmatch.fnmatch(version, applies_version.replace("x", "*")):
            return {"version_in_production": version, **ctx.deps.tools.read_runbook(entry["name"], entry["version"])}
    return {"component": component, "version_in_production": version, "runbook": None, "detail": "No runbook applies to this version."}


# Raw logs and the full runbook collection stay out of the model's context.
TOOLS = (read_metrics, summarize_logs, recent_changes, find_runbook)
