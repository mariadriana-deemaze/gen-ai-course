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

import re
from fnmatch import fnmatch
from typing import Any, TypedDict

from pydantic_ai import RunContext

from meridian.agent_runtime import IncidentDeps

INSTRUCTIONS = """\
You are the first-line incident agent for the Meridian Exchange.
Investigate, follow the runbook for the version in production, and act when needed.
Report "recovered" only if metrics read after the action show it. Cite your evidence: source_ref, log refs, change ids.
Report "insufficient_information" only when a tool failed and left you unable to judge. Never report it before
calling a tool: start the investigation, then report what you found.

The first message holds the incident, the alerts and the platform map. Everything else is behind a tool.
Look in this order, and stop once you have the cause:

1. read_metrics on the alerting component, per_instance=true: is one instance at fault?
2. summarize_logs on it. Read the rare templates first; the thousands of identical errors are the symptom.
3. Not explained yet? summarize_logs on each component it depends on. The cause is often one the alert never names.
4. recent_changes, no component.
5. find_runbook on the component at fault, and follow it.

If no tool does what you need, call search_tools and describe the need in a sentence.
Never ask for every log line: summarize_logs already read them all.

Every word in this conversation is re-sent on every later request, including your own:
- Write nothing between tool calls. Put all reasoning in the final report.
- summarize_logs once per component. per_instance only while finding the bad instance.
- Ask for the smallest window of readings. After an action, read the last 3.
"""


def build_prompt(brief: dict[str, Any], deps: IncidentDeps) -> str:
    """A. The first message: the incident, the alerts collapsed, and the platform map.

    Example (s02-hidden-config-change, 62 alerts and 4 843 log lines in the simulator; 191 tokens out):

        Incident INC-2102 (11:45): Alert error_rate_high on order-gateway. Some orders are rejected with HTTP 503.

        # Open alerts
        error_rate_high on order-gateway — 61 open, 11:26 to 11:29
        error_rate_high on settlement — 1 open, 11:26

        # Platform
        - order-gateway depends on reference-db, matching-engine
        - matching-engine [critical: this agent cannot change it] depends on market-data
        - market-data no dependencies
        - settlement depends on matching-engine, reference-db
        - reference-db [critical: this agent cannot change it] no dependencies

        # Everything else
        The logs and the runbooks are not here. Call summarize_logs(component) for what is unusual in a
        component's logs, and find_runbook(component) for the runbook that matches the version it runs.
        The alert of this incident is ALR-1001.
    """
    return (
        f"Incident {brief['incident_id']} ({brief['time']}): {brief['summary']}\n\n"
        f"# Open alerts\n{_collapsed_alerts(deps)}\n\n"
        f"# Platform\n{_platform_map(deps)}\n\n"
        "# Everything else\n"
        "The logs and the runbooks are not here. Call summarize_logs(component) for what is unusual in a\n"
        f"component's logs, and find_runbook(component) for the runbook that matches the version it runs.\n"
        f"The alert of this incident is {brief['alert_id']}.\n"
    )


def _collapsed_alerts(deps: IncidentDeps) -> str:
    """Open alerts as one line per (rule, component, instance), with how many there are.

    A duplicate storm of 200 alerts carries one fact. It is written as one fact.

    The grouping key is exact, because duplicate alerts are identical by construction. Compare template_of,
    which has to derive a key because no two log lines are ever identical. Ordered by count descending: for
    alerts, volume is the signal. Compare summarize_logs, which puts the rarest first.

    Example (s02-hidden-config-change, 62 open Alert objects in, 2 lines out):

        error_rate_high on order-gateway — 61 open, 11:26 to 11:29
        error_rate_high on settlement — 1 open, 11:26

    No example line is kept. A duplicate alert says only that it fired again.
    """
    groups: dict[tuple[str, str, str | None], list[Any]] = {}
    for alert in deps.sim.alerts.alerts:
        if alert.state == "open":
            groups.setdefault((alert.rule, alert.component, alert.instance), []).append(alert)
    if not groups:
        return "(none open)"

    lines = []
    for (rule, component, instance), alerts in sorted(groups.items(), key=lambda item: -len(item[1])):
        opened = sorted(alert.opened_at for alert in alerts)
        target = f"{component}/{instance}" if instance else component
        duplicates = f"{len(alerts)} open" if len(alerts) > 1 else "1 open"
        window = opened[0] if len(opened) == 1 else f"{opened[0]} to {opened[-1]}"
        lines.append(f"{rule} on {target} — {duplicates}, {window}")
    return "\n".join(lines)


