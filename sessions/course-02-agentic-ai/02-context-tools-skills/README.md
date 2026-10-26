# Session 2 — Context Engineering, Tools and Skills

The situation, in two sentences:

- **The agent drowns.** The starter puts every alert, every log line (about
  5 000 per incident) and every runbook in its first message: around 100 000
  tokens. The cause of the incident is often a single line in there.
- **The agent repeats itself.** When a restart is requested twice, or its
  answer is lost, the restart happens twice.

In this session you build the pieces that fix both: tools that turn thousands
of lines into the few that matter, and a layer that makes actions safe to
repeat.

Run every command from the repository root, after `uv sync` and after setting
`OPENAI_API_KEY` in a local `.env` file. Commands are on one line, so they
work in any terminal.

## What you have

```text
02-context-tools-skills/
├── starter/harness.py        # the agent: everything in the prompt, raw tools, unprotected actions
├── starter/check_actions.py  # calls your action tools directly, without a model, and counts the effects
├── cases/                    # the incidents of this session
└── checks/                   # the success criteria of each incident (PASS / FAIL)
```

| Case | What happens | Exercise |
|---|---|---|
| `s02-noisy-gateway` | One gateway instance leaks memory, among thousands of log lines and 200 duplicated alerts. | 1 |
| `s02-hidden-config-change` | The gateway rejects orders. The cause is one line in the logs of another component. | 1 |
| `s02-firewall-change` | The gateway cannot reach the database. The cause is one line among errors. | 1 |
| `s02-lost-response` | A restart is applied, but its answer never arrives. | 2 |

Every check in exercise 1 includes a **token budget**: all the model requests
of a run together must stay under 40 000 input tokens. The starter uses more
than 100 000 in its first request.

Runbooks live in `meridian/data/runbooks/`. Each one has a version and says
which component versions it applies to (`applies_to`). There are two gateway
runbooks: one for 2.3.x (restart the whole service) and one for 2.4.x
(restart a single instance). Production runs 2.4.0.

## The commands

```bash
uv run mrx-chaos run sessions/course-02-agentic-ai/02-context-tools-skills/cases/s02-noisy-gateway.yaml --ticks 3
uv run python -m meridian.run --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness.py --scenario sessions/course-02-agentic-ai/02-context-tools-skills/cases/s02-noisy-gateway.yaml
uv run python -m meridian.run --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness.py --cases-dir sessions/course-02-agentic-ai/02-context-tools-skills/cases --summary
uv run python sessions/course-02-agentic-ai/02-context-tools-skills/starter/check_actions.py --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness.py
```

The starter's first request is expensive. Run it once, on one case, to see
the trace; then work on your own version.

## Exercise 1 — Find the needle without reading the haystack

Copy `starter/harness.py` to `starter/harness_v2.py` and build three things.

**A. A context policy.** Rewrite `build_prompt`: what goes in the first
message, and what the agent loads with a tool when it needs it. Write the
policy down in three lines: always in the prompt, loaded on demand, never
loaded.

**B. A log summary tool, `summarize_logs(component)`.** Logs are written from
a few dozen templates; only the values change:

```text
INFO  order accepted id=O481920 member=NORDBANK side=buy
INFO  order accepted id=O519034 member=ATLAS-SEC side=sell
WARN  max_connections changed from 500 to 60 (CHG-4471); new connections above the limit are rejected
```

The first two lines have the same template; the third appears once. Build a
tool that reads all the lines of a component (`ctx.deps.tools.read_logs(...)`),
turns each line into its template by replacing the variable parts (numbers,
ids, `key=value` values) with placeholders, groups the lines by template and
counts them, and returns the rare and new templates first, each with one full
example line and its log ref. 5 000 lines should become a result the model
can read in one go.

Decide: what counts as a variable part, what counts as rare or new, how many
templates to return, and what the docstring tells the model.

**C. A runbook finder, `find_runbook(component)`.** Instead of every runbook,
return only the one that applies to the version in production. The version
is in the metrics (`version`); the runbook list gives `applies_to`, e.g.
`order-gateway 2.4.x`. Only the matching runbook enters the context.

Then run the three exercise 1 cases with `harness_v2.py`.

**Done when** `s02-noisy-gateway`, `s02-hidden-config-change` and
`s02-firewall-change` pass, each under the token budget.

**Write down:** your context policy; how your summary decides what is rare;
one case where your first version of the policy dropped what the agent
needed, and how you got it back without putting everything in the prompt.

## Exercise 2 — Actions that are safe to repeat

The executor (`meridian/actions.py`) already understands two things:

- `idempotency_key`: the same key with the same action and target returns the
  earlier action without a new effect;
- `ctx.deps.tools.action_status(key)`: what happened to the action sent with
  that key (`completed`, or `unknown_key` if it never arrived).

It does not decide anything for you. Build the layer around your actions in
`harness_v2.py`:

1. **One intent, one key.** Derive the key from the intent (incident, action,
   target), so that the same request always gets the same key, whatever the
   model writes.
2. **Ask before acting, and after a lost answer.** If the intent was already
   carried out, report it instead of acting. If the answer times out, ask the
   executor what happened instead of trying again.
3. **Act only if the reason still holds.** `restart_instance` restarts an
   instance only while its memory is above 90 % (or it is not running).
   Otherwise it refuses and says why.
4. **Dry run for the whole service.** `restart_service` does nothing without
   an explicit confirmation argument; it answers with what it would do and
   the impact (every member session on the service is dropped).

Test without a model, then with the model:

```bash
uv run python sessions/course-02-agentic-ai/02-context-tools-skills/starter/check_actions.py --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness_v2.py
uv run python -m meridian.run --harness sessions/course-02-agentic-ai/02-context-tools-skills/starter/harness_v2.py --scenario sessions/course-02-agentic-ai/02-context-tools-skills/cases/s02-lost-response.yaml
```

**Done when** `check_actions.py` passes 4/4 and all four cases pass.

**Write down:** why a key chosen by the model is weaker than a key derived
from the intent; what "the answer was lost" means for the state of the
system; and one situation where your "one intent, one key" rule would be
wrong (a legitimate second restart).

## Deliverable

- `starter/harness_v2.py`.
- The notes of both exercises.
- One new case (in `cases/` and `checks/`) where the cause is a rare line in
  the logs of a component the alert does not mention.

## Conventions

- Keep a `status` field in the report.
- Do not edit `meridian/`.
- Critical components (`matching-engine`, `reference-db`) cannot be changed by
  this agent. The executor rejects those actions.

## Scope boundary

Context within one run, tools that summarise and select, runbooks loaded
when they apply, and actions that are safe to repeat. MCP is Session 3;
memory across runs is Session 4; permissions and approvals are Session 5;
retries and resuming interrupted work are Session 9.
