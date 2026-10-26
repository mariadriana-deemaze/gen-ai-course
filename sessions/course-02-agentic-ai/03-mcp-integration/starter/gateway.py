"""Exercise 2: the gatekeeper between the agent and the MCP servers of other teams.

Every tool the model is offered goes through `approve_tool`, and every call
the model makes goes through `check_call` (before) and `after_call` (after).
As supplied, everything is approved and nothing is blocked.

Your job: decide, in code, what the agent may see and do.
- approve_tool: only tools of the approved catalog (your lock file), with the
  description you approved; nothing from servers you never reviewed; and only
  the tools this agent needs.
- check_call: block calls that are dangerous even with an approved tool
  (for example a restart of a whole service).
- after_call: learn from answers (for example, a refusal for lack of scope).

Create the lock file of the approved catalog with:

    uv run python -m meridian.mcp_servers inspect --out sessions/course-02-agentic-ai/03-mcp-integration/<your folder>/catalog.lock.json

Test without a model: check_gateway.py.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LOCK_FILE = Path(__file__).with_name("catalog.lock.json")


def reset() -> None:
    """Called at the start of every run, so that one incident does not inherit the state of another."""


def approve_tool(server: str, name: str, description: str) -> bool:
    """True if the model may be offered this tool."""
    return True


def check_call(server: str, name: str, args: dict[str, Any]) -> str | None:
    """None to let the call through, or the reason to block it (the model receives the reason)."""
    return None


def after_call(server: str, name: str, args: dict[str, Any], result: Any) -> None:
    """Called with the answer of every call that went through."""


def load_lock() -> list[dict[str, Any]]:
    """The approved catalog, as written by `inspect --out` (a list of servers with their tools)."""
    return json.loads(LOCK_FILE.read_text(encoding="utf-8")) if LOCK_FILE.is_file() else []
