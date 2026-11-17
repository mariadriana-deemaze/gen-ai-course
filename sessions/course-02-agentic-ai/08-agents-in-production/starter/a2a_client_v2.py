"""Exercise 1 — Ask another team's agent to act, through A2A.

The confirmations of batch 10:12 reached settlement late and must be resent. Your team has no tool for that:
settlement's systems belong to the settlement team. Their agent, the "Settlement Agent", speaks A2A (Agent2Agent).
The on-call already approved the resend (a2a.request has the approval reference).

resend(a2a) talks to the Settlement Agent and returns the final Task.

The client below works, but it is naive. It answers settlement's question correctly (inside the same task), but
because settlement is slow, it then sends the whole request again. Run it and look at the result. Then write a client
that holds up.

What a good client must do:
- read the Agent Card first: what the skill needs, and how to authenticate;
- send the request once, with what the skill asks for;
- answer the agent inside the same task if it needs more from you (TASK_STATE_INPUT_REQUIRED);
- wait for a long task by asking for its state (GetTask), never by sending the request again;
- check the result before returning it.

What you can use (lab/a2a.py has the details): a2a.card(), await a2a.send_message(message, token),
await a2a.get_task(task_id, token), a2a.token, a2a.request, await a2a.wait(seconds), a2a.note(text).
A message (A2A v1.0): {"role": "ROLE_USER", "parts": [{"text": "..."}, {"data": {...}}], "taskId"?: "...", "contextId"?: "..."}

Hints:
- Read every field of the skill's description in the card. One sentence in it is the whole exercise.
- TASK_STATE_WORKING is not TASK_STATE_FAILED. What is the difference between sending again and asking again?
- A task that asks you a question is still the same task. The naive client already gets this part right.
"""

from __future__ import annotations

TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}
MAX_POLLS = 20
SEND_ATTEMPTS = 3


def _state(task: dict) -> str | None:
    return task.get("status", {}).get("state")


def _asked(task: dict) -> dict:
    """The data part of the agent's question, if it has one."""
    return next((p["data"] for p in task.get("status", {}).get("message", {}).get("parts", []) if "data" in p), {})


async def resend(a2a) -> dict:
    request = a2a.request

    # 1. Read the Agent Card: the skill and how to authenticate.
    card = a2a.card()
    skill = next(s for s in card["skills"] if s["id"] == "resend-confirmations")
    if "bearer" not in card["securitySchemes"]:
        raise RuntimeError("Settlement no longer accepts a bearer token: check its Agent Card.")
    a2a.note(f"Card read: skill '{skill['name']}', bearer auth, long-running, idempotency_key supported.")

    # 2. Send the request once. The key is derived from the request itself, so a resend after a lost reply
    #    (same key, no taskId) returns the same task instead of starting a new resend.
    key = f"{request['incident']}:{request['batch']}:{request['approval_ref']}"
    text = f"Please resend {request['batch']} for incident {request['incident']}. Approval: {request['approval_ref']}."
    message = {"role": "ROLE_USER", "parts": [
        {"text": text},
        {"data": {"batch": request["batch"], "approval_ref": request["approval_ref"],
                  "incident": request["incident"], "idempotency_key": key}},
    ]}
    # Until we have a taskId, a lost reply leaves us not knowing whether settlement got the request.
    # Sending the same message (same key) again is safe: settlement answers with the task it already has.
    for attempt in range(1, SEND_ATTEMPTS + 1):
        try:
            task = await a2a.send_message(message, a2a.token)
            break
        except (TimeoutError, ConnectionError) as error:
            if attempt == SEND_ATTEMPTS:
                raise RuntimeError(f"No reply from settlement after {SEND_ATTEMPTS} sends with key {key}. "
                                   "Do not send a new request: escalate, and retry later with the same key.") from error
            a2a.note(f"No reply to the request ({type(error).__name__}): sending it again with the same key {key}.")
            await a2a.wait(attempt)
    if "error" in task:
        raise RuntimeError(f"Settlement refused the request: {task['error']['message']}")
    task_id = task["id"]
    a2a.note(f"Request accepted as {task_id} (idempotency_key {key}).")

    # 3. Follow that one task until it ends: answer its questions in the same task, poll while it works.
    for _ in range(MAX_POLLS):
        state = _state(task)
        if state in TERMINAL:
            break
        if state == "TASK_STATE_INPUT_REQUIRED":
            asked = _asked(task)
            if asked.get("reply_with") == "confirm_count":
                a2a.note(f"Settlement asks to confirm {asked['confirmations']} confirmations "
                         f"({asked.get('members')} members charged): confirming in {task_id}.")
                reply = {"data": {"confirm_count": asked["confirmations"]}}
            else:
                reply = {"text": f"Approval: {request['approval_ref']}. Batch: {request['batch']}."}
            task = await a2a.send_message({"role": "ROLE_USER", "taskId": task_id, "contextId": task["contextId"],
                                           "parts": [reply]}, a2a.token)
        else:  # SUBMITTED or WORKING: ask again, never send again
            await a2a.wait(1)
            task = await a2a.get_task(task_id, a2a.token)
        if "error" in task:
            raise RuntimeError(f"Settlement answered with an error for {task_id}: {task['error']['message']}")

    # 4. Check the result before returning it.
    state = _state(task)
    receipt = next((p["data"] for art in task.get("artifacts", []) for p in art.get("parts", []) if "data" in p), {})
    if state == "TASK_STATE_COMPLETED" and receipt.get("batch") == request["batch"]:
        a2a.note(f"{task_id} completed: {receipt['batch']} {receipt.get('outcome')} at {receipt.get('sent_at')}.")
    elif state not in TERMINAL:
        a2a.note(f"{task_id} still {state} after {MAX_POLLS} polls: not resending, poll {task_id} again later.")
    else:
        a2a.note(f"{task_id} ended {state} (receipt {receipt or 'missing'}): escalate to the on-call, do not resend.")
    return task
