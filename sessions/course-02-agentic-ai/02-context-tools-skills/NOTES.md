# Session 2 notes — context engineering, tools and skills (WIP)

Harness: `starter/harness.py` as supplied, on `claude-opus-5-5`.

## Exercise 1 — the context policy

| | What | Why there |
|---|---|---|
| Always | The incident line; the open alerts collapsed to one line per `(rule, component, instance)` with a count; the platform map | Small, and needed before the agent can choose its first call. |
| On demand | Log lines, through `summarize_logs`; runbook text, through `find_runbook` | Large, and only ever needed for one or two components per incident. |
| Never | Raw log lines; runbooks for other versions | `read_logs` is out of `TOOLS`, so the raw dump is unreachable. |

The starter's first message was over 100 000 tokens against a 40 000 budget.
This one is 191.

### How the summary decides what is rare

Alerts collapse on an exact key, because duplicates are identical by
construction. Log lines never are, so `template_of` computes a key by
substitution — ids, IP addresses, `key=value` values, then any number left.
Ids go first: `CHG-4471` is not a `key=value` pair, and the number rule would
otherwise shred it into `CHG-<n>`.

A template is rare when it was seen at most twice, **or** first seen after
`_new_since(lines, now)`. Both clauses are needed, one per case:
`s02-hidden-config-change` hides its cause in a line seen once, and
`s02-noisy-gateway` logs `WARN GC pause` 15 times — not rare by count, but
never seen before tick 100 of 120.

Rare templates come first, ascending by count, each with a full example line
and its `ref` to cite. Common ones are a count with no example. That is the
inverse of the alert ordering, deliberately: for alerts volume is the signal,
for logs it is the noise.

### Where the first policy dropped what the agent needed

Taking the logs and runbooks out of the prompt also took out every component
name except the one in the alert. Every tool takes a component as an argument,
so the agent could only ask about what it already knew — and in
`s02-hidden-config-change` the alert is on `order-gateway` while the cause is
one line in `reference-db`'s logs. It would have had to guess the name.

The platform map restores exactly what was lost, in ten static lines. The run
confirmed it: the agent called `summarize_logs("order-gateway")`, found nothing
conclusive, then `summarize_logs("reference-db")` — a name it could only have
read from the map. The map paid twice over, since it also marks `reference-db`
critical, which the agent cited as its reason to escalate instead of acting.

A second thing the first rule got wrong: "first seen in the last 30 ticks"
marked *everything* new on `s02-lost-response`, whose whole log is 30 ticks.
`_new_since` now caps the window at the older half of the log.

### Status

Run on `anthropic:claude-sonnet-5-5`, with the whole catalog on. All three cases
investigate correctly and all three fail on the budget alone; every other check
passes.

| Case | Requests | Input tokens |
|---|---|---|
| `s02-noisy-gateway` | 8 | 44 516 |
| `s02-firewall-change` | 9 | 53 614 |
| `s02-hidden-config-change` | 13 | 80 428 |

Two causes, both from the catalog being on. The router hands the agent ten or
eleven tools instead of seven, and every schema is re-sent on every request; and
the extra tools invite detours, so `s02-hidden-config-change` spent turns on
`request_approval`, a rollback against critical `reference-db`, and
`search_tools` before finishing.

Before the `insufficient_information` guard was added, Haiku reported
`insufficient_information` on two of the three cases after a single request and
no tool call at all, writing its plan into the report instead of acting. The
guard fixed that; the budget overrun is what remains.

## Exercise 2 — tool selection

### What the model decides, and what code decides

The model reads the incident line and one summary per tool, and names up to
four specialists. That is all it decides.

Code decides everything that has to be guaranteed:

- `CORE` is always added, so no guess can leave the agent without metrics,
  logs, changes, runbooks or a way to act.
- `NEVER` is subtracted before the model sees the catalog and again after it
  answers, so a banned tool cannot be proposed, invented, or unlocked through
  `search_tools`.
- `SUPERSEDED` hides a tool that a better one replaces: `read_logs` and
  `search_logs` beside `summarize_logs`, `list_runbooks` and `read_runbook`
  beside `find_runbook`. An agent offered both will sometimes take the worse.
- Names the model invented are dropped, and the total is capped.
- If the model is absent or fails, word overlap against the tool lines takes
  over. A failed router must not stop an incident.

Putting the two actions in `CORE` was not a preference. With them left to the
router, `s02-noisy-gateway` was offered no `restart_instance` and
`s02-firewall-change` no `rollback_change`, so neither case could pass however
good the investigation was.

### Where search_tools recovered what the router missed

R11, `TLS handshake failures on order-gateway for several members`. The router
does not offer `certificate_status`. Calling
`search_tools("look up the TLS certificate of a member")` returns it and makes
it callable from the next step. Verified in code, without a model.

### Status

With `anthropic:claude-sonnet-5-5` as the router: **coverage 0.96, size 10.1,
never offered 0**. Two gates met; `size` misses by two names across twenty
incidents.

The two gates are in tension, and every configuration tried falls on one side:

| CORE | picks allowed | coverage | size |
|---|---|---|---|
| 8 | 2 | 0.93 | 9.7 |
| 8 | 3 | 0.96 | 10.1 |
| 7 | 3 | 0.93 | 9.3 |
| 7 | 4 | 0.96 | 10.1 |

## Exercise 3 — repeatable actions

### 3. Why a model-chosen key is weaker than an intent-derived one

A model-chosen key is unstable. The same intent can produce different strings
across calls: `"INC-2103:restart:order-gateway-2"` vs `"restart-order-gateway-2"`.
The executor treats each as distinct, so the guard is bypassed and the restart
happens again. A derived key — `f"{incident_id}:{action}:{target}"` — is
deterministic, computed from structured data, not the model's wording.

But "one intent, one key" is not always right. A legitimate second restart in
the same incident gets silently blocked. Example: at tick 10, memory is 88 %,
restart is applied, key is `INC-2103:restart_instance:order-gateway-2`. Memory
drops. At tick 30, a new fault injects memory at 0.5 %/tick, it climbs past 90
%, and the agent correctly restarts again — but the key is identical, so the
guard returns `"already_applied"`. Fix: extend the key with a condition snapshot,
e.g. `f"{incident_id}:{action}:{target}:{memory_pct_floor}"`, so a restart at 90
% produces a different key from one at 95 %. Or allow a new action after a time
threshold since the last one.
