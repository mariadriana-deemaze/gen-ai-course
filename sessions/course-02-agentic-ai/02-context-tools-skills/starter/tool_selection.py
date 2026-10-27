"""Exercise 2 — Many tools, few choices.

The problem
    The platform team publishes one catalog for every agent of the exchange:
    43 tools (catalog.py). Most are useful for some incident. A few are legacy
    (read_logs_v1 reads an old index; get_metrics_legacy gives hourly averages
    that hide a 5-minute spike). One is dangerous (restart_all).

    Give all 43 to the agent and:
    - every definition goes in every model request: ~2 600 tokens before the
      agent reads anything;
    - the model picks worse: it confuses similar tools and uses legacy ones
      because the name looks right.

    People on call know the handful of tools for this kind of incident, and
    where to look when they need another.

Build, in this file
    0. Set USE_FULL_CATALOG = True: the agent gets the whole catalog.
    A. select_tools: before the run, choose the few tools this incident needs.
    B. search_tools: during the run, the agent describes what it needs and the
       matching tools become available from its next step. Add it to ALWAYS_ON.

    The harness offers the agent only the tools named in ACTIVE (filled with
    what select_tools returns), plus ALWAYS_ON, and checks ACTIVE before every
    model request.

Watch out
    - The router can be wrong. If it drops the one tool the incident needs, the
      agent is stuck. That is why B exists: the router decides the start, not the limit.
    - read_logs_v1, get_metrics_legacy and restart_all are never offered, whatever
      the router says, and search_tools must not unlock them either. Only code guarantees that.
    - Your tools from exercises 1 and 3 are in the catalog too, and win over the
      catalog tool of the same name.
    - The router only sees one line per tool: the first line of its docstring.
    - Do not tune to the 20 incidents of routing/incidents.yaml. The trainer has others.

Hints (read only if stuck)
    1. Let the model choose from the summaries, then correct it with code: drop
       unknown names, drop the never-offered tools, always add a small core (the
       tools every investigation starts with), cap the total.
    2. If the model call fails, fall back to the core: a failed router must not stop the incident.
    3. search_tools: compare the words of the need with the name and line of each
       tool, take the best 2 or 3, add them to ACTIVE. Tell the agent in
       INSTRUCTIONS (context.py) when to use it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from meridian.agent_runtime import IncidentDeps

# 0. Set to True: the agent gets the platform's whole catalog.
USE_FULL_CATALOG = False

# The tools the agent sees in this run. The harness fills it with select_tools at the start of every run.
ACTIVE: set[str] = set()

# Every tool this agent could use, name -> first line of its docstring. The harness fills it before select_tools.
CATALOG: dict[str, str] = {}


class Selection(BaseModel):
    tools: list[str]


def select_tools(incident: str, tools: dict[str, str], model: Any) -> list[str]:
    """A. The names of the tools the agent needs for this incident.

    incident: the incident line the agent receives; tools: name -> one-line summary;
    model: the model of the run. As supplied: every tool.
    """
    # Hint: Agent(model, output_type=Selection, instructions=...).run_sync(prompt).output.tools,
    # with the incident and one "name: summary" line per tool in the prompt.
    return list(tools)


def search_tools(ctx: RunContext[IncidentDeps], need: str) -> dict[str, Any]:
    """B. Write the docstring the model will read: when to call it and what to describe."""
    # Hint: score each tool of CATALOG by the words it shares with `need`; add the best to ACTIVE.
    raise NotImplementedError


# Tools the agent always has, whatever select_tools says. Add search_tools.
ALWAYS_ON: tuple = ()
