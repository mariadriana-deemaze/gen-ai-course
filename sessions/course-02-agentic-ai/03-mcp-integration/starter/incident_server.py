"""Exercise 1: your own MCP server, `incidents`.

Events about the same problem arrive from several places: a monitoring alert,
a ticket, a member complaint. This server is where the agent reports every
event it handles. The server decides whether the event belongs to an incident
that is already open (and attaches it there) or starts a new one.

The decision is `correlate(event, open_incidents)`. As supplied, it always
answers "new incident", so every event opens its own incident. Your job:
- write `correlate` (test it without a model: check_correlation.py);
- write the tool descriptions, so that the model knows when to call each tool
  and what it gets back.

Shapes used by `correlate`:

    event = {"source": "monitoring" | "ticket" | "complaint",
             "time": 665,                    # minutes since midnight
             "component": "market-data",     # may be None (complaints, some tickets)
             "text": "latency_high on market-data (353 ms)"}

    incident = {"key": "MRX-2210",
                "components": ["order-gateway"],
                "first_seen": 610, "last_seen": 612,
                "events": [event, ...]}

The topology (which component depends on which) is available with
`meridian.model.load_topology()`; each component has `depends_on`.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from meridian.tools import MeridianTools


def correlate(event: dict[str, Any], open_incidents: list[dict[str, Any]]) -> str | None:
    """Return the key of the open incident this event belongs to, or None to open a new incident."""
    return None


# The server -----------------------------------------------------------------


def _minutes(tools: MeridianTools, tick: int) -> int:
    return tools.sim.clock.minutes_at(tick) % (24 * 60)


def _open_incidents(tools: MeridianTools) -> dict[str, dict[str, Any]]:
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

    @server.tool(description="Report an event.", run_in_thread=False)
    def report_event(source: str, text: str, component: str | None = None) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            event = {"source": source, "time": _minutes(tools, sim.state.tick), "component": component, "text": text}
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
            registry[ticket.key] = {"key": ticket.key, "components": [component] if component else [], "first_seen": event["time"], "last_seen": event["time"], "events": [event]}
            return {"incident": ticket.key, "attached": False, "new_incident": True}

        return tools.record_call("incidents.report_event", {"source": source, "text": text, "component": component}, run)

    @server.tool(description="List incidents.", run_in_thread=False)
    def list_incidents() -> dict[str, Any]:
        def run() -> dict[str, Any]:
            return {"incidents": [{key: value for key, value in incident.items() if key != "events"} | {"events": len(incident["events"])} for incident in registry.values()]}

        return tools.record_call("incidents.list_incidents", {}, run)

    return server
