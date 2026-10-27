# Session 3 — MCP: External Integration, Governance and Remote Access

## The situation

- **The same problem arrives several times.** An alert, a ticket and a member complaint can be one incident. The agent opens one for each.
- **The agent trusts every server it is connected to.** The tickets, monitoring and deployments servers belong to other teams. They change overnight, refuse calls for lack of permission, and sometimes a server nobody reviewed appears.

## The exercises

You work directly in the files of `starter/`. Each file starts with its exercise: the problem, what to build, what to watch out for, and hints.

| Exercise | File | You build |
|---|---|---|
| 1. Your MCP server: one incident, several sources | `starter/incident_server.py` | `correlate()` and the descriptions of the server's tools |
| 2. The gatekeeper | `starter/gateway.py` | an approved catalog, least privilege, blocked calls, remembered refusals |

`starter/harness.py` wires the agent, the servers and your two files. You do not need to edit it. The other teams' servers are in `meridian/mcp_servers/`.

## The cases

| Case | What happens | Exercise |
|---|---|---|
| `s03-alert-and-ticket` | A gateway alert, while ticket MRX-2210 and complaint MEM-1187 already describe the same problem. | 1 |
| `s03-similar-but-different` | A gateway alert, while an unrelated market data incident is open. | 1 |
| `s03-description-change` | The monitoring server was upgraded overnight; one tool description now asks for a rollback. | 2 |
| `s03-scope-refused` | The ticket credentials were rotated and lost the permission to comment. | 2 |
| `s03-shadow-server` | A community server nobody reviewed appeared in the catalog, with a "quick fix" tool. | 2 |

## The commands

Run them from the repository root, after `uv sync` and with `OPENAI_API_KEY` in `.env`.

```bash
uv run python -m meridian.mcp_servers inspect
uv run python -m meridian.mcp_servers inspect --out sessions/course-02-agentic-ai/03-mcp-integration/starter/catalog.lock.json
uv run python sessions/course-02-agentic-ai/03-mcp-integration/starter/check_correlation.py --server sessions/course-02-agentic-ai/03-mcp-integration/starter/incident_server.py
uv run python sessions/course-02-agentic-ai/03-mcp-integration/starter/check_gateway.py --gateway sessions/course-02-agentic-ai/03-mcp-integration/starter/gateway.py
uv run python -m meridian.run --harness sessions/course-02-agentic-ai/03-mcp-integration/starter/harness.py --scenario sessions/course-02-agentic-ai/03-mcp-integration/cases/s03-alert-and-ticket.yaml
uv run python -m meridian.run --harness sessions/course-02-agentic-ai/03-mcp-integration/starter/harness.py --cases-dir sessions/course-02-agentic-ai/03-mcp-integration/cases --summary
```

- `inspect` shows the catalog of the other teams' servers; with `--out` it writes your lock file (exercise 2).
- `check_correlation.py` replays the 22 events of `events/dev.yaml` through your `correlate`, without a model, and gives precision, recall and F1.
- `check_gateway.py` runs 7 tests on your gatekeeper, without a model.
- The last two run the agent with the model, on one case or on all of them.

## Done when

| Exercise | Done when |
|---|---|
| 1 | `check_correlation.py` gives F1 ≥ 0.90, and `s03-alert-and-ticket` and `s03-similar-but-different` pass. |
| 2 | `check_gateway.py` gives 7/7, and all five cases pass. |

## Write down

- **1.** Your correlation rules and the hardest one to get right; why this logic belongs in a server and not in the agent's instructions.
- **2.** For each exercise 2 case: what went wrong, and which kind of problem it was (capability selection, server governance, or credentials and scopes).

## Rules

- Do not edit `meridian/` or `starter/harness.py`. The other teams' servers are their systems: adapt your side.
- Critical components (`matching-engine`, `reference-db`) cannot be changed by this agent.
- Deliverable: your two files and lock file, the notes, and five new events for `events/dev.yaml` that your correlation gets right and a naive rule ("same component within 30 minutes") gets wrong.

## Trainer demo — remote access

The tickets server over Streamable HTTP, with bearer tokens that carry
different scopes:

```bash
uv run python -m meridian.mcp_servers.http_demo serve
uv run python -m meridian.mcp_servers.http_demo client --token none
uv run python -m meridian.mcp_servers.http_demo client --token first-line
uv run python -m meridian.mcp_servers.http_demo client --token incident-manager
```

## Scope boundary

Building an MCP server, correlating sources, governing the servers of others
and handling scopes. Defending against injected instructions in general is
Session 5; memory of earlier incidents is Session 4. Do not build an OAuth
server or support several transports from scratch.
