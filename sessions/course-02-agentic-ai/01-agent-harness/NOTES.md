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

## Results

```
scenario                   harness         status                     tool calls actions input tokens evaluation
s01-alert-cleared-errors   harness_v2.py   resolved (claims recovery)          7       0        18633       FAIL
s01-flaky-metrics          harness_v2.py   unresolved                          8       0        13998       PASS
s01-gateway-latency        harness_v2.py   unresolved                          6       0        16063       FAIL
s01-market-data-delay      harness_v2.py   unresolved                          6       0        19112       PASS
s01-metrics-unavailable    harness_v2.py   insufficient_information            7       0        14806       PASS
s01-stale-alert            harness_v2.py   unresolved                          7       0        25126       FAIL
```

The three failures are exactly the three exercise 2 cases, and all three show
`actions 0` — the agent has no action available yet.

## What is not demonstrated

- No `ModelRetry` was observed firing in any trace read in full. The retry
  handling is justified by `s01-flaky-metrics` passing, but has not been
  watched working.
- `window_ticks` is not really under the harness's control. The model supplies
  it explicitly on every call, and asks for 30, so the default never binds.
  Only a clamp inside the tool would change that, and it was not added.
- Whether `s01-gateway-latency` names the faulty instance is unconfirmed; the
  summary does not show diagnosis text and that trace was not read.
