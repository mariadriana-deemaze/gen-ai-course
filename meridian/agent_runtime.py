"""Run a PydanticAI agent on a Meridian scenario and show what happened.

A harness is a Python file that defines `build_agent(model) -> Agent`. It may
also define:

- `build_prompt(brief, deps) -> str`: the first message (`deps` gives access
  to the tools, the simulator, the memory store and the identity);
- `build_toolsets(deps) -> list`: toolsets created for this run, e.g. the
  simulated MCP servers, which must share this run's simulator (Session 3);
- `after_run(report, deps)`: called once the agent has finished, e.g. to
  write a shift handover note to memory (Session 4).
Sessions keep their harness files next to their exercises; this module only
provides the plumbing to load a harness, run it and print the trace.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

from dotenv import load_dotenv
from pydantic_ai import capture_run_messages
from pydantic_ai.exceptions import AgentRunError, UsageLimitExceeded
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.usage import UsageLimits

from meridian.evaluate import EvaluationResult, evaluate, print_evaluation, report_as_dict
import yaml

from meridian.memory import MemoryStore
from meridian.model import DATA_DIR
from meridian.scenarios import load_solution, load_scenario
from meridian.simulator import Simulator
from meridian.tools import MeridianTools, ToolCall

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOLUTIONS_DIR = REPO_ROOT / "private" / "course-02-agentic-ai" / "solutions"


IDENTITIES_FILE = DATA_DIR / "identities.yaml"


def load_identities() -> dict[str, dict[str, Any]]:
    return yaml.safe_load(IDENTITIES_FILE.read_text(encoding="utf-8"))["roles"]


def identity_for(role: str) -> dict[str, Any]:
    roles = load_identities()
    if role not in roles:
        raise SystemExit(f"Unknown role {role!r}; use one of {', '.join(roles)}")
    return {"role": role, **roles[role]}


@dataclass
class IncidentDeps:
    """Dependencies available to tools through `RunContext.deps`.

    - `tools`: the simulator tools (and through them `sim`);
    - `memory`: the persistent memory store, or None when memory is disabled;
    - `identity`: who the agent acts as: role, name and team.
    """

    tools: MeridianTools
    memory: MemoryStore | None = None
    identity: dict[str, Any] = field(default_factory=lambda: identity_for("first-line"))

    @property
    def sim(self) -> Simulator:
        return self.tools.sim

    @property
    def role(self) -> str:
        return self.identity["role"]


@dataclass
class IncidentRun:
    scenario_id: str
    harness: str
    brief: dict[str, Any]
    report: Any
    trace: list[ToolCall]
    sim: Simulator
    error: str | None = None
    messages: list[ModelMessage] = field(default_factory=list)
    evaluation: EvaluationResult | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def default_model() -> str:
    load_dotenv()
    return f"openai:{os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')}"


def require_api_key() -> None:
    load_dotenv()
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is missing. Create a local .env from .env.example.")


def load_harness(path: str | Path) -> ModuleType:
    path = Path(path).resolve()
    module_name = f"harness_{path.stem}_{abs(hash(path)) % 10_000}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load harness from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "build_agent"):
        raise SystemExit(f"{path} must define build_agent(model)")
    return module


def default_prompt(brief: dict[str, Any], deps: IncidentDeps | None = None) -> str:
    alert = f" Related alert: {brief['alert_id']}." if brief.get("alert_id") else ""
    return f"Incident {brief['incident_id']} ({brief['time']}): {brief['summary']}{alert}"


def find_solution(scenario_id: str, solutions_dir: Path | None = None, scenario_path: str | Path | None = None) -> Path | None:
    """The success criteria for a scenario.

    Public checks next to the session's cases (`<session>/checks/<id>.yaml`)
    come first. Otherwise the trainer's private solutions are used
    (`MERIDIAN_SOLUTIONS` or `private/course-02-agentic-ai/solutions`).
    """
    if scenario_path is not None:
        public = Path(scenario_path).resolve().parent.parent / "checks" / f"{scenario_id}.yaml"
        if public.is_file():
            return public
    directory = solutions_dir or Path(os.getenv("MERIDIAN_SOLUTIONS", DEFAULT_SOLUTIONS_DIR))
    path = directory / f"{scenario_id}.yaml"
    return path if path.is_file() else None


def open_memory(scenario_memory: list[dict[str, Any]], memory_path: str | Path | None) -> MemoryStore:
    """A persistent store when a path is given, otherwise a fresh store for this run only."""
    if memory_path is None:
        memory_path = Path(tempfile.mkdtemp(prefix="meridian-memory-")) / "memory.db"
    store = MemoryStore(memory_path)
    store.seed(scenario_memory)
    return store


def model_input_text(messages: list[ModelMessage]) -> str:
    """Everything the model received: prompts and tool results."""
    chunks = []
    for message in messages:
        if isinstance(message, ModelRequest):
            if message.instructions:
                chunks.append(message.instructions)
            for part in message.parts:
                if isinstance(part, (UserPromptPart, ToolReturnPart, RetryPromptPart)):
                    chunks.append(part.content if isinstance(part.content, str) else json.dumps(part.content, default=str))
    return "\n".join(chunks)


def run_incident(
    harness: ModuleType,
    scenario_path: str | Path,
    *,
    model: Any = None,
    max_requests: int = 20,
    solutions_dir: Path | None = None,
    memory_path: str | Path | None = None,
    use_memory: bool = True,
    role: str | None = None,
) -> IncidentRun:
    """Run one harness on one scenario. Evaluate it when a solution file exists."""
    scenario = load_scenario(scenario_path)
    sim = Simulator(scenario)
    identity = identity_for(role or scenario.actor.get("role", "first-line"))
    tools = MeridianTools(sim, actor=identity["name"])
    memory = open_memory(scenario.memory_seed, memory_path) if use_memory else None
    deps = IncidentDeps(tools, memory, identity)
    agent = harness.build_agent(model or default_model())
    brief = sim.incident_brief()
    build_prompt: Callable[[dict[str, Any], IncidentDeps], str] = getattr(harness, "build_prompt", default_prompt)
    toolsets = harness.build_toolsets(deps) if hasattr(harness, "build_toolsets") else None

    report, error = None, None
    with capture_run_messages() as messages:
        try:
            result = agent.run_sync(
                build_prompt(brief, deps),
                deps=deps,
                usage_limits=UsageLimits(request_limit=max_requests),
                toolsets=toolsets,
            )
            report = result.output
        except UsageLimitExceeded as exceeded:
            error = f"stopped: {exceeded}"
        except AgentRunError as failure:
            error = f"agent error: {failure}"

    if report is not None and hasattr(harness, "after_run"):
        harness.after_run(report, deps)

    run = IncidentRun(scenario.scenario_id, Path(harness.__file__).name, brief, report, tools.trace, sim, error, list(messages))
    run.extra["end_tick"] = sim.state.tick
    run.extra["identity"] = identity
    run.extra["memory"] = memory
    run.extra["input_tokens"] = sum(message.usage.input_tokens for message in messages if isinstance(message, ModelResponse))
    run.extra["requests"] = sum(1 for message in messages if isinstance(message, ModelResponse))
    solution_path = find_solution(scenario.scenario_id, solutions_dir, scenario_path)
    if solution_path is not None:
        run.evaluation = evaluate(
            sim,
            tools.trace,
            report,
            load_solution(solution_path),
            memory=memory,
            model_input=model_input_text(list(messages)),
            input_tokens=run.extra["input_tokens"],
        )
    return run


def _short(value: Any, width: int = 110) -> str:
    text = json.dumps(value, default=str)
    return text if len(text) <= width else text[: width - 3] + "..."


def model_trace(messages: list[ModelMessage]) -> list[tuple[str, str]]:
    """What the model asked for and what it received: each call followed by its result."""
    answers: dict[str, tuple[str, str]] = {}
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    answers[part.tool_call_id] = ("result", _short(part.content))
                elif isinstance(part, RetryPromptPart) and part.tool_name:
                    answers[part.tool_call_id] = ("retry", _short(part.content))
    lines: list[tuple[str, str]] = []
    for message in messages:
        for part in message.parts:
            if isinstance(message, ModelResponse) and isinstance(part, ToolCallPart):
                lines.append(("call", f"{part.tool_name}({_short(part.args_as_dict(), 70)})"))
                if part.tool_call_id in answers:
                    lines.append(answers[part.tool_call_id])
            elif isinstance(message, ModelRequest) and isinstance(part, RetryPromptPart) and not part.tool_name:
                lines.append(("retry", _short(part.content)))
    return lines


def print_run(run: IncidentRun, show_trace: bool = True) -> None:
    print(f"\n=== {run.scenario_id} | harness {run.harness} | incident {run.brief['incident_id']} ===")
    if show_trace:
        print("What the model requested and received:")
        steps = model_trace(run.messages)
        step = 0
        for kind, text in steps:
            if kind == "call":
                step += 1
                print(f"{step:>4}  {text}")
            else:
                print(f"      {'->' if kind == 'result' else 'retry:'} {text}")
        if not steps:
            print("      (no tool calls)")
        print(f"Simulated time: {run.sim.clock.label(run.brief['tick'])} -> {run.sim.clock.label(run.extra.get('end_tick', run.sim.state.tick))}")
    if run.error:
        print(f"Run ended without a report: {run.error}")
    else:
        print("Report:")
        print(json.dumps(report_as_dict(run.report), indent=2, default=str))
    identity = run.extra.get("identity")
    if identity:
        print(f"Acting as: {identity['name']} ({identity['role']})")
    print(f"Model requests: {run.extra.get('requests', 0)}, input tokens: {run.extra.get('input_tokens', 0)}")
    for approval in run.sim.approvals:
        print(f"Approval {approval['approval_ref']} {approval['time']} {approval['action']} {approval['target']} by {approval['approver_role']} -> {approval['status']}")
    for message in run.sim.outbox:
        print(f"Message {message['message_id']} {message['time']} to {message['channel']}: {_short(message['text'], 90)}")
    for ticket in run.sim.tickets.tickets:
        if ticket.created_by_agent:
            print(f"Ticket created: {ticket.key} [{ticket.issue_type}] {ticket.summary}")
        elif ticket.updated_by_agent:
            print(f"Ticket updated: {ticket.key} (status {ticket.status}, {len(ticket.comments)} comment(s), links {ticket.links})")
    actions = [record for record in run.sim.executor.audit]
    if actions:
        print("Actions recorded by the executor:")
        for record in actions:
            approval = f" [{record.confirmation_ref}]" if record.confirmation_ref else ""
            print(f"  {record.action_id} {record.time} {record.action} {record.target}{approval} by {record.actor} -> {record.outcome}")
    if run.evaluation is not None:
        print_evaluation(run.evaluation)
