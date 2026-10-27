"""Session 2: the incident agent. This file only wires the pieces; you do not need to edit it.

Your work is in:
- context.py         exercise 1: what the agent sees, the log summary, the runbook finder;
- tool_selection.py  exercise 2: which tools of the catalog the agent gets;
- actions.py         exercise 3: actions that are safe to repeat.

As supplied, it runs: everything goes in the prompt, the tools hand over raw
data, and the actions run every time they are called.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.toolsets import FunctionToolset

from meridian.agent_runtime import IncidentDeps

HERE = Path(__file__).resolve().parent
CATALOG_FILE = HERE / "catalog.py"


def _load(path: Path) -> Any:
    """Load a module from a file next to this one."""
    module_name = f"{path.stem}_{abs(hash(path)) % 100_000}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


catalog = _load(CATALOG_FILE)
context = _load(HERE / "context_v2.py")
selection = _load(HERE / "tool_selection_v2.py")
actions = _load(HERE / "actions.py")

# Used by check_actions.py and check_routing.py.
build_prompt = context.build_prompt
restart_instance = actions.restart_instance
restart_service = actions.restart_service
select_tools = selection.select_tools


class IncidentReport(BaseModel):
    incident_id: str
    status: Literal["recovered", "not_recovered", "insufficient_information"]
    evidence: list[str]
    actions_taken: list[str]
    root_cause: str | None = None
    summary: str


def available_tools() -> dict[str, Any]:
    """Every tool the agent could use, by name. Your tools win over catalog tools of the same name."""
    tools = {tool.__name__: tool for tool in catalog.CATALOG} if selection.USE_FULL_CATALOG else {}
    tools.update({tool.__name__: tool for tool in (*context.TOOLS, *actions.TOOLS)})
    return tools


def tool_catalog() -> dict[str, str]:
    """One line per tool: the first line of its docstring."""
    return {name: (inspect.getdoc(tool) or "").splitlines()[0] for name, tool in available_tools().items()}


_model: dict[str, Any] = {"model": None}


def build_toolsets(deps: IncidentDeps) -> list[Any]:
    """Called once per run, before the agent starts: offer the selected tools, plus the ones always on."""
    brief = deps.sim.incident_brief()
    incident = f"Incident {brief['incident_id']} ({brief['time']}): {brief['summary']}"
    selection.CATALOG.clear()
    selection.CATALOG.update(tool_catalog())
    selection.ACTIVE.clear()
    selection.ACTIVE.update(selection.select_tools(incident, dict(selection.CATALOG), _model["model"]))
    selected = FunctionToolset(list(available_tools().values())).filtered(lambda ctx, tool: tool.name in selection.ACTIVE)
    return [selected, FunctionToolset(list(selection.ALWAYS_ON))]


def build_agent(model: Any) -> Agent[IncidentDeps, IncidentReport]:
    _model["model"] = model
    return Agent(model, deps_type=IncidentDeps, output_type=IncidentReport, instructions=context.INSTRUCTIONS)
