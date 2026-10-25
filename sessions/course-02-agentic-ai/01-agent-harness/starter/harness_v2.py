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

Exercise 1 and 2 (the extra tools, the restart action, the output validator)
still have to be added below.
"""

from __future__ import annotations

import os
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.profiles.qwen import qwen_model_profile
from pydantic_ai.providers.openai import OpenAIProvider

from meridian.agent_runtime import IncidentDeps

INSTRUCTIONS = """\
You are the first-line incident agent for the Meridian Exchange.
Investigate the incident and propose a mitigation.
Report "resolved" only if the incident is over.
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


def read_alert(ctx: RunContext[IncidentDeps], alert_id: str) -> dict[str, Any]:
    """Read an alert: rule, component, instance, state (open or resolved) and when it was opened."""
    return ctx.deps.tools.read_alert(alert_id)


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
    agent = Agent(build_model(), deps_type=IncidentDeps, output_type=IncidentReport, instructions=INSTRUCTIONS)
    agent.tool(read_alert)
    return agent
