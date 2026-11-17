"""Exercise 3 — Resume after a crash, without repeating what was done.

The lab runs the first half of incident INC-2 and saves a checkpoint after each step:
findings → plan → step 1 (fail over to site C) → step 2 (resend the delayed confirmations to settlement).
Then the process crashes. resume(room, checkpoint) is what the next shift runs.

The resume below works, but it is naive: it trusts the checkpoint and hands the whole plan to remediation again.
Run scenario A and look at the result. Then make it safe.

What a good resume must do:
- never repeat an effect that already happened (resending the confirmations twice charges members twice);
- not trust "status" in the checkpoint blindly: the crash can happen between doing a step and saving that it was done;
- check every approval it reuses: time passes during a crash (scenario B);
- finish the work that is really left, with the agents, and confirm the result.

What you can use (lab/room.py has the details):
- checkpoint["plan"]: the steps, each with action, target, key (its idempotency key), status, approval_ref
- room.history(): every action the systems really ran, with its key. It survives the crash.
- room.approval_status(ref): is an approval still valid now?
- await room.ask("remediation" | "reviewer", message): give a task to an agent
- room.note(text): show on the board what your code decided

Hints:
- Where is the truth after a crash: in the checkpoint, in the systems, or in the agent's memory?
- An idempotency key is only useful if the retry sends the same key.
- Remediation does exactly what you ask. If you ask for everything, it does everything.
"""

from __future__ import annotations


def _ran(step: dict, history: list[dict]) -> dict | None:
    """The effect that shows this step really ran: same idempotency key, or the same action on the same target."""
    return next((e for e in history if e["key"] == step["key"]), None) or \
        next((e for e in history if e["action"] == step["action"] and e["target"] == step["target"]), None)


async def resume(room, checkpoint: dict) -> None:
    # 1. The truth is in the systems, not in the checkpoint: the crash can fall between doing a step and saving it.
    history = room.history()
    left = []
    for step in checkpoint["plan"]:
        if effect := _ran(step, history):
            room.note(f"Step {step['step']} {step['action']} {step['target']}: checkpoint says '{step['status']}', "
                      f"history says it ran at {effect['time']} [{effect['key']}]. Not repeating it.")
        else:
            left.append(step)

    # 2. Every approval we would reuse is checked now: time passed during the crash.
    for step in left:
        ref = step["approval_ref"]
        if ref and not (status := room.approval_status(ref))["valid"]:
            room.note(f"Step {step['step']}: approval {ref} is no longer valid ({status['reason']}). Asking the on-call again.")
            step["approval_ref"] = None

    # 3. Only the steps that are really left, each with its own key, so a retry is still safe.
    if left:
        findings = "\n".join(f"- {name}: {text}" for name, text in checkpoint["findings"].items())
        lines = []
        for step in left:
            approval = (f"approval_ref {step['approval_ref']}" if step["approval_ref"]
                        else "no approval: leave approval_ref empty so the on-call is asked")
            lines.append(f"{step['step']}) {step['action']} {step['target']} · idempotency_key {step['key']} · {approval}")
        done = [f"{s['step']}) {s['action']} {s['target']}" for s in checkpoint["plan"] if s not in left]
        room.note("Left to do: " + ", ".join(f"step {s['step']}" for s in left) + ". Handing only those to remediation.")
        await room.ask("remediation", (
            f"{checkpoint['brief']}\n\nFindings:\n{findings}\n\n"
            + (f"Already done before the crash, do NOT do again: {'; '.join(done)}.\n\n" if done else "")
            + "Carry out only these steps, with exactly these idempotency keys and approvals:\n" + "\n".join(lines)
            + "\n\nThen check gateway_state and report."))
    else:
        room.note("Every step of the plan already ran: nothing to hand to remediation.")

    # 4. Confirm the result: every step ran exactly once, and the reviewer checks the real state.
    history = room.history()
    for step in checkpoint["plan"]:
        runs = [e for e in history if e["action"] == step["action"] and e["target"] == step["target"]]
        room.note(f"Step {step['step']} {step['action']} {step['target']}: ran {len(runs)} time(s)"
                  + ("." if len(runs) == 1 else ". ⚠ expected exactly once."))
    await room.ask("reviewer", f"{checkpoint['brief']}\n\nThe plan was: "
                   + "; ".join(f"{s['step']}) {s['action']} {s['target']}" for s in checkpoint["plan"])
                   + ". Check the gateway's real state and the systems' history: is the incident over, and did any action run twice?")
