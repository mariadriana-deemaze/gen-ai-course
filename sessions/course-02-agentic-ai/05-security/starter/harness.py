"""Session 5: the on-call agent. This file only wires the pieces; you do not need to edit it.

Your work is in:
- policy.py     exercise 1: who may do what, when, with whose approval;
- privacy.py    exercise 2: personal data masked before the model, messages checked before they leave;
- untrusted.py  exercise 3: text that gives orders, and actions without evidence.

How the pieces are used:
- every tool result from logs, tickets and memory goes through privacy.mask, then untrusted.screen;
- every action goes through policy.authorize, then untrusted.justified, before the executor;
- every message goes through privacy.check_message before it is sent.

As supplied, every check lets everything through. The run acts as the case's
identity (deps.identity): first-line agent, on-call engineer or incident
manager. Use --role to try another one.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from meridian.agent_runtime import IncidentDeps
from meridian.security import describe_action

HERE = Path(__file__).resolve().parent


def _load(name: str) -> Any:
    """Load a module from a file next to this one."""
    path = HERE / f"{name}.py"
    module_name = f"{name}_{abs(hash(path)) % 100_000}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


policy = _load("policy")
privacy = _load("privacy")
untrusted = _load("untrusted")


class IncidentReport(BaseModel):
    incident_id: str
    status: Literal["recovered", "not_recovered", "insufficient_information", "escalated"]
    evidence: list[str]
    actions_taken: list[str]
    summary: str


# What reaches the model ----------------------------------------------------------


def _mask_all(value: Any) -> Any:
    if isinstance(value, str):
        return privacy.mask(value)
    if isinstance(value, dict):
        return {key: _mask_all(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_mask_all(item) for item in value]
    return value


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _untrusted(source: str, result: dict[str, Any]) -> dict[str, Any]:
    """Text written by others: mask personal data, then flag text that gives orders."""
    result = _mask_all(result)
    warnings = [warning for text in _strings(result) if (warning := untrusted.screen(text))]
    if warnings:
        result = {**result, "untrusted_text_warnings": sorted(set(warnings))[:5]}
    return result


def exchange_status(ctx: RunContext[IncidentDeps]) -> dict[str, Any]:
    """Current date and time, and whether the trading session is open."""
    return ctx.deps.tools.exchange_status()


def read_metrics(ctx: RunContext[IncidentDeps], component: str, window_ticks: int = 5, per_instance: bool = False) -> dict[str, Any]:
    """Recent metrics of a component, one reading per minute. per_instance=true shows each instance (memory, status)."""
    return ctx.deps.tools.read_metrics(component, window_ticks, per_instance)


def recent_changes(ctx: RunContext[IncidentDeps], component: str | None = None) -> dict[str, Any]:
    """Recent deployments and configuration changes, and whether each one is still active."""
    return ctx.deps.tools.recent_changes(component)


def read_runbook(ctx: RunContext[IncidentDeps], name: str) -> dict[str, Any]:
    """Read the latest version of a runbook: order-gateway-latency, reference-db-connections or market-data-delays."""
    return ctx.deps.tools.read_runbook(name)


def read_logs(ctx: RunContext[IncidentDeps], component: str, level: str | None = None) -> dict[str, Any]:
    """The 50 most recent log lines of a component."""
    return _untrusted("logs", ctx.deps.tools.read_logs(component=component, level=level, limit=50))


def search_tickets(ctx: RunContext[IncidentDeps], text: str) -> dict[str, Any]:
    """Search tickets."""
    return _untrusted("tickets", ctx.deps.tools.search_tickets(text=text))


def read_ticket(ctx: RunContext[IncidentDeps], key: str) -> dict[str, Any]:
    """Read a ticket with its description and comments."""
    return _untrusted("tickets", ctx.deps.tools.read_ticket(key))


def recall(ctx: RunContext[IncidentDeps], query: str) -> dict[str, Any]:
    """Search long-term memory: known issues, procedures and notes from earlier shifts."""
    if ctx.deps.memory is None:
        return {"error": "memory is disabled for this run"}
    records = ctx.deps.memory.search(text=query, team=ctx.deps.identity["team"])
    return _untrusted("memory", {"memories": [record.public() for record in records]})


# Approvals, actions and messages ------------------------------------------------


def request_approval(ctx: RunContext[IncidentDeps], action: str, target: str, reason: str, approver_role: str = "on-call") -> dict[str, Any]:
    """Ask a human to approve one action (restart_instance, restart_service or rollback_change) on one target.
    approver_role: on-call or incident-manager. Pass the approval_ref you get as confirmation_ref of the action."""
    return ctx.deps.tools.request_approval(action, target, reason, approver_role)


def _act(ctx: RunContext[IncidentDeps], action: str, target: str, confirmation_ref: str | None, execute: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    request = describe_action(ctx.deps, action, target, confirmation_ref)
    decision = policy.authorize(request)
    if decision.get("decision") != "allow":
        return {"status": decision.get("decision", "deny"), "reason": decision.get("reason", ""), "approver_role": decision.get("approver_role")}
    objection = untrusted.justified(request, ctx.deps)
    if objection:
        return {"status": "refused", "reason": objection}
    return execute(f"{ctx.deps.sim.scenario.incident['id']}:{action}:{target}")


def restart_instance(ctx: RunContext[IncidentDeps], instance_id: str, confirmation_ref: str | None = None) -> dict[str, Any]:
    """Restart one instance, e.g. order-gateway-2. Safe to repeat in the same incident."""
    return _act(ctx, "restart_instance", instance_id, confirmation_ref, lambda key: ctx.deps.tools.restart_instance(instance_id, idempotency_key=key, confirmation_ref=confirmation_ref))


def restart_service(ctx: RunContext[IncidentDeps], component: str, confirmation_ref: str | None = None) -> dict[str, Any]:
    """Restart every instance of a component. Safe to repeat in the same incident."""
    return _act(ctx, "restart_service", component, confirmation_ref, lambda key: ctx.deps.tools.restart_service(component, idempotency_key=key, confirmation_ref=confirmation_ref))


def rollback_change(ctx: RunContext[IncidentDeps], change_id: str, confirmation_ref: str | None = None) -> dict[str, Any]:
    """Roll back a deployment or configuration change, e.g. CHG-4471. Safe to repeat in the same incident."""
    return _act(ctx, "rollback_change", change_id, confirmation_ref, lambda key: ctx.deps.tools.rollback_change(change_id, idempotency_key=key, confirmation_ref=confirmation_ref))


def send_message(ctx: RunContext[IncidentDeps], channel: str, text: str) -> dict[str, Any]:
    """Send a message. Channels: members-status (all members), ops-internal, or email:<address>."""
    objection = privacy.check_message(channel, text)
    if objection:
        return {"status": "blocked", "reason": objection}
    return ctx.deps.tools.send_message(channel, text)


TOOLS = (
    exchange_status,
    read_metrics,
    read_logs,
    recent_changes,
    search_tickets,
    read_ticket,
    recall,
    read_runbook,
    request_approval,
    restart_instance,
    restart_service,
    rollback_change,
    send_message,
)


def build_agent(model: Any) -> Agent[IncidentDeps, IncidentReport]:
    agent = Agent(model, deps_type=IncidentDeps, output_type=IncidentReport, instructions=untrusted.INSTRUCTIONS)
    for tool in TOOLS:
        agent.tool(tool)
    return agent
