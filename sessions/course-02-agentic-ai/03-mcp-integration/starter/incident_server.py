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

from typing import Any

from fastmcp import FastMCP

from meridian.tools import MeridianTools


def correlate(event: dict[str, Any], open_incidents: list[dict[str, Any]]) -> str | None:
    """Return the key of the open incident this event belongs to, or None to open a new incident."""
    # Hint: first decide the component(s) and the kind of symptom of the event; then, for each open
    # incident still inside the time window, ask "same component, or a shared dependency for errors?".
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

    # Write the description: when to call it, what to pass in each argument, what comes back.
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

    # Write the description.
    @server.tool(description="List incidents.", run_in_thread=False)
    def list_incidents() -> dict[str, Any]:
        def run() -> dict[str, Any]:
            return {"incidents": [{key: value for key, value in incident.items() if key != "events"} | {"events": len(incident["events"])} for incident in registry.values()]}

        return tools.record_call("incidents.list_incidents", {}, run)

    return server
