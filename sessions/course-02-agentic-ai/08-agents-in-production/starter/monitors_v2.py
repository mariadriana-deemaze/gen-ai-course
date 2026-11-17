"""Exercise 2 — Find the cause in the traces, and watch the agents in production.

Every run of the incident team leaves a trace: OpenTelemetry spans for each agent, model call and tool call.
Tab 2 shows four recorded traces as a waterfall. Something went wrong in three of them. Healthy in one.

You write two things that watch every trace:
1. MONITORS: functions that read a trace and return alerts (a list of short texts; empty = all fine).
   Code, no model: cheap, run on every trace, page someone when they fire.
2. The trace analyst: an agent (a model) that reads describe(trace) and says what went wrong and why.
   describe(trace) is what it sees; ANALYST_INSTRUCTIONS is how it reasons.

The versions below run, but they are naive: the only monitor looks for crashed spans (errors), and the analyst
only sees what the team reported. Run them, read the result, then make them useful.

Goal: on the three problem traces at least one monitor fires; on the healthy trace none does;
and the analyst names the real cause of each problem.

What a trace gives you (lab/traces.py has the details): trace.root (incident, pattern, reported, recovered, gateway),
trace.spans, trace.agents() (model_calls, tool_calls, tokens per agent), trace.tool_calls(), trace.cost_usd().

Hints:
- Read the waterfall of each trace first. What does a problem look like there?
- An agent that fails rarely raises an error: the naive monitor catches one problem out of three. What does "it went wrong" look like in numbers?
- What does the team say happened, and what does the root span say really happened?
- The analyst only knows what describe() shows it.
"""

from __future__ import annotations

import json
from collections import Counter

# Budgets: a healthy specialist uses about 3 model calls and under 1,000 input tokens per incident.
MAX_MODEL_CALLS_PER_AGENT = 8
MAX_TOKENS_PER_INCIDENT = 8_000
MAX_ERROR_RATE = 0.01  # a recovered gateway runs at about 0.1% errors


def error_spans(trace) -> list[str]:
    """A span that ended with an error (an exception in the code)."""
    return [f"{span['name']} failed: {span['error']}" for span in trace.spans if span["status"] == "ERROR"]


def false_resolution(trace) -> list[str]:
    """The team says it is fixed, but the systems (the root span) say it is not."""
    root, gateway = trace.root, trace.root.get("gateway") or {}
    if root.get("reported") != "resolved":
        return []
    alerts = []
    if not root.get("recovered"):
        alerts.append("reported 'resolved' but the incident did not recover")
    if gateway.get("error_rate", 0) > MAX_ERROR_RATE:
        alerts.append(f"reported 'resolved' with the gateway on site {gateway.get('site')} at {gateway['error_rate']:.0%} errors")
    return alerts


def runaway_agent(trace) -> list[str]:
    """An agent going round in circles: far more model calls than the job needs."""
    return [f"{agent} made {row['model_calls']} model calls ({row['input_tokens']:,} input tokens)"
            for agent, row in trace.agents().items() if row["model_calls"] > MAX_MODEL_CALLS_PER_AGENT]


def duplicate_work(trace) -> list[str]:
    """The same agent ran the same tool with the same arguments and got the same result: paid twice for one answer.
    A repeated call with a new result (checking the gateway before and after an action) is not waste."""
    seen = Counter((call["agent"], call["tool"], call["arguments"], call["result"]) for call in trace.tool_calls())
    return [f"{agent} ran {tool}({arguments}) {count} times for the same result"
            for (agent, tool, arguments, _), count in seen.items() if count > 1]


def token_budget(trace) -> list[str]:
    """The whole incident cost more than its budget."""
    total = sum(row["input_tokens"] + row["output_tokens"] for row in trace.agents().values())
    return [f"{total:,} tokens, over the budget of {MAX_TOKENS_PER_INCIDENT:,}"] if total > MAX_TOKENS_PER_INCIDENT else []


MONITORS = [error_spans, false_resolution, runaway_agent, duplicate_work, token_budget]


ANALYST_INSTRUCTIONS = """You review the trace of an incident handled by a team of AI agents and find the real cause of what went wrong.

Work through the trace in this order:
1. Outcome. Compare what the team REPORTED with what the systems say (recovered, gateway site and error rate).
   A team that reports "resolved" while the gateway still has errors is a failure, whatever the agents claim.
   If the systems did recover (recovered=True, error rate near 0.1%), the outcome is correct: do not call it a
   wrong outcome, and only question the diagnosis if the trace directly contradicts it.
2. Action. What did remediation do (rollback, failover to which site), and was that the right action?
3. Chain. Follow the information backwards: which specialist's finding led to that action, and which fact was
   missing, vague or lost on the way (an id not quoted, a constraint not passed on, an agent that gave no answer)?
   An agent that gave NO ANSWER is a missing link: everything after it was decided without its findings, so say
   what the next agent did without them. Check timing before blaming a change: one made long before the
   symptoms started (a large negative at_tick) is not the cause.
4. Efficiency. Look for an agent that loops (many model calls, the same tools again and again) or work done twice
   (the same question or the same tool call repeated). A run can recover and still be a cost problem.

Answer in at most 5 sentences: the verdict (healthy, wrong outcome, or cost problem), then the root cause naming
the agent responsible and the evidence from the trace (tool calls, ids, numbers). Do not invent facts the trace
does not show. If nothing went wrong, say it is healthy and why.
"""


def _short(value, n: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    text = text.replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"


def describe(trace) -> str:
    root, gateway = trace.root, trace.root.get("gateway") or {}
    lines = [
        f"Incident {root['incident']}, handled with a {root['pattern']}.",
        f"The team reported: {root['reported']}.",
        f"What the systems say: recovered={root.get('recovered')}, gateway on site {gateway.get('site')}, "
        f"error rate {gateway.get('error_rate', 0):.1%}, latency {gateway.get('latency_ms')} ms.",
        "",
        "Per agent (model calls, tool calls, input/output tokens):",
    ]
    lines += [f"- {agent}: {r['model_calls']} model calls, {r['tool_calls']} tool calls, {r['input_tokens']:,} in / {r['output_tokens']:,} out"
              for agent, r in trace.agents().items()]
    lines += ["", "Timeline (agents, tool calls with result, errors):"]
    # an agent answers when it ends, after its tool calls: order answers by end time, tool calls by start time
    for span in sorted(trace.spans, key=lambda s: s["end_ms"] if s["operation"] == "invoke_agent" else s["start_ms"]):
        if span["operation"] == "execute_tool":
            args = span["attributes"].get("gen_ai.tool.call.arguments", "")
            lines.append(f"- [{span['start_ms']} ms] {span['agent']} → {span['tool']}({_short(args, 100)}) = "
                         f"{_short(span['attributes'].get('gen_ai.tool.call.result', ''), 500)}")
        elif span["operation"] == "invoke_agent":
            final = span["attributes"].get("final_result")
            lines.append(f"- [{span['end_ms']} ms] {span['agent']} answered: {_short(final, 300) if final else '(NO ANSWER)'}")
        if span["status"] == "ERROR":
            lines.append(f"  ERROR in {span['name']}: {span.get('error')}")
    alerts = [f"{monitor.__name__}: {alert}" for monitor in MONITORS for alert in monitor(trace)]
    lines += ["", "Monitor alerts: " + ("; ".join(alerts) if alerts else "none")]
    return "\n".join(lines)
