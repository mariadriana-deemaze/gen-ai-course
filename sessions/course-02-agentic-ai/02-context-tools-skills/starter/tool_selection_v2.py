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

import re
from typing import Any, TypedDict

from pydantic import BaseModel
from pydantic_ai import Agent, RunContext

from meridian.agent_runtime import IncidentDeps

# 0. The agent gets the platform's whole catalog, and this file cuts it down.
USE_FULL_CATALOG = True

# The tools the agent sees in this run. The harness fills it with select_tools at the start of every run.
ACTIVE: set[str] = set()

# Every tool this agent could use, name -> first line of its docstring. The harness fills it before select_tools.
CATALOG: dict[str, str] = {}

# Never offered, whatever the router says and whatever the agent asks for.
# read_logs_v1 reads an old index; get_metrics_legacy gives hourly averages that
# hide a five-minute spike; restart_all restarts the whole exchange.
NEVER = frozenset({"read_logs_v1", "get_metrics_legacy", "restart_all"})

# Every investigation starts here, so these are not left to a guess: what the
# metrics say, per instance; what is unusual in the logs; what changed; whether
# the links to dependencies are up; and the runbook for the version in production.
#
# The last two are actions, and they are here for the same reason. An agent that
# cannot act cannot resolve an incident, and whether this one will need to act is
# not knowable from the alert line the router reads. Leaving them to the router
# meant s02-noisy-gateway was offered no restart_instance and s02-firewall-change
# no rollback_change, so neither case could pass however good the investigation was.
#
# check_link is deliberately absent, though it is often useful: it was the one
# member of CORE chosen by counting how often routing/incidents.yaml needed it,
# which is the tuning the exercise warns against. The router names it when the
# incident is about reaching a dependency, which is when it is worth a slot.
#
# restart_service is deliberately absent too. No incident needs it, several
# checks forbid it, and search_tools can still reach it.
CORE = (
    "read_metrics",
    "read_instance_metrics",
    "summarize_logs",
    "recent_changes",
    "find_runbook",
    "restart_instance",
    "rollback_change",
)

# The cap the agent is held to. Above this the model starts confusing similar tools.
# 11 = the 8 of CORE plus 3 named for this incident. Two incidents in
# routing/incidents.yaml need three specialists, and at a cap of 10 they were
# unreachable however good the router was.
MAX_TOOLS = 11

# The fallback names at most this many. It used to fill every free slot with
# whatever shared one word, which covered no need and spent the size budget that
# the incidents needing three specialists require.
FALLBACK_PICKS = 2

# How many tools search_tools unlocks per call.
SEARCH_RESULTS = 3

ROUTER_INSTRUCTIONS = """\
You choose the tools a first-line incident agent is given, before it starts.

You see one incident and a catalog of tools, one line each. Answer only with names, spelled as the catalog
spells them.

The agent already has metrics, per-instance metrics, a log summary, recent changes, runbooks, restarting
one instance and rolling back a change. Never name those.

Name what this incident needs beyond them: whatever it must look up, and whatever it must do to proceed,
such as asking for approval, checking an exchange-wide status, or checking whether an earlier action finished.

Most incidents need one or two. Name up to four when the incident plainly calls for them, and name none
when the tools above already cover it. An unnecessary name costs the agent as much as a missing one.
"""


class Selection(BaseModel):
    tools: list[str]


# Words that appear in nearly every incident and nearly every tool line, so
# matching on them tells you nothing about which tool fits which incident.
_STOPWORDS = frozenset(
    """
    alert alerts component components incident incidents order orders gateway meridian exchange agent tool tools
    read list recent check find this that with from have been they them than then when what which while
    minute minutes since after before still again more most some each every only also just
    inc the for and but was are may say log chg alr cap max
    """.split()
)
_WORD = re.compile(r"[a-z]+")


def _words(text: str) -> set[str]:
    """The words of a text worth matching on: long enough, and not in every sentence.

    Example:

        _words("Incident INC-3011 (07:58): TLS handshake failures on order-gateway for several members.")
            -> {"failures", "handshake", "members", "several"}

    Three letters is the floor, because "tls" and "dns" are the words that single an incident out. The
    short words that survive that floor without discriminating anything ("inc", "chg", "alr", "log") are
    stopwords instead.
    """
    return {word for word in _WORD.findall(text.lower()) if len(word) >= 3 and word not in _STOPWORDS}


def _ranked(need: str, tools: dict[str, str]) -> list[tuple[int, str]]:
    """Tools ordered by how many meaningful words they share with `need`, best first.

    Tools sharing no word at all are left out, so the list is usually short.

    Example, scoring a whole incident line (the weak case):

        _ranked("Incident INC-3011 (07:58): TLS handshake failures on order-gateway for several members.", ...)
            -> [(1, "list_members")]

    Example, scoring a sentence written to describe a need (what search_tools passes):

        _ranked("look up the TLS certificate of a member", ...)
            -> [(2, "certificate_status"), (1, "drain_instance"), (1, "export_volume_report")]

    The same scorer, two very different results. An incident line is written for a person and says what
    broke; a need is written for this function and says what tool to find.
    """
    wanted = _words(need)
    scored = [(len(wanted & _words(f"{name.replace('_', ' ')} {summary}")), name) for name, summary in tools.items()]
    return sorted((item for item in scored if item[0] > 0), key=lambda item: (-item[0], item[1]))