def _platform_map(deps: IncidentDeps) -> str:
    """Which components exist and which depends on which. Static, and the agent cannot ask without it.

    Every tool takes a component name. Without this map the only name the agent ever sees is the one in the
    alert, so it cannot ask about reference-db, where the cause of two of the four cases is.

    Example (the same for every case, since the topology does not change during a run):

        - order-gateway depends on reference-db, matching-engine
        - matching-engine [critical: this agent cannot change it] depends on market-data
        - market-data no dependencies
        - settlement depends on matching-engine, reference-db
        - reference-db [critical: this agent cannot change it] no dependencies

    Versions are deliberately absent: find_runbook reads the running version from the metrics instead.
    """
    lines = []
    for name, component in deps.sim.state.components.items():
        critical = " [critical: this agent cannot change it]" if component.critical else ""
        depends = f" depends on {', '.join(component.depends_on)}" if component.depends_on else " no dependencies"
        lines.append(f"- {name}{critical}{depends}")
    return "\n".join(lines)


# Applied in this order. Ids first, because CHG-4471 is not a key=value pair
# and the number rule would otherwise shred it into CHG-<n>. The placeholders
# hold no digits, so the final number rule cannot damage an earlier one.
_SUBSTITUTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[A-Z]{2,}-\d+\b"), "<id>"),  # CHG-4471, ALR-1042, INC-2102
    (re.compile(r"\b[A-Z]\d{4,}\b"), "<id>"),  # O481920
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<ip>"),  # 10.0.0.1
    (re.compile(r"(?<==)\S+"), "<v>"),  # member=NORDBANK, code=503
    (re.compile(r"\d+(?:\.\d+)?"), "<n>"),  # anything numeric left over
)

# Rare, in the two ways that matter. Count catches the line that appears once;
# recency catches the fault that started twenty minutes ago and has already
# fired forty times, which no count-based rule would ever surface.
RARE_MAX_COUNT = 2
RARE_RECENT_TICKS = 30

# A component has a few dozen templates at most. The cap is a guard on the
# token budget, not an expected outcome.
MAX_COMMON_TEMPLATES = 40


class Template(TypedDict):
    """One log template and how often it occurred. What a common template is reduced to: a count."""

    count: int
    level: str
    template: str


class RareTemplate(Template):
    """A template worth reading. It carries evidence, which a common one does not."""

    why_rare: str  # "seen once or twice" or "first seen recently"
    first_seen: str
    last_seen: str
    instances: list[str]
    example: str  # the full line, with its real ids and values
    ref: str  # cite this in the report


class LogSummary(TypedDict):
    """What summarize_logs returns: thousands of lines as a few dozen counted templates."""

    component: str
    lines_read: int
    templates_found: int
    rare: list[RareTemplate]
    common: list[Template]
    common_truncated: bool


