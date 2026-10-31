"""Exercise 1 — Your MCP server: one incident, several sources.

The problem
    10:00. The gateway starts leaking memory. In ten minutes the team receives a
    monitoring alert, a ticket (MRX-2210) and an e-mail from a member: "our
    orders are acknowledged after 2-3 seconds". It is one problem. The agent
    opens three incidents, and three people start working on it.

    At 11:00 the opposite: market-data gets slow while the gateway has errors.
    Same time, similar words, two problems. Merging them hides one.

    Before any code: events/dev.yaml has the 22 events of one morning. How many
    incidents are there? Answer by hand, and write down how you decided.

Build, in this file
    - correlate(event, open_incidents): the key of the open incident this event
      belongs to, or None to open a new one. As supplied it always returns None.
    - The descriptions of the two server tools (report_event, list_incidents):
      when the model must call them, with what, and what it gets back. Today
      they say "Report an event." They are all the model knows about your server.

Watch out
    - Complaints do not name a component: "orders are acknowledged after 2-3
      seconds" is the gateway, but the word "gateway" is not there.
    - Time: the same leak at 10:00 and at 15:00 are two incidents.
    - A shared cause: errors on the gateway and on settlement at the same time
      can be one incident, because both depend on reference-db.
    - Same time is not same incident: latency on market-data next to errors on the gateway.
    - Requests ("add member VALE-INV to the test environment") are not incidents.
    - Two ways to fail: merging too much hides an incident, splitting too much
      duplicates work. check_correlation.py measures both (precision and recall).

Shapes used by correlate

    event = {"source": "monitoring" | "ticket" | "complaint",
             "time": 665,                    # minutes since midnight
             "component": "market-data",     # may be None (complaints, some tickets)
             "text": "latency_high on market-data (353 ms)"}

    incident = {"key": "MRX-2210",
                "components": ["order-gateway"],
                "first_seen": 610, "last_seen": 612,
                "events": [event, ...]}

    The topology is in meridian.model.load_topology(); each component has depends_on.

Hints (read only if stuck)
    1. Write the rules you used by hand, one per line. Most become one `if` each.
    2. Component of a complaint: a small table of words per component
       ("acknowledged", "orders", "rejected" -> order-gateway; "prices", "quotes" -> market-data).
    3. Classify the symptom too: slow or errors. Latency on one component and errors
       on another are rarely the same incident; errors on two components that share
       a dependency usually are.
    4. Compare the event time with last_seen, not first_seen. Start with 30 minutes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, TypedDict

from fastmcp import FastMCP
from pydantic import Field

from meridian.model import load_topology
from meridian.tools import MeridianTools

# The shapes this file works with, as the exercise defines them.


class Event(TypedDict):
    """One thing that happened: an alert, a ticket or a member complaint."""

    source: str
    time: int  # Minutes since midnight.
    component: str | None  # Complaints and some tickets do not name one.
    text: str


class Incident(TypedDict):
    """An open incident and the events attached to it so far."""

    key: str
    components: list[str]
    first_seen: int
    last_seen: int
    events: list[Event]


# Correlation
#
# Three facts are derived from every event: is it an incident at all, which
# component it is about, and how that component is failing. Events join an open
# incident only when the component matches (or shares a direct dependency) and
# the way it fails has not changed.

# An incident goes quiet after half an hour: the same leak at 10:00 and at 15:00 are two
# incidents. The symptom class, not this window, is what separates two episodes that are
# close in time, so the value can be raised without changing the grouping.
WINDOW_MINUTES = 30

# Complaints describe symptoms, not components: "our orders are acknowledged
# after 2-3 seconds" is the order-gateway, but the word is never there.
COMPONENT_WORDS: dict[str, tuple[str, ...]] = {
    "order-gateway": ("acknowledg", "order", "reject", "503", "fill", "execution"),
    "market-data": ("quote", "price", "market data", "feed", "screen", "tick"),
    "settlement": ("settle", "clearing", "batch"),
}

# The symptom class, not a severity. "neutral" is for saturation signals that say a
# component is unhealthy without saying how it fails; they join whatever is open on it.
SLOW_WORDS = ("latency", "slow", "late", "delay", "lag", "seconds")
ERROR_WORDS = ("error", "reject", "503", "500", "fail", "refused", "exception", "unavailable")
NEUTRAL_WORDS = ("memory_pressure", "queue_backlog", "cpu_", "disk_", "saturat")

# Not incidents: nobody is paged for them, and they must never join one. Defensive only: the
# requests in events/dev.yaml name no component, so they open their own group regardless. This
# catches one that does, such as "Request: order entry onboarding is slow".
REQUEST_WORDS = ("request:", "please send", "please add", "please create", "add member")


@lru_cache(maxsize=1)
def _dependencies() -> dict[str, frozenset[str]]:
    """The direct dependencies of each component, from the topology."""
    return {name: frozenset(component.depends_on) for name, component in load_topology().components.items()}


def _text(event: Event) -> str:
    return str(event.get("text") or "").lower()


def _is_request(event: Event) -> bool:
    return any(word in _text(event) for word in REQUEST_WORDS)


def _component_of(event: Event) -> str | None:
    """The component the event is about: the field when present, otherwise the words."""
    if event.get("component"):
        return str(event["component"])
    text = _text(event)
    for name, words in COMPONENT_WORDS.items():
        if any(word in text for word in words):
            return name
    return None


def _symptom_of(event: Event) -> str | None:
    """"errors", "slow", "neutral", or None when the text says none of them.

    The class is decided by the word that comes first, because an event leads with its symptom and
    mentions the rest afterwards: "latency_high on order-gateway, severity error" is a slow gateway,
    not a failing one, and "error_rate_high on order-gateway, latency 521 ms" is the opposite.
    """
    text = _text(event)
    first: tuple[int, str] | None = None
    for symptom, words in (("neutral", NEUTRAL_WORDS), ("errors", ERROR_WORDS), ("slow", SLOW_WORDS)):
        found = [text.index(word) for word in words if word in text]
        if found and (first is None or min(found) < first[0]):
            first = (min(found), symptom)
    return first[1] if first else None


def _symptom_of_incident(incident: Incident) -> str | None:
    """How this incident fails: the class of its first event that commits to one."""
    for event in incident.get("events", []):
        symptom = _symptom_of(event)
        if symptom and symptom != "neutral":
            return symptom
    return None


def _share_a_dependency(component: str, other: str) -> bool:
    dependencies = _dependencies()
    return bool(dependencies.get(component, frozenset()) & dependencies.get(other, frozenset()))


def correlate(event: Event, open_incidents: list[Incident]) -> str | None:
    """Return the key of the open incident this event belongs to, or None to open a new incident."""
    if _is_request(event):
        return None
    component = _component_of(event)
    symptom = _symptom_of(event)
    if not component or not symptom:
        # Unknown component or unknown symptom: open an incident rather than guess a merge.
        return None

    recent = [
        incident
        for incident in open_incidents
        if incident.get("components")
        and event["time"] - incident["last_seen"] <= WINDOW_MINUTES
        # A group of requests is not an incident, even when its ticket names a component.
        and not all(_is_request(earlier) for earlier in incident["events"])
    ]
    for incident in sorted(recent, key=lambda incident: incident["last_seen"], reverse=True):
        components = list(incident["components"])
        known = _symptom_of_incident(incident)
        if component in components:
            if symptom == "neutral" or known is None or symptom == known:
                return incident["key"]
            continue  # Same component, but it fails differently now: a new episode.
        if symptom == "errors" and known == "errors" and any(_share_a_dependency(component, other) for other in components):
            return incident["key"]  # Errors on two components with a shared dependency: one cause.
    return None


# The server -----------------------------------------------------------------


def _minutes(tools: MeridianTools, tick: int) -> int:
    return tools.sim.clock.minutes_at(tick) % (24 * 60)


def _open_incidents(tools: MeridianTools) -> dict[str, Incident]:
    """Incident tickets already open when the run starts, in the shape `correlate` expects."""
    registry = {}
    for ticket in tools.sim.tickets.tickets:
        if ticket.project == "MRX" and ticket.issue_type == "Incident" and ticket.status not in ("Resolved", "Closed"):
            seen = _minutes(tools, ticket.created_tick)
            event = {"source": "ticket", "time": seen, "component": ticket.component, "text": f"{ticket.summary}. {ticket.description}"}
            registry[ticket.key] = {"key": ticket.key, "components": [ticket.component] if ticket.component else [], "first_seen": seen, "last_seen": seen, "events": [event]}
    return registry


def build_server(tools: MeridianTools) -> FastMCP:
    server = FastMCP("incidents", instructions="Registry of the incidents handled by the operations team.", version="0.1.0")
    registry = _open_incidents(tools)
    sim = tools.sim

    @server.tool(
        description=(
            "Report one thing that happened: the alert you were sent, a ticket you found, or a member complaint. "
            "Call it once for each of them, as soon as you have read it and before you act on it. "
            "This server decides whether the event belongs to an incident that is already open or starts a new one, "
            "and it comments on that incident ticket for you, so do not open or update incident tickets yourself. "
            "It answers the incident key, whether the event was attached to an open incident (attached) or opened "
            "one (new_incident), and how many events that incident now holds (events_in_incident)."
        ),
        run_in_thread=False,
    )
    def report_event(
        source: Annotated[
            str,
            Field(description="Where the event came from: 'monitoring' for an alert, 'ticket' for an issue in the tracker, 'complaint' for a member's words. One of those three."),
        ],
        text: Annotated[
            str,
            Field(description="What happened, in the words of the source: the alert line, the ticket summary and description, or the member's own sentence. Keep the measurements and names that are in it; correlation reads these words."),
        ],
        component: Annotated[
            str | None,
            Field(description="The component the event is about, named as the exchange names it: order-gateway, market-data, matching-engine, settlement or reference-db. Leave it out when the source does not name one, as complaints usually do not; this server infers it from the text rather than having you guess."),
        ] = None,
    ) -> dict[str, object]:
        def run() -> dict[str, object]:
            event: Event = {"source": source, "time": _minutes(tools, sim.state.tick), "component": component, "text": text}
            key = correlate(event, list(registry.values()))
            if key in registry:
                incident = registry[key]
                incident["events"].append(event)
                incident["last_seen"] = event["time"]
                if component and component not in incident["components"]:
                    incident["components"].append(component)
                sim.tickets.comment(key, f"[{source}] {text}", tools.actor, sim.state.tick, sim.clock)
                return {"incident": key, "attached": True, "events_in_incident": len(incident["events"])}
            ticket = sim.tickets.create(
                {"project": "MRX", "issue_type": "Incident", "summary": text[:80], "description": f"[{source}] {text}", "component": component},
                tools.actor,
                sim.state.tick,
                sim.clock,
            )
            opened: Incident = {"key": ticket.key, "components": [component] if component else [], "first_seen": event["time"], "last_seen": event["time"], "events": [event]}
            registry[ticket.key] = opened
            return {"incident": ticket.key, "attached": False, "new_incident": True}

        return tools.record_call("incidents.report_event", {"source": source, "text": text, "component": component}, run)

    @server.tool(
        description=(
            "List the incidents open in this run, each with the components it touches, the minutes it was first and "
            "last seen, and how many events it holds. Call it before you report your first event, to see what is "
            "already open, and again before you conclude, to check what your events were attached to. "
            "It takes no arguments and changes nothing."
        ),
        run_in_thread=False,
    )
    def list_incidents() -> dict[str, object]:
        def run() -> dict[str, object]:
            summaries = [
                {
                    "key": incident["key"],
                    "components": incident["components"],
                    "first_seen": incident["first_seen"],
                    "last_seen": incident["last_seen"],
                    "events": len(incident["events"]),
                }
                for incident in registry.values()
            ]
            return {"incidents": summaries}

        return tools.record_call("incidents.list_incidents", {}, run)

    return server
