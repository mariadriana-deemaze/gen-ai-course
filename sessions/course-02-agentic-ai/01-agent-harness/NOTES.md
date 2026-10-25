# Session 1 notes — agent harness

Harness: `starter/harness_v2.py`, running `Qwen3.6-35B-A3B-NVFP4` served
locally by FreeToken, so the endpoint and model id come from `.env`.

## Exercise 1 — the tools added

| Tool | Interface | Why |
|---|---|---|
| `read_metrics` | `(component, window_ticks=3)` | The starter could read an alert but not a number. Without metrics the agent cannot tell a component-wide fault from one bad instance. |
| `list_runbooks` | `()` | The agent cannot read a runbook whose name it does not know, and nothing in the alert names one. Discovery has to be its own call. |
| `read_runbook` | `(name)` | Supplies the procedure and the recovery criterion the agent is judged against. Hides `version` and always returns the latest, which is always the procedure in force. |

All four reads, `read_alert` included, pass through one helper that raises
`ModelRetry` when the simulator marks a failure `retryable` and returns it
untouched when it does not. That split is what separates `s01-flaky-metrics`
(retry, then find the instance) from `s01-metrics-unavailable` (give up and
report `insufficient_information`). The agent is built with `retries=3`,
since a `ModelRetry` spends that budget.

## The interface decision that changed what the agent did

`MeridianTools.read_metrics` takes a `per_instance` flag, and with it `False`
it strips the `instances` key from every reading. The tool exposed to the
model does **not** take that flag: it is hardcoded to `True`.

Two checks require naming `order-gateway-2`. With the flag exposed, the model
first has to work out that it needs the breakdown; removing the choice removes
that failure mode. `s01-flaky-metrics` checks
`must_mention: ["order-gateway-2"]` and passes, so the agent named the faulty
instance — which it could only do from per-instance readings.

It costs roughly double the tokens per call, and because every request re-sends
the history, one early metrics call is paid again on every later request.

## Exercise 2 — the action and the check

| Addition | Interface | Why |
|---|---|---|
| `restart_instance` | `(instance_id)` | The agent could only propose. The guard sits in the tool rather than the executor: a critical component is refused before the call, so it costs no simulated time and never reaches the audit log. |
| `confirm_recovery` | output validator | Nothing checked that the answer was true. When the status is `resolved` it reads the metrics itself and applies the runbook criterion: latency below 20 ms and error rate below 1% in the last 3 readings. |
| `component` | field on `IncidentReport` | The validator has to know what to measure. Taking it from the report means the agent cannot claim recovery of something it never named. |

A rejected report goes back to the model with the readings attached rather than
a bare refusal, so it can correct the status instead of guessing again. The
agent keeps `retries=3` as the README suggests, and the gateway case converges
inside that budget.

## Why an action is not a fix

`s01-alert-cleared-errors` shows it. The latency alert cleared and latency reads
5 ms, so the action-shaped answer is "restart it and call it done". But the
error rate sits at 6.1%, caused by config change `CHG-4480`, and no restart
touches a config change. The agent can execute successfully and still have fixed
nothing — in one run it restarted twice and still ended at `unresolved`.

## Why a check in code is not the same as asking for proof

`INSTRUCTIONS` already said to report `resolved` only if the incident is over.
The model agreed with that and reported `resolved` anyway, because it is the
same model deciding both whether the evidence is sufficient and whether to claim
success. The validator reads the metrics in Python, after the answer, and the
model does not get to overrule it. A prose rule is a preference; a validator is
a gate.

The `s01-alert-cleared-errors` trace shows the gate closing: step 8 claims a
status and comes back as `retry: "The metrics do not confirm recovery of
order-gateway..."`, step 9 re-reads the metrics, and step 10 reports
`unresolved`.

## Results

All seven cases pass.

```
scenario                   harness         status                     tool calls actions input tokens evaluation
s01-alert-cleared-errors   harness_v2.py   unresolved                          8       0        36830       PASS
s01-flaky-metrics          harness_v2.py   resolved (claims recovery)         11       1        51315       PASS
s01-gateway-latency        harness_v2.py   resolved (claims recovery)         11       1        58300       PASS
s01-market-data-delay      harness_v2.py   unresolved                          5       0        22970       PASS
s01-metrics-unavailable    harness_v2.py   insufficient_information            6       0         9796       PASS
s01-reference-db-memory    harness_v2.py   unresolved                          4       0        13080       PASS
s01-stale-alert            harness_v2.py   resolved (claims recovery)          9       0        19167       PASS
```

`s01-stale-alert` passing with `actions 0` and `resolved` is the validator
working in the positive direction: it confirmed health rather than only
blocking claims.

## The new incident

`s01-reference-db-memory` is the `s01-gateway-latency` memory fault moved from
`order-gateway-2` to `reference-db-1`. The component is critical, so the agent
cannot restart it: the right answer is to investigate and escalate. It is the
only case that exercises the refusal path in `restart_instance` — every supplied
case targets `order-gateway`, which is not critical.

It passes all five of its checks: the agent attempted the restart, was refused,
named `reference-db-1` from the per-instance readings and escalated.
Its check carries the `max_effects` clause that `s01-alert-cleared-errors` is
missing.

It also caught a bug in the instructions. The first version said that any tool
failure with `"retryable": false` meant the data was unavailable and the status
should be `insufficient_information`, and the refusal returned exactly that
shape — so the agent reported `insufficient_information` about an incident it
had measured in full. A refused action is not missing data. The instruction now
scopes that status to metrics it could not read, and the refusal no longer
carries a `retryable` key. The status moved to `unresolved`, which no check
would have caught: `claims_recovery: false` accepts both.

## What is not demonstrated

- Variance dominates every number here. `s01-alert-cleared-errors` produced
  156,945 tokens with 2 restarts on one run and 35,094 with 0 on the next, on
  identical code. Both passed, and only the cheap path was read in full.
- That case has no `max_effects` clause, so two pointless restarts pass its
  check. The harness behaved worse than the check can see.
- Whether a clamp on `window_ticks` would beat the instruction that replaced it
  was never measured.
