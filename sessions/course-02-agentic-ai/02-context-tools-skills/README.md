# Session 2 — Context Engineering, Tools and Skills

## The situation

- **The agent drowns.** Every alert, about 5 000 log lines and every runbook go in its first message: more than 100 000 tokens. The cause is often one line in there.
- **The agent has too many tools.** The platform offers 43. Every definition goes in every request, and the model picks worse.
- **The agent repeats itself.** When a restart is requested twice, or its answer is lost, the restart happens twice.

## The exercises

You work directly in the files of `starter/`. Each file starts with its exercise: the problem, what to build, what to watch out for, and hints.

| Exercise | File | You build |
|---|---|---|
| 1. Find the needle without reading the haystack | `starter/context.py` | the first message, `summarize_logs`, `find_runbook` |
| 2. Many tools, few choices | `starter/tool_selection.py` | `select_tools` (a router), `search_tools` |
| 3. Actions that are safe to repeat | `starter/actions.py` | idempotency keys, checks before acting, dry run |

`starter/harness.py` wires the pieces. You do not need to edit it. `starter/catalog.py` is the platform's catalog: do not edit it either.

## The cases

| Case | What happens | Exercise |
|---|---|---|
| `s02-noisy-gateway` | One gateway instance leaks memory, among thousands of log lines and 200 duplicated alerts. | 1 |
| `s02-hidden-config-change` | The gateway rejects orders. The cause is one line in the logs of another component. | 1 |
| `s02-firewall-change` | The gateway cannot reach the database. The cause is one line among errors. | 1 |
| `s02-lost-response` | A restart is applied, but its answer never arrives. | 3 |

Every case has a budget: 40 000 input tokens per run, all requests together.

## The commands

Run them from the repository root, after `uv sync` and with `OPENAI_API_KEY` in `.env`.

```bash
uv run python -m meridian.run --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness.py --scenario sessions/course-02-agentic-ai/02-context-tools-skills/cases/s02-noisy-gateway.yaml
uv run python -m meridian.run --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness.py --cases-dir sessions/course-02-agentic-ai/02-context-tools-skills/cases --summary
uv run python sessions/course-02-agentic-ai/02-context-tools-skills/starter/check_routing.py --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness.py
uv run python sessions/course-02-agentic-ai/02-context-tools-skills/starter/check_actions.py --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness.py
```

- The first two run the agent with the model, on one case or on all of them.
- `check_routing.py` scores your `select_tools` on 20 incidents (`routing/incidents.yaml`), without running the agent.
- `check_actions.py` calls your action tools directly, without a model, and counts the restarts.

The starter's first request is expensive. Run it once, on one case, to see the trace.

## Done when

| Exercise | Done when |
|---|---|
| 1 | The three exercise 1 cases pass, under the budget. |
| 2 | `check_routing.py` gives coverage ≥ 0.95, ≤ 10 tools on average, no never-offered tool; the exercise 1 cases still pass with the whole catalog. |
| 3 | `check_actions.py` gives 4/4, and all four cases pass. |

## Write down

- **1.** Your context policy; how your summary decides what is rare; one case where your first policy dropped what the agent needed.
- **2.** What your router decides with the model and what with code; one incident where `search_tools` recovered a tool the router missed.
- **3.** Why a key chosen by the model is weaker than one derived from the intent; one situation where "one intent, one key" is wrong.

## Rules

- Do not edit `meridian/`, `starter/harness.py` or `starter/catalog.py`.
- Critical components (`matching-engine`, `reference-db`) cannot be changed by this agent.
- Deliverable: your three files, the notes, and one new case (in `cases/` and `checks/`) where the cause is a rare line in the logs of a component the alert does not mention.
