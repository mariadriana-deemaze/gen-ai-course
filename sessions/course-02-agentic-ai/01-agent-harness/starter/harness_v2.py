"""Session 1, v2: the starter agent, pointed at whichever model the .env names.

The runner builds a default model string (`openai:$OPENAI_MODEL`) and hands it
to `build_agent`. This harness ignores that argument on purpose and builds its
own model, so `meridian/` stays untouched (see Conventions in the README).

Which model that is comes from the environment, through the course module
`agent_models`: a Qwen served by FreeToken, the Anthropic API, or any other
provider PydanticAI knows. See `sessions/course-02-agentic-ai/agent_models.py`
for the variables to set.

The agent can read metrics per instance and find its own runbooks,
restart a single instance, and have its own "resolved" claim checked
against the metrics before it is accepted.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext

from meridian.agent_runtime import IncidentDeps

COURSE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(COURSE_DIR))

from agent_models import build_model  # noqa: E402

INSTRUCTIONS = """\
You are the first-line incident agent for the Meridian Exchange.
Investigate the incident and propose a mitigation.
Report "resolved" only if the incident is over.

Read the metrics of the affected component before you conclude anything, and
name the specific instance at fault when the readings single one out. Look for
a runbook that applies to the component and follow it. Set "component" to the
component your verdict is about.

You may restart a single instance of a non-critical component when the metrics
single that instance out. Never restart a whole service. After a restart the
instance takes about 2 minutes to come back: read the metrics once more before
you judge whether it worked, and ask only for the last 3 readings.

Readings you have already requested stay in this conversation. Do not ask for
the full history again: request the smallest window that answers your question.

If a tool fails with "retryable": true, call it again before giving up.

