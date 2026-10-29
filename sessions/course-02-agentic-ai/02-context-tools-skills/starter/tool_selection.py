"""Exercise 2 — Completed tool selection.

The router selects the initial tools with a short model request.
Code filters forbidden/unknown names, keeps a small core and limits the selection.
search_tools can add missing capabilities during the run, without a model call.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel
from pydantic_ai import RunContext

from meridian.agent_runtime import IncidentDeps

# Include the platform catalog; only ACTIVE and ALWAYS_ON are exposed to the agent.
USE_FULL_CATALOG = True
ACTIVE: set[str] = set()
CATALOG: dict[str, str] = {}

NEVER = {"read_logs_v1", "get_metrics_legacy", "restart_all"}
CORE = {"read_metrics", "summarize_logs", "recent_changes", "find_runbook"}
MAX_TOOLS = 10

ROUTER_INSTRUCTIONS = """\
You choose the tools an incident agent will get for one incident. You see the incident and one line per tool.
Pick the tools for: confirming the symptom, finding the likely cause (network, changes, certificates, DNS, members,
market data, ...), and the action that would fix it. One tool per need; at most 6. Use the exact tool names.
"""


class Selection(BaseModel):
    tools: list[str]


def select_tools(incident: str, tools: dict[str, str], model: Any) -> list[str]:
    """Choose initial tools from their summaries, with a fixed core and code-enforced restrictions."""
    allowed = {name: line for name, line in tools.items() if name not in NEVER}
    core = sorted(CORE & set(allowed))
    listing = "\n".join(f"- {name}: {line}" for name, line in allowed.items() if name not in CORE)
    try:
        picked = Agent(
            model,
            output_type=Selection,
            instructions=ROUTER_INSTRUCTIONS,
        ).run_sync(f"{incident}\n\nTools:\n{listing}").output.tools
    except Exception:  # A failed router falls back to the core instead of stopping the incident.
        picked = []
    extra = [name for name in dict.fromkeys(picked) if name in allowed and name not in CORE]
    return core + extra[: MAX_TOOLS - len(core)]


STOP_WORDS = {"the", "a", "an", "of", "to", "and", "or", "for", "on", "in", "is", "are", "with", "between", "i", "need", "check", "whether", "if"}


def _stems(text: str) -> set[str]:
    return {word[:5] for word in re.findall(r"[a-z]+", text.lower()) if word not in STOP_WORDS}


def search_tools(ctx: RunContext[IncidentDeps], need: str) -> dict[str, Any]:
    """Find missing capabilities by describing what you need, e.g. network connectivity or TLS certificates.

    Up to 3 matching tools become available from the next step. This only
    adds tools; it does not execute them.
    """
    wanted = _stems(need)
    scored = []
    for name, line in CATALOG.items():
        if name in NEVER or name in ACTIVE:
            continue
        score = len(wanted & _stems(name.replace("_", " ") + " " + line))
        if score:
            scored.append((score, name, line))
    found = sorted(scored, key=lambda item: -item[0])[:3]
    ACTIVE.update(name for _, name, _ in found)
    if not found:
        return {"added": [], "detail": "No other tool matches. Describe the need differently, or continue with the tools you have."}
    return {"added": [{"name": name, "what": line} for _, name, line in found]}


ALWAYS_ON = (search_tools,)
