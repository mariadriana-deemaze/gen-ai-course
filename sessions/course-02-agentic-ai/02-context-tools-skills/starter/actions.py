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

from typing import Any

from pydantic_ai import RunContext

from meridian.agent_runtime import IncidentDeps


def restart_instance(ctx: RunContext[IncidentDeps], instance_id: str) -> dict[str, Any]:
    """Restart one instance, e.g. order-gateway-2."""
    # Hint: check the memory first (rule 3), then go through your guarded helper (rules 1 and 2).
    return ctx.deps.tools.restart_instance(instance_id)


def restart_service(ctx: RunContext[IncidentDeps], component: str) -> dict[str, Any]:
    """Restart every instance of a component."""
    # Hint: add a `confirm: bool = False` argument; without it, return the dry run (rule 4).
    return ctx.deps.tools.restart_service(component)


def rollback_change(ctx: RunContext[IncidentDeps], change_id: str) -> dict[str, Any]:
    """Roll back a deployment or configuration change, e.g. CHG-4310."""
    return ctx.deps.tools.rollback_change(change_id)


# The action tools of this agent.
TOOLS = (restart_instance, restart_service, rollback_change)
