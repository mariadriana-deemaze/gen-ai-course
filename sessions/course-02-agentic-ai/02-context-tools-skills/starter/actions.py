"""Exercise 3 — Completed action tools.

Action keys come from the incident, action and target, not from the model.
The executor deduplicates effects; these wrappers check conditions and reconcile
lost responses. Whole-service restarts default to a dry run.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext

from meridian.agent_runtime import IncidentDeps

MEMORY_LIMIT_PCT = 90


def intent_key(deps: IncidentDeps, action: str, target: str) -> str:
    """Give repeated requests for the same action in one incident the same key."""
    return f"{deps.sim.scenario.incident['id']}:{action}:{target}"


def guarded(deps: IncidentDeps, action: str, target: str, execute: Any) -> dict[str, Any]:
    """Check for an earlier action, execute once, and look up a lost response."""
    key = intent_key(deps, action, target)
    known = deps.tools.action_status(key)
    if known["status"] in ("completed", "already_applied"):
        return {
            "status": "already_done",
            "action_id": known["action_id"],
            "idempotency_key": key,
            "detail": f"This {action} on {target} was already carried out. Not repeated.",
        }

    result = execute(key)
    if "error" in result and "timeout" in str(result["error"]).lower():
        # A timeout does not tell us whether the action happened. Ask the executor.
        known = deps.tools.action_status(key)
        return {**known, "reconciled_after_timeout": True}
    return result


def restart_instance(ctx: RunContext[IncidentDeps], instance_id: str) -> dict[str, Any]:
    """Restart one non-critical instance only if memory exceeds 90% or it is not running.

    Repeated requests use the same action key. If the condition no longer holds,
    the restart is refused. Critical components still require executor approval.
    """
    component = instance_id.rsplit("-", 1)[0]
    metrics = ctx.deps.tools.read_metrics(component, 1, per_instance=True)
    if "error" in metrics:
        return {"status": "refused", "detail": f"Cannot check {instance_id} before restarting: {metrics['error']}"}
    state = metrics["readings"][-1]["instances"].get(instance_id)
    if state is None:
        return {"status": "refused", "detail": f"Unknown instance {instance_id}"}
    if state["status"] == "running" and state["memory_pct"] <= MEMORY_LIMIT_PCT:
        return {"status": "refused", "detail": f"{instance_id} is running with memory {state['memory_pct']}%. The restart condition does not hold."}

    return guarded(
        ctx.deps,
        "restart_instance",
        instance_id,
        lambda key: ctx.deps.tools.restart_instance(instance_id, idempotency_key=key),
    )


def restart_service(ctx: RunContext[IncidentDeps], component: str, confirm: bool = False) -> dict[str, Any]:
    """Preview restarting every instance of a component; execute only with confirm=true.

    Restarting drops every open member session on the service. Prefer restarting
    one instance. confirm is an explicit tool argument, not a human approval.
    """
    if not confirm:
        metrics = ctx.deps.tools.read_metrics(component, 1, per_instance=True)
        if "error" in metrics:
            return {"status": "refused", "detail": f"Cannot preview {component}: {metrics['error']}"}
        return {
            "status": "dry_run",
            "would_restart": list(metrics["readings"][-1]["instances"]),
            "impact": "every open member session on this component is dropped",
            "to_execute": "call again with confirm=true",
        }
    return guarded(
        ctx.deps,
        "restart_service",
        component,
        lambda key: ctx.deps.tools.restart_service(component, idempotency_key=key),
    )


def rollback_change(ctx: RunContext[IncidentDeps], change_id: str) -> dict[str, Any]:
    """Roll back a deployment or configuration change without repeating its effect.

    Critical components need valid executor approval and are refused here.
    """
    return guarded(
        ctx.deps,
        "rollback_change",
        change_id,
        lambda key: ctx.deps.tools.rollback_change(change_id, idempotency_key=key),
    )


# Only these functions are offered to the model. The helpers above run in Python.
TOOLS = (restart_instance, restart_service, rollback_change)