# A catalog tool offered beside the better one that replaces it is an invitation
# to use the worse one. read_logs and search_logs return raw lines, which is the
# problem exercise 1 removed; list_runbooks and read_runbook ignore the version
# in production, which is the mistake find_runbook exists to prevent.
SUPERSEDED: dict[str, tuple[str, ...]] = {
    "summarize_logs": ("read_logs", "search_logs"),
    "find_runbook": ("list_runbooks", "read_runbook"),
}


def _offerable(tools: dict[str, str]) -> dict[str, str]:
    """The catalog without the tools that are never offered.

    Applied before the model sees the catalog and again after it answers, so a banned tool cannot be
    proposed, invented or unlocked by search_tools. Only code can make that guarantee.

    Tools that a better one supersedes are hidden the same way, and for the same reason: an agent offered
    both summarize_logs and read_logs will sometimes reach for read_logs.
    """
    hidden = set(NEVER)
    for better, worse in SUPERSEDED.items():
        if better in tools:
            hidden.update(worse)
    return {name: summary for name, summary in tools.items() if name not in hidden}


def _propose(incident: str, tools: dict[str, str], model: Any) -> list[str]:
    """Ask the model which specialists this incident needs. An empty list when it cannot answer.

    Returns [] in three cases, which the caller treats alike: no model was given, the request failed, or the
    model named nothing. Each sends select_tools to the word-matching fallback.

    Example (model is None, as when check_routing.py is run without --model):

        _propose("Incident INC-3011 ...", catalog, None) -> []

    Beware: check_routing.py passes default_model(), the string "openai:$OPENAI_MODEL". With an empty
    OPENAI_API_KEY that raises, is caught here, and the score you read is the fallback's, not the router's.
    Pass --model anthropic:<name> to score the router itself.
    """
    if model is None:
        return []
    catalog = "\n".join(f"{name}: {summary}" for name, summary in tools.items())
    try:
        router = Agent(model, output_type=Selection, instructions=ROUTER_INSTRUCTIONS)
        return router.run_sync(f"{incident}\n\n# Catalog\n{catalog}").output.tools
    except Exception:
        # A router that fails must not stop the incident: fall back to the words.
        return []


def select_tools(incident: str, tools: dict[str, str], model: Any) -> list[str]:
    """A. The names of the tools the agent needs for this incident.

    incident: the incident line the agent receives; tools: name -> one-line summary;
    model: the model of the run.

    CORE is always present, so no guess can leave the agent without metrics, logs, changes, links,
    runbooks, or a way to act. The model adds the specialists; the fallback adds whatever words match.
    At most MAX_TOOLS.

    Example (the fallback path, model=None, on the TLS incident):

        ["read_metrics", "read_instance_metrics", "summarize_logs", "recent_changes",
         "check_link", "find_runbook", "restart_instance", "rollback_change", "list_members"]

    Eight core tools and one guess, where the one the incident actually needs is certificate_status. That is
    the fallback working as designed and still being wrong, and it is exactly what search_tools is for.
    """
    offerable = _offerable(tools)
    chosen = [name for name in CORE if name in offerable]

    proposed = [name for name in _propose(incident, offerable, model) if name in offerable and name not in chosen]
    if not proposed:
        # No model, or it failed, or it named nothing usable: match the incident's
        # own words against the tool lines instead. Only the best few: a tool that
        # shares one word with the incident is noise, and a slot spent on noise is
        # a slot the incidents needing three specialists cannot have.
        proposed = [name for _, name in _ranked(incident, offerable) if name not in chosen][:FALLBACK_PICKS]

    for name in proposed:
        if len(chosen) >= MAX_TOOLS:
            break
        chosen.append(name)
    return chosen


class ToolsFound(TypedDict):
    """Tools that matched, now callable. `now_available` maps each name to its one-line summary."""

    need: str
    now_available: dict[str, str]
    note: str


class NoToolsFound(TypedDict):
    """Nothing matched. A different shape, so an empty result cannot be read as an available tool."""

    need: str
    found: list[str]
    note: str


def search_tools(ctx: RunContext[IncidentDeps], need: str) -> ToolsFound | NoToolsFound:
    """Find a tool you do not have. Describe in your own words what you need to do or find out.

    You start the incident with the tools it was expected to need. If none of them does what you want, say what
    you are trying to do, in a sentence, and the tools that match become available to you from your next step.
    Example: "look up the TLS certificate of a member" or "see whether a settlement batch failed".
    """
    found = [name for _, name in _ranked(need, _offerable(CATALOG))[:SEARCH_RESULTS]]
    ACTIVE.update(found)
    if not found:
        return {"need": need, "found": [], "note": "No tool matches. Try other words, or work with what you have."}
    return {
        "need": need,
        "now_available": {name: CATALOG[name] for name in found},
        "note": "These are available from your next step. Call one of them.",
    }


# Tools the agent always has, whatever select_tools says.
ALWAYS_ON: tuple = (search_tools,)
