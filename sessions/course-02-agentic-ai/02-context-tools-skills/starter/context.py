"""Exercise 1 — Find the needle without reading the haystack.

The problem
    The gateway is slow and the pager shows 200 alerts. The agent receives
    every alert, about 5 000 log lines and every runbook in its first message:
    more than 100 000 tokens. The cause is often a single line in there, and
    the agent misses it. Sometimes it follows the runbook of an older gateway
    version and restarts the whole service.

    A person on call does not read 5 000 lines. They look for what is unusual,
    open the runbook of the version they run, and fetch the rest when needed.

Build, in this file
    A. build_prompt: what goes in the first message; the rest is fetched with tools.
    B. summarize_logs(component): thousands of log lines -> a short list, unusual lines first.
    C. find_runbook(component): only the runbook that applies to the version in production.
    Then add B and C to TOOLS, and remove read_logs (it returns every line).

Watch out
    - Whatever you take out of the prompt must be reachable with a tool.
    - The cause is not always on the component of the alert: in two cases it is
      in another component's logs, or in a change made to another component.
    - Thousands of identical errors are a symptom. The line that explains them
      can appear once.
    - The model only knows a tool from its name, arguments and docstring.
    - Budget: all the model requests of a run together stay under 40 000 input tokens.

Logs are written from a few dozen templates; only the values change:

    INFO  order accepted id=O481920 member=NORDBANK side=buy
    INFO  order accepted id=O519034 member=ATLAS-SEC side=sell
    WARN  max_connections changed from 500 to 60 (CHG-4471); new connections above the limit are rejected

The first two lines are the same template. The third appears once: it is the cause.

Hints (read only if stuck)
    1. Write the policy in three lines first: always in the prompt; loaded on
       demand; never loaded. Then tell the agent, in INSTRUCTIONS, in which order to look.
    2. Templates: replace with regular expressions, in this order: ids like
       CHG-4471, IP addresses, key=value values, any remaining number. If you
       get dozens of nearly identical templates, you are not replacing enough.
    3. Rare: seen once or twice, or first seen in the last 30 minutes. Return
       each rare template with one full example line and its log ref.
    4. Runbooks: applies_to looks like "order-gateway 2.4.x"; turn x into * and use fnmatch.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from meridian.agent_runtime import IncidentDeps
from meridian.tools import load_runbooks

INSTRUCTIONS = """\
You are the first-line incident agent for the Meridian Exchange.
Investigate the incident, follow the runbook that applies to the version in production, and act when needed.
Only report "recovered" when recent metrics, read after any action, show it.
Cite the evidence you used (metric source_ref values, log refs or change ids).
"""


def build_prompt(brief: dict[str, Any], deps: IncidentDeps) -> str:
    """A. The first message. As supplied: everything the agent might need."""
    # Hint: keep the incident line; replace the three dumps below with a pointer to the tools.
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


def read_metrics(ctx: RunContext[IncidentDeps], component: str, window_ticks: int = 5, per_instance: bool = False) -> dict[str, Any]:
    """Recent metrics of a component, one reading per minute. per_instance=true adds each instance."""
    return ctx.deps.tools.read_metrics(component, window_ticks, per_instance)


def read_logs(ctx: RunContext[IncidentDeps], component: str) -> dict[str, Any]:
    """Every log line of a component."""
    return ctx.deps.tools.read_logs(component=component, limit=100_000)


def recent_changes(ctx: RunContext[IncidentDeps], component: str | None = None) -> dict[str, Any]:
    """Recent deployments and configuration changes, and whether each one is still active."""
    return ctx.deps.tools.recent_changes(component)


def template_of(message: str) -> str:
    """B. The template of a log line: the line with its variable parts replaced by placeholders."""
    # Hint: a list of (compiled regex, placeholder) pairs, applied in order.
    return message


def summarize_logs(ctx: RunContext[IncidentDeps], component: str) -> dict[str, Any]:
    """B. Write the docstring the model will read: what this returns and when to use it."""
    # Hint: ctx.deps.tools.read_logs(component=component, limit=1_000_000)["lines"]; each line has
    # "message", "level", "tick", "time", "instance" and "ref". Group by template_of(message).
    raise NotImplementedError


def find_runbook(ctx: RunContext[IncidentDeps], component: str) -> dict[str, Any]:
    """C. Write the docstring the model will read."""
    # Hint: version from ctx.deps.tools.read_metrics(component, 1)["readings"][-1]["version"];
    # list from ctx.deps.tools.list_runbooks()["runbooks"]; text from ctx.deps.tools.read_runbook(name, version).
    raise NotImplementedError


# The investigation tools of this agent. Add summarize_logs and find_runbook; remove read_logs.
TOOLS = (read_metrics, read_logs, recent_changes)
