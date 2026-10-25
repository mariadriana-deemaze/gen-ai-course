"""Session 1, v2: the starter agent, pointed at a Qwen model served by FreeToken.

The runner builds a default model string (`openai:$OPENAI_MODEL`) and hands it
to `build_agent`. This harness ignores that argument on purpose and builds its
own model, so `meridian/` stays untouched (see Conventions in the README).

FreeToken speaks the OpenAI chat-completions protocol, so the plain
`OpenAIProvider` works with a different `base_url`. The Qwen model profile is
passed explicitly: `OpenAIProvider` picks its profile from the model name
assuming OpenAI model names, and would not recognise a Qwen one.

Set in your local `.env`:

    OPENAI_API_KEY=<your FreeToken key>   # the runner also checks this name
    AGENT_BASE_URL=<FreeToken OpenAI-compatible endpoint, ending in /v1>
    AGENT_MODEL=<the Qwen model id FreeToken exposes>

Exercise 1 is done: the agent can read metrics per instance and find its own
runbooks. Exercise 2 (the restart action and the output validator) is not.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.qwen import qwen_model_profile
from pydantic_ai.providers.openai import OpenAIProvider

from meridian.agent_runtime import IncidentDeps

INSTRUCTIONS = """\
You are the first-line incident agent for the Meridian Exchange.
Investigate the incident and propose a mitigation.
Report "resolved" only if the incident is over.

Read the metrics of the affected component before you conclude anything, and
name the specific instance at fault when the readings single one out. Look for
a runbook that applies to the component and follow it.

If a tool fails with "retryable": true, call it again before giving up. If it
fails with "retryable": false, the data is not available: report
"insufficient_information" rather than guessing, and take no action.
"""


class IncidentReport(BaseModel):
    incident_id: str
    status: Literal["resolved", "unresolved", "insufficient_information"]
    diagnosis: str
    proposed_mitigation: str | None = None
    evidence: list[str]


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


# Model -------------------------------------------------------------------


def build_model() -> OpenAIChatModel:
    """The Qwen model behind FreeToken's OpenAI-compatible endpoint."""
    load_dotenv()
    base_url = os.getenv("AGENT_BASE_URL")
    model_name = os.getenv("AGENT_MODEL")
    if not base_url or not model_name:
        raise SystemExit("AGENT_BASE_URL and AGENT_MODEL are missing. Add them to your local .env.")
    return OpenAIChatModel(
        model_name,
        # A local FreeToken server ignores the key, but the provider requires one.
        provider=OpenAIProvider(base_url=base_url, api_key=os.getenv("OPENAI_API_KEY") or "freetoken-local"),
        profile=qwen_model_profile(model_name),
    )


def build_agent(model: Any) -> Agent[IncidentDeps, IncidentReport]:
    # `model` comes from the runner and is deliberately unused: the model is
    # configured here, not through OPENAI_MODEL.
    # retries=3 because a ModelRetry from _or_retry spends this budget, and the
    # default of 1 is not enough to survive a flaky backend.
    agent = Agent(build_model(), deps_type=IncidentDeps, output_type=IncidentReport, instructions=INSTRUCTIONS, retries=3)
    agent.tool(read_alert)
    agent.tool(read_metrics)
    agent.tool(list_runbooks)
    agent.tool(read_runbook)
    return agent
