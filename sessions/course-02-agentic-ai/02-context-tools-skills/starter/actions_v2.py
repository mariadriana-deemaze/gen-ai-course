"""Exercise 3 — Actions that are safe to repeat.

The problem
    The agent restarts order-gateway-2. The restart happens, but the answer
    never arrives: a timeout. The agent does what any program does on an error:
    it tries again. The instance restarted twice, and every member connected
    to it lost its session twice.

    The same happens when the model simply asks twice, restarts a healthy
    instance "just in case", or restarts the whole service because an old
    runbook says so. The model will make these mistakes. Your tools must make
    them harmless.

Build, in this file: a layer around the actions, with four rules
    1. One intent, one key. Every action carries an idempotency_key derived from
       the intent (incident, action, target), not chosen by the model.
    2. Ask before acting, and after a lost answer. If the intent was already
       carried out, report it instead of acting. After a timeout, ask the
       executor what happened instead of trying again.
    3. Act only if the reason still holds. restart_instance restarts only while
       the instance's memory is above 90 % (or it is not running); otherwise it
       refuses and says why.
    4. Dry run for the whole service. restart_service does nothing without an
       explicit confirmation argument; it answers with what it would do and the
       impact (every member session on the service is dropped).

What the executor gives you (ctx.deps.tools)
    - restart_instance / restart_service / rollback_change(..., idempotency_key=key):
      the same key returns the earlier action without a new effect;
    - action_status(key): "completed", or "unknown_key" if it never arrived;
    - a lost answer comes back as {"error": "... timeout ...", "retryable": False}.

Watch out
    - A key written by the model can change between two calls: the protection is gone.
    - "No answer" is not "nothing happened". Find out; do not guess.
    - When a rule blocks an action, the model reads your answer: say what was
      blocked, why, and what to do instead.
    - Your key will block a legitimate second restart in the same incident. Think about when that is wrong.

Hints (read only if stuck)
    1. The key: f"{incident_id}:{action}:{target}", with incident_id = ctx.deps.sim.scenario.incident["id"].
    2. One helper used by the three actions: guarded(deps, action, target, execute).
       Check action_status(key); if completed, return "already done"; otherwise
       execute with the key; on a timeout, check action_status(key) again and report it.
    3. Memory of one instance: ctx.deps.tools.read_metrics(component, 1, per_instance=True)
       gives each instance with memory_pct and status.
"""

from __future__ import annotations

from typing import Literal

from meridian.actions import ActionTimeout
from pydantic_ai import RunContext

from meridian.agent_runtime import IncidentDeps


# ── Return types ──────────────────────────────────────────────────────────

class ActionAlreadyApplied(dict):
    """The action was already carried out with this idempotency key."""
    action_id: str
    tick: int
    time: str
    action: str
    target: str
    actor: str
    outcome: Literal["completed", "already_applied"]
    idempotency_key: str | None
    note: str  # "already applied with this key; no new effect"


class ActionApplied(dict):
    """The action was successfully applied."""
    action_id: str
    tick: int
    time: str
    action: str
    target: str
    actor: str
    outcome: Literal["completed"]
    idempotency_key: str | None
    note: str  # "action applied"


class ActionRefused(dict):
    """The action was refused because the precondition does not hold."""
    action: str
    target: str
    status: Literal["refused"]
    reason: str


class DryRun(dict):
    """A dry-run response — describes impact without acting."""
    action: str
    target: str
    status: Literal["dry_run"]
    instances_affected: int
    impact: str


class TimeoutRecovered(dict):
    """The executor timed out but the action was already applied."""
    action_id: str
    tick: int
    time: str
    action: str
    target: str
    actor: str
    outcome: str
    idempotency_key: str | None
    note: str  # "executor timed out but the action was already applied"


class TimeoutNoRecord(dict):
    """The executor timed out and there is no record of the action."""
    error: str
    retryable: Literal[False]
    note: str  # "no answer and no record of the action"


# ── Helpers ───────────────────────────────────────────────────────────────

def _idempotency_key(ctx: RunContext[IncidentDeps], action: str, target: str) -> str:
    """Deterministic idempotency key derived from the intent, not chosen by the model."""
    incident_id = ctx.deps.sim.scenario.incident["id"]
    return f"{incident_id}:{action}:{target}"


