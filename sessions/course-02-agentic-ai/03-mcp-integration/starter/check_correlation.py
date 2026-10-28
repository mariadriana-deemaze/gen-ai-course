"""Score a correlation function on a set of events, without a model.

    uv run python sessions/course-02-agentic-ai/03-mcp-integration/starter/check_correlation.py --server sessions/course-02-agentic-ai/03-mcp-integration/starter/incident_server.py

The events (events/dev.yaml) are fed one by one, in order, to
`correlate(event, open_incidents)` from your incident_server.py. The script
keeps the open incidents exactly as the server does, then compares the
grouping with the right answer:

- precision: of the pairs of events you put together, how many belong together;
- recall: of the pairs that belong together, how many you put together;
- F1: both at once. Done when F1 >= 0.90.
"""

from __future__ import annotations

import argparse
import importlib.util
from itertools import combinations
from pathlib import Path
from typing import Any

import yaml

EVENTS = Path(__file__).resolve().parents[1] / "events" / "dev.yaml"


def load_correlate(path: str) -> Any:
    spec = importlib.util.spec_from_file_location("student_incident_server", Path(path).resolve())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.correlate


def group(events: list[dict[str, Any]], correlate: Any) -> dict[str, str]:
    incidents: dict[str, dict[str, Any]] = {}
    assigned: dict[str, str] = {}
    for event in events:
        visible = {key: value for key, value in event.items() if key not in ("id", "incident")}
        visible.setdefault("component", None)
        key = correlate(visible, [dict(incident) for incident in incidents.values()])
        if key not in incidents:
            key = f"INC-{len(incidents) + 1}"
            incidents[key] = {"key": key, "components": [], "first_seen": visible["time"], "last_seen": visible["time"], "events": []}
        incident = incidents[key]
        incident["events"].append(visible)
        incident["last_seen"] = visible["time"]
        if visible["component"] and visible["component"] not in incident["components"]:
            incident["components"].append(visible["component"])
        assigned[event["id"]] = key
    return assigned


def score(events: list[dict[str, Any]], assigned: dict[str, str]) -> tuple[float, float, float]:
    truth = {event["id"]: event["incident"] for event in events}
    predicted_pairs = {pair for pair in combinations(sorted(truth), 2) if assigned[pair[0]] == assigned[pair[1]]}
    true_pairs = {pair for pair in combinations(sorted(truth), 2) if truth[pair[0]] == truth[pair[1]]}
    hits = len(predicted_pairs & true_pairs)
    precision = hits / len(predicted_pairs) if predicted_pairs else 0.0
    recall = hits / len(true_pairs) if true_pairs else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--server", required=True, help="Your incident_server.py")
    parser.add_argument("--events", default=str(EVENTS))
    args = parser.parse_args()
    events = yaml.safe_load(Path(args.events).read_text(encoding="utf-8"))["events"]
    assigned = group(events, load_correlate(args.server))

    by_incident: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_incident.setdefault(assigned[event["id"]], []).append(event)
    print(f"{len(events)} events -> {len(by_incident)} incidents\n")
    for key, members in by_incident.items():
        print(f"{key}")
        for event in members:
            mark = "" if all(other["incident"] == event["incident"] for other in members) else "   <- mixed"
            print(f"   {event['id']} {event['time']:>4} {event['source']:<10} {event.get('component') or '-'!s:<15} {event['text'][:60]}{mark}")
    precision, recall, f1 = score(events, assigned)
    print(f"\nprecision {precision:.2f}   recall {recall:.2f}   F1 {f1:.2f}   ->  {'PASS' if f1 >= 0.9 else 'FAIL'} (target F1 >= 0.90)")


if __name__ == "__main__":
    main()