Report "insufficient_information" only when you could not read the metrics you
needed, so you cannot judge the incident at all. If you could read them but an
action was refused, you have the facts and lack the authority: report
"unresolved", say what you would do and who should do it.
"""


class IncidentReport(BaseModel):
    incident_id: str
    component: str
    status: Literal["resolved", "unresolved", "insufficient_information"]
    diagnosis: str
    proposed_mitigation: str | None = None
    evidence: list[str]


# The recovery criterion of the order-gateway runbook (v2, step 4).
RECOVERY_LATENCY_MS = 20.0
RECOVERY_ERROR_RATE = 0.01
RECOVERY_READINGS = 3


# Tools -------------------------------------------------------------------
# A tool is a Python function. The model sees its name, its arguments and its
# docstring, and receives what it returns. `ctx.deps.tools` gives access to
# the simulator (see meridian/tools.py for what it offers).


def _or_retry(result: dict[str, Any]) -> dict[str, Any]:
    """Hand a transient backend failure back to the model as a retry.

    A permanent failure is returned untouched, so the model can see that the
    data does not exist and report "insufficient_information" instead of
    retrying forever.
    """
    if result.get("retryable"):
        raise ModelRetry(result["error"])
    return result


def read_alert(ctx: RunContext[IncidentDeps], alert_id: str) -> dict[str, Any]:
    """Read an alert: rule, component, instance, state (open or resolved) and when it was opened."""
    return _or_retry(ctx.deps.tools.read_alert(alert_id))


def read_metrics(ctx: RunContext[IncidentDeps], component: str, window_ticks: int = 3) -> dict[str, Any]:
    """Recent metrics of a component (latency_ms, error_rate, cpu_pct, memory_pct), broken down per instance.

    Use this to tell a component-wide problem from a single bad instance: read
    the per-instance numbers and compare them. `window_ticks` is how many
    recent readings to return, one per minute, at most 30. Ask for a wider
    window only when you need it: every reading stays in the conversation and
    is re-sent on every later request.
    """
    # The default is 3 because that is the window the runbooks use to judge
    # recovery, and a per-instance reading is roughly 170 tokens per tick.
    # per_instance is not an argument the model can set: without the breakdown
    # it cannot name the instance at fault, which is the point of this tool.
    return _or_retry(ctx.deps.tools.read_metrics(component, window_ticks, per_instance=True))


def list_runbooks(ctx: RunContext[IncidentDeps]) -> dict[str, Any]:
    """List the available runbooks: name, version and which components each one applies to."""
    return _or_retry(ctx.deps.tools.list_runbooks())


def read_runbook(ctx: RunContext[IncidentDeps], name: str) -> dict[str, Any]:
    """Read the procedure of one runbook by name, as listed by list_runbooks.

    Returns the latest version, including its diagnosis steps and the criteria
    that count as recovery.
    """
    # The version is deliberately not exposed: the latest one is always the
    # procedure in force, so this is one less decision for the model to get wrong.
    return _or_retry(ctx.deps.tools.read_runbook(name))


# Actions -----------------------------------------------------------------


def restart_instance(ctx: RunContext[IncidentDeps], instance_id: str) -> dict[str, Any]:
    """Restart one instance of a component. The instance is back after about 2 minutes.

    Only instances of non-critical components can be restarted. Restart a
    single instance, never a whole service: that would drop every open member
    session. Use this only when the per-instance metrics single out one
    instance as the cause.
    """
    # No "retryable" key on either refusal: that key means "transient backend
    # failure", and a refused action is neither transient nor missing data.
    try:
        component, _ = ctx.deps.tools.sim.state.instance(instance_id)
    except KeyError:
        return {"error": f"Unknown instance: {instance_id}"}
    if component.critical:
        # Refused here rather than at the executor, so the call costs no
        # simulated time and never reaches the audit log.
        return {"error": f"{component.name} is a critical component: its instances cannot be restarted by this agent."}
    return _or_retry(ctx.deps.tools.restart_instance(instance_id))


# Checking the answer -----------------------------------------------------


def confirm_recovery(ctx: RunContext[IncidentDeps], report: IncidentReport) -> IncidentReport:
    """Reject "resolved" unless the metrics confirm it.

    PydanticAI checks the shape of the report, but nothing checks that it is
    true. This reads the metrics itself and applies the runbook criterion:
    latency below 20 ms and error rate below 1% in the last 3 readings. A
    report that fails goes back to the model with the numbers attached.
    """
    if report.status != "resolved":
        return report

    metrics = ctx.deps.tools.read_metrics(report.component, RECOVERY_READINGS, per_instance=False)
    if isinstance(metrics.get("error"), str):
        raise ModelRetry(
            f"Recovery of {report.component} cannot be confirmed: {metrics['error']}. "
            "Do not report resolved without metrics that back it."
        )

    readings = metrics["readings"][-RECOVERY_READINGS:]
    failing = [
        reading
        for reading in readings
        if reading["latency_ms"] >= RECOVERY_LATENCY_MS or reading["error_rate"] >= RECOVERY_ERROR_RATE
    ]
    if len(readings) < RECOVERY_READINGS or failing:
        detail = "; ".join(
            f"{reading['time']} latency {reading['latency_ms']} ms, errors {reading['error_rate']:.1%}" for reading in readings
        )
        raise ModelRetry(
            f"The metrics do not confirm recovery of {report.component}. Recovery needs latency below "
            f"{RECOVERY_LATENCY_MS:.0f} ms and error rate below {RECOVERY_ERROR_RATE:.0%} in {RECOVERY_READINGS} "
            f"consecutive readings. Last readings: {detail}. Either address the real cause or report a status "
            "other than resolved."
        )
    return report


# Model -------------------------------------------------------------------


def build_agent(model: Any) -> Agent[IncidentDeps, IncidentReport]:
    # `model` comes from the runner and is deliberately unused: the model is
    # configured by the .env, not through OPENAI_MODEL.
    # retries=3 because a ModelRetry from _or_retry spends this budget, and the
    # default of 1 is not enough to survive a flaky backend.
    agent = Agent(build_model(), deps_type=IncidentDeps, output_type=IncidentReport, instructions=INSTRUCTIONS, retries=3)
    agent.tool(read_alert)
    agent.tool(read_metrics)
    agent.tool(list_runbooks)
    agent.tool(read_runbook)
    agent.tool(restart_instance)
    agent.output_validator(confirm_recovery)
    return agent