def _guarded(
    ctx: RunContext[IncidentDeps],
    tool_fn,
    action: str,
    target: str,
    *args,
    **kwargs,
) -> ActionAlreadyApplied | ActionApplied | TimeoutRecovered | TimeoutNoRecord:
    """Guard an action with idempotency, lost-response recovery, and audit.

    1. Check action_status(key). If already completed, return the earlier result.
    2. Call tool_fn with idempotency_key. On success, return the result.
    3. MeridianTools._run catches ActionTimeout and returns {"error": ..., "retryable": False}.
       Check if the result looks like a timeout, then verify with action_status.
    """
    key = _idempotency_key(ctx, action, target)
    status = ctx.deps.tools.action_status(key)
    if status["status"] == "completed":
        return {**status, "note": "already applied with this key; no new effect"}

    try:
        result = tool_fn(target, *args, idempotency_key=key, **kwargs)
        # MeridianTools._run catches ActionTimeout and returns a dict with error info.
        # Check if it looks like a timeout, then verify with action_status.
        if isinstance(result, dict) and result.get("error") and result.get("retryable") is False:
            later = ctx.deps.tools.action_status(key)
            if later["status"] == "completed":
                return {
                    **later,
                    "note": "executor timed out but the action was already applied; no second attempt needed",
                }
        return {**result, "note": "action applied"}
    except ActionTimeout as exc:
        # The action may have been applied even though we didn't get the answer.
        later = ctx.deps.tools.action_status(key)
        if later["status"] == "completed":
            return {
                **later,
                "note": "executor timed out but the action was already applied; no second attempt needed",
            }
        return {"error": str(exc), "retryable": False, "note": "no answer and no record of the action"}


# ── Action tools ──────────────────────────────────────────────────────────

def restart_instance(ctx: RunContext[IncidentDeps], instance_id: str) -> ActionRefused | ActionApplied | ActionAlreadyApplied | TimeoutRecovered | TimeoutNoRecord:
    """Restart one instance, e.g. order-gateway-2."""
    # Rule 3: only restart if memory > 90 % or instance is not running.
    # read_metrics expects a component name, not an instance_id.
    try:
        component_obj, _ = ctx.deps.sim.state.instance(instance_id)
        component_name = component_obj.name
    except KeyError:
        component_name = instance_id.rsplit("-", 1)[0]

    metrics = ctx.deps.tools.read_metrics(component_name, 1, per_instance=True)
    # read_metrics returns {"readings": [tick1, tick2, ...]}.
    # When per_instance=True, each reading has an "instances" dict keyed by instance_id.
    readings = metrics.get("readings", [])
    if readings:
        last = readings[-1]
        instances = last.get("instances", {})
        if instance_id in instances:
            inst = instances[instance_id]
            mem = inst.get("memory_pct", 0)
            status = inst.get("status", "running")
            if mem <= 90 and status == "running":
                return {
                    "action": "restart_instance",
                    "target": instance_id,
                    "status": "refused",
                    "reason": f"memory {mem}% (threshold 90%) and instance is running",
                }

    # Rules 1 & 2: idempotency + lost-response guard.
    return _guarded(ctx, ctx.deps.tools.restart_instance, "restart_instance", instance_id)


def restart_service(ctx: RunContext[IncidentDeps], component: str, confirm: bool = False) -> DryRun | ActionApplied | ActionAlreadyApplied | TimeoutRecovered | TimeoutNoRecord:
    """Restart every instance of a component.

    Without ``confirm=True`` this is a dry run — it describes what would happen
    and the impact (every member session on the service is dropped) but does not
    act.
    """
    if not confirm:
        # Rule 4: dry run — describe impact without acting.
        metrics = ctx.deps.tools.read_metrics(component, 1, per_instance=True)
        count = 0
        readings = metrics.get("readings", [])
        if readings:
            count = len(readings[-1].get("instances", {}))
        return {
            "action": "restart_service",
            "target": component,
            "status": "dry_run",
            "instances_affected": count,
            "impact": f"All {count} instance session(s) on {component} would be dropped.",
        }

    return _guarded(ctx, ctx.deps.tools.restart_service, "restart_service", component)


def rollback_change(ctx: RunContext[IncidentDeps], change_id: str) -> ActionApplied | ActionAlreadyApplied | TimeoutRecovered | TimeoutNoRecord:
    """Roll back a deployment or configuration change, e.g. CHG-4310."""
    return _guarded(ctx, ctx.deps.tools.rollback_change, "rollback_change", change_id)


# The action tools of this agent.
TOOLS = (restart_instance, restart_service, rollback_change)
