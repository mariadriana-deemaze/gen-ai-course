"""Tools that agents can use on the simulated exchange.

These are plain Python methods. Each session decides which of them to expose
to the model and with which interface (name, description, arguments).

Every call is recorded in a trace (tool, arguments, short result) and moves
simulated time forward by the scenario's `ticks_per_action`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from meridian.actions import ActionTimeout
from meridian.model import DATA_DIR
from meridian.simulator import Simulator

RUNBOOKS_DIR = DATA_DIR / "runbooks"


@dataclass
class ToolCall:
    step: int
    tick: int
    time: str
    tool: str
    args: dict[str, Any]
    result: Any
    error: str | None = None


def load_runbooks() -> list[dict[str, Any]]:
    runbooks = []
    for path in sorted(RUNBOOKS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.S)
        if not match:
            continue
        meta: dict[str, Any] = {}
        for line in match.group(1).splitlines():
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip().strip('"')
        runbooks.append({"name": meta["name"], "version": int(meta["version"]), "applies_to": meta.get("applies_to", ""), "text": match.group(2).strip()})
    return runbooks


@dataclass
class MeridianTools:
    sim: Simulator
    actor: str = "agent-first-line"
    trace: list[ToolCall] = field(default_factory=list)
    _calls: dict[str, int] = field(default_factory=dict)

    # Plumbing ------------------------------------------------------------

    def _run(self, tool: str, args: dict[str, Any], fn) -> Any:
        self._calls[tool] = self._calls.get(tool, 0) + 1
        tick = self.sim.state.tick
        error = None
        behaviour = self.sim.scenario.tools.get(tool, {})
        if behaviour.get("unavailable"):
            result: Any = {"error": f"{tool} backend unavailable", "retryable": False}
            error = result["error"]
        elif self._calls[tool] in behaviour.get("fail_calls", []):
            result = {"error": f"{tool} timed out", "retryable": True}
            error = result["error"]
        else:
            try:
                result = fn()
            except ActionTimeout as timeout:
                result = {"error": str(timeout), "retryable": False}
                error = "timeout"
            except KeyError as missing:
                result = {"error": str(missing).strip("'\"")}
                error = result["error"]
        if error is None and isinstance(result, dict) and isinstance(result.get("error"), str):
            error = result["error"]
        self.trace.append(ToolCall(len(self.trace) + 1, tick, self.sim.clock.label(tick), tool, args, result, error))
        self.sim.agent_action()
        return result

    def record_call(self, tool: str, args: dict[str, Any], fn) -> Any:
        """Run `fn` as a traced tool call: recorded in the trace and moving time forward, like the built-in tools.

        Use it in tools and MCP servers you write yourself, so that their calls appear in the trace and the checks.
        """
        return self._run(tool, args, fn)

    # Reading -------------------------------------------------------------

    def read_alert(self, alert_id: str) -> dict[str, Any]:
        def fn():
            for alert in self.sim.alerts.alerts:
                if alert.alert_id == alert_id:
                    return alert.public()
            raise KeyError(f"Unknown alert: {alert_id}")

        return self._run("read_alert", {"alert_id": alert_id}, fn)

    def list_alerts(self, state: str = "open", component: str | None = None, limit: int = 50) -> dict[str, Any]:
        def fn():
            alerts = [
                alert.public()
                for alert in self.sim.alerts.alerts
                if (state == "all" or alert.state == state) and (component is None or alert.component == component)
            ]
            return {"total": len(alerts), "returned": min(limit, len(alerts)), "truncated": len(alerts) > limit, "alerts": alerts[:limit]}

        return self._run("list_alerts", {"state": state, "component": component, "limit": limit}, fn)

    def read_metrics(self, component: str, window_ticks: int = 5, per_instance: bool = False) -> dict[str, Any]:
        def fn():
            self.sim.state.component(component)
            readings = self.sim.metrics.series(component, max(1, min(window_ticks, 30)))
            if not per_instance:
                readings = [{key: value for key, value in reading.items() if key != "instances"} for reading in readings]
            last_tick = readings[-1]["tick"]
            return {"component": component, "window_ticks": len(readings), "readings": readings, "source_ref": f"metrics/{component}/{last_tick}"}

        return self._run("read_metrics", {"component": component, "window_ticks": window_ticks, "per_instance": per_instance}, fn)

    def read_logs(
        self,
        component: str | None = None,
        contains: str | None = None,
        level: str | None = None,
        since_tick: int | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        def fn():
            lines = [
                line
                for line in self.sim.logs.lines
                if (component is None or line["component"] == component)
                and (contains is None or contains.lower() in line["message"].lower())
                and (level is None or line["level"] == level.upper())
                and (since_tick is None or line["tick"] >= since_tick)
            ]
            selected = lines[-limit:]
            return {"total_matching": len(lines), "returned": len(selected), "truncated": len(lines) > limit, "lines": selected}

        args = {"component": component, "contains": contains, "level": level, "since_tick": since_tick, "limit": limit}
        return self._run("read_logs", args, fn)

    def check_link(self, source: str, target: str) -> dict[str, Any]:
        def fn():
            link = self.sim.state.link(source, target)
            return {"source": source, "target": target, "state": link.state, "observed_tick": self.sim.state.tick}

        return self._run("check_link", {"source": source, "target": target}, fn)

    def recent_changes(self, component: str | None = None, limit: int = 10) -> dict[str, Any]:
        def fn():
            changes = [
                {
                    "change_id": change.change_id,
                    "component": change.component,
                    "kind": change.kind,
                    "description": change.description,
                    "at_tick": change.at,
                    "at": self.sim.clock.timestamp(change.at),
                    "active": change.active,
                }
                for change in sorted(self.sim.state.changes, key=lambda change: change.at, reverse=True)
                if component is None or change.component == component
            ]
            return {"changes": changes[:limit], "total": len(changes)}

        return self._run("recent_changes", {"component": component, "limit": limit}, fn)

    def list_runbooks(self) -> dict[str, Any]:
        def fn():
            return {"runbooks": [{key: runbook[key] for key in ("name", "version", "applies_to")} for runbook in load_runbooks()]}

        return self._run("list_runbooks", {}, fn)

    def read_runbook(self, name: str, version: int | None = None) -> dict[str, Any]:
        def fn():
            matches = [runbook for runbook in load_runbooks() if runbook["name"] == name and (version is None or runbook["version"] == version)]
            if not matches:
                raise KeyError(f"Unknown runbook: {name} (version {version})")
            return max(matches, key=lambda runbook: runbook["version"])

        return self._run("read_runbook", {"name": name, "version": version}, fn)

    def exchange_status(self) -> dict[str, Any]:
        def fn():
            tick = self.sim.state.tick
            return {
                "time": self.sim.clock.timestamp(tick),
                "trading_session_open": self.sim.clock.is_trading_session(tick),
                "trading_hours": f"{self.sim.state.trading_open}-{self.sim.state.trading_close}",
            }

        return self._run("exchange_status", {}, fn)

    # Tickets (native access; Session 3 exposes the same data through MCP) --

    def search_tickets(self, text: str | None = None, status: str | None = None, component: str | None = None, limit: int = 20) -> dict[str, Any]:
        def fn():
            found = self.sim.tickets.search(text=text, status=status, component=component)
            return {"total": len(found), "tickets": [ticket.public(with_comments=False) for ticket in found[:limit]]}

        return self._run("search_tickets", {"text": text, "status": status, "component": component, "limit": limit}, fn)

    def read_ticket(self, key: str) -> dict[str, Any]:
        return self._run("read_ticket", {"key": key}, lambda: self.sim.tickets.get(key).public())

    def comment_ticket(self, key: str, body: str) -> dict[str, Any]:
        def fn():
            tick = self.sim.state.tick
            return {"key": key, "comment": self.sim.tickets.comment(key, body, self.actor, tick, self.sim.clock)}

        return self._run("comment_ticket", {"key": key, "body": body}, fn)

    # Approvals and outbound messages ---------------------------------------

    def request_approval(self, action: str, target: str, reason: str, approver_role: str = "on-call") -> dict[str, Any]:
        args = {"action": action, "target": target, "reason": reason, "approver_role": approver_role}
        return self._run("request_approval", args, lambda: self.sim.request_approval(action, target, reason, approver_role, self.actor))

    def send_message(self, channel: str, text: str) -> dict[str, Any]:
        return self._run("send_message", {"channel": channel, "text": text}, lambda: self.sim.send_message(channel, text, self.actor))

    # Actions ---------------------------------------------------------------

    def restart_instance(self, instance_id: str, idempotency_key: str | None = None, confirmation_ref: str | None = None) -> dict[str, Any]:
        args = {"instance_id": instance_id, "idempotency_key": idempotency_key, "confirmation_ref": confirmation_ref}
        return self._run("restart_instance", args, lambda: self.sim.executor.execute("restart_instance", instance_id, idempotency_key=idempotency_key, actor=self.actor, confirmation_ref=confirmation_ref))

    def restart_service(self, component: str, idempotency_key: str | None = None, confirmation_ref: str | None = None) -> dict[str, Any]:
        args = {"component": component, "idempotency_key": idempotency_key, "confirmation_ref": confirmation_ref}
        return self._run("restart_service", args, lambda: self.sim.executor.execute("restart_service", component, idempotency_key=idempotency_key, actor=self.actor, confirmation_ref=confirmation_ref))

    def rollback_change(self, change_id: str, idempotency_key: str | None = None, confirmation_ref: str | None = None) -> dict[str, Any]:
        args = {"change_id": change_id, "idempotency_key": idempotency_key, "confirmation_ref": confirmation_ref}
        return self._run("rollback_change", args, lambda: self.sim.executor.execute("rollback_change", change_id, idempotency_key=idempotency_key, actor=self.actor, confirmation_ref=confirmation_ref))

    def action_status(self, idempotency_key: str) -> dict[str, Any]:
        return self._run("action_status", {"idempotency_key": idempotency_key}, lambda: self.sim.executor.status(idempotency_key))