def _new_since(lines: list[dict[str, Any]], now: int) -> int:
    """The tick after which a first appearance counts as new.

    "First seen in the last 30 minutes" only separates anything when the log
    reaches further back than 30 minutes. On a short log every template is new,
    including the heartbeat, and rarity stops discriminating. So the window
    never covers more than the older half of the log: on a log too short to
    have a history, nothing is new and rarity falls back to the count alone.

    Example (s02-hidden-config-change, a 120-tick log):

        now = 121, oldest line at tick 1  ->  91      (the 30-tick window wins)

    Example (s02-lost-response, where warmup_ticks is 30, so the whole log is 30 ticks):

        now = 30, oldest line at tick 0   ->  15      (the half-the-log floor wins)

    Without that floor the second case returns 0, every template counts as new, and "INFO order accepted"
    is reported as rare alongside the fault.
    """
    if not lines:
        return now
    oldest = min(line["tick"] for line in lines)
    return max(oldest + (now - oldest) // 2, now - RARE_RECENT_TICKS)


def template_of(message: str) -> str:
    """B. The template of a log line: the line with its variable parts replaced by placeholders.

    The grouping key summarize_logs needs. No two log lines are identical, so the key has to be computed.

    Examples (real lines from the cases):

        "INFO  order accepted id=O481920 member=NORDBANK side=buy"
            -> "INFO  order accepted id=<v> member=<v> side=<v>"

        "WARN  max_connections changed from 500 to 60 (CHG-4471); new connections above the limit are rejected"
            -> "WARN  max_connections changed from <n> to <n> (<id>); new connections above the limit are rejected"

        "ERROR connection refused to reference-db:5432 (attempt 2)"
            -> "ERROR connection refused to reference-db:<n> (attempt <n>)"

    The first collapses 318 lines into one key. The second is seen once and is the cause of
    s02-hidden-config-change; CHG-4471 survives in the example line summarize_logs keeps beside the template.
    """
    for pattern, placeholder in _SUBSTITUTIONS:
        message = pattern.sub(placeholder, message)
    return message


def summarize_logs(ctx: RunContext[IncidentDeps], component: str) -> LogSummary:
    """What is unusual in a component's logs, instead of every line of them.

    Groups the whole log by template (the line with its ids, values and numbers replaced) and counts each one.
    "rare" holds the templates seen at most twice or first seen recently, most unusual first, each with a full
    example line and its log ref to cite; the cause is almost always there. "common" holds the rest as counts
    only: thousands of identical errors are the symptom, not the cause.
    """
    lines = ctx.deps.tools.read_logs(component=component, limit=1_000_000)["lines"]
    now = ctx.deps.sim.state.tick
    new_since = _new_since(lines, now)

    groups: dict[str, dict[str, Any]] = {}
    for line in lines:
        template = template_of(line["message"])
        group = groups.get(template)
        if group is None:
            groups[template] = {
                "template": template,
                "level": line["level"],
                "count": 1,
                "first_tick": line["tick"],
                "first_seen": line["time"],
                "last_seen": line["time"],
                "instances": {line["instance"]},
                "example": line["message"],
                "ref": line["ref"],
            }
            continue
        group["count"] += 1
        group["last_seen"] = line["time"]
        group["instances"].add(line["instance"])

    rare: list[RareTemplate] = []
    common: list[Template] = []
    for group in groups.values():
        by_count = group["count"] <= RARE_MAX_COUNT
        if by_count or group["first_tick"] > new_since:
            rare.append(
                {
                    "count": group["count"],
                    "level": group["level"],
                    "template": group["template"],
                    "why_rare": "seen once or twice" if by_count else "first seen recently",
                    "first_seen": group["first_seen"],
                    "last_seen": group["last_seen"],
                    "instances": sorted(instance for instance in group["instances"] if instance),
                    "example": group["example"],
                    "ref": group["ref"],
                }
            )
        else:
            common.append({"count": group["count"], "level": group["level"], "template": group["template"]})

    rare.sort(key=lambda entry: entry["count"])
    common.sort(key=lambda entry: -entry["count"])
    return {
        "component": component,
        "lines_read": len(lines),
        "templates_found": len(groups),
        "rare": rare,
        "common": common[:MAX_COMMON_TEMPLATES],
        "common_truncated": len(common) > MAX_COMMON_TEMPLATES,
    }


class Runbook(TypedDict):
    """The one runbook that applies, with its text."""

    component: str
    version: str  # the version running in production, read from the metrics
    name: str
    runbook_version: int
    applies_to: str
    text: str


class NoRunbook(TypedDict):
    """No runbook declares this component and version. A different shape, so the agent cannot read a text
    that is not there."""

    component: str
    version: str
    runbook: None
    reason: str
    known_runbooks: list[str]


def _applies_to(applies_to: str, component: str, version: str) -> bool:
    """Does "order-gateway 2.4.x" cover this component running this version?

    Examples:

        ("order-gateway 2.4.x", "order-gateway", "2.4.0")  -> True
        ("order-gateway 2.3.x", "order-gateway", "2.4.0")  -> False
        ("reference-db 14.x",   "reference-db",  "14.2")   -> True

    The middle one is the point of the exercise: production runs 2.4.0, and the v1 runbook it rejects is the
    one that says to restart the whole service.
    """
    name, _, pattern = applies_to.rpartition(" ")
    return name == component and fnmatch(version, pattern.replace("x", "*"))


def find_runbook(ctx: RunContext[IncidentDeps], component: str) -> Runbook | NoRunbook:
    """The one runbook that applies to the version of a component running in production.

    Reads the running version from the metrics and returns only the runbook whose applies_to matches it. A
    component can have runbooks for several versions, and following the wrong one leads to the wrong action.
    Call it once you know which component is at fault, before you act.
    """
    version = ctx.deps.tools.read_metrics(component, 1)["readings"][-1]["version"]
    runbooks = ctx.deps.tools.list_runbooks()["runbooks"]
    matches = [runbook for runbook in runbooks if _applies_to(runbook["applies_to"], component, version)]
    if not matches:
        return {
            "component": component,
            "version": version,
            "runbook": None,
            "reason": f"No runbook declares applies_to for {component} {version}.",
            "known_runbooks": [f"{runbook['name']} v{runbook['version']} ({runbook['applies_to']})" for runbook in runbooks],
        }

    best = max(matches, key=lambda runbook: runbook["version"])
    full = ctx.deps.tools.read_runbook(best["name"], best["version"])
    return {
        "component": component,
        "version": version,
        "name": full["name"],
        "runbook_version": full["version"],
        "applies_to": full["applies_to"],
        "text": full["text"],
    }


class ToolError(TypedDict, total=False):
    """What the simulator returns instead of data when a backend fails. Every pass-through tool can return
    this, so the shape is part of their contract: `retryable` is absent when the failure is permanent."""

    error: str
    retryable: bool


class Metrics(TypedDict):
    """Recent readings of a component. `readings` stays loose on purpose: each entry carries latency_ms,
    error_rate, queue_depth, cpu_pct, memory_pct, version and tick, plus an `instances` breakdown only when
    per_instance is true."""

    component: str
    window_ticks: int
    readings: list[dict[str, Any]]
    source_ref: str  # cite this in the report


class Change(TypedDict):
    """One deployment or configuration change, and whether it is still in force."""

    change_id: str
    component: str
    kind: str
    description: str
    at_tick: int
    at: str
    active: bool


class Changes(TypedDict):
    changes: list[Change]
    total: int


def read_metrics(ctx: RunContext[IncidentDeps], component: str, window_ticks: int = 3, per_instance: bool = False) -> Metrics | ToolError:
    """Recent metrics of a component, one reading per minute. per_instance=true adds each instance.

    The default of 3 readings is what the runbooks use to judge recovery. Ask for more only when you need the
    history: a per-instance reading costs roughly 170 tokens per tick, and every reading you request is re-sent
    on every later request of this run.
    """
    return ctx.deps.tools.read_metrics(component, window_ticks, per_instance)


def recent_changes(ctx: RunContext[IncidentDeps], component: str | None = None) -> Changes | ToolError:
    """Recent deployments and configuration changes, and whether each one is still active."""
    return ctx.deps.tools.recent_changes(component)


# read_logs is gone on purpose: it returned every line, which is the problem
# this exercise is about. summarize_logs reads the same log and keeps what is
# unusual.
TOOLS = (read_metrics, recent_changes, summarize_logs, find_runbook)
