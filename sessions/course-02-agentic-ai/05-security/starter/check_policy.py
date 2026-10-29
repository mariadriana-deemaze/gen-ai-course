"""Exercise 1: score a harness's authorize on 22 action requests, without a model.

    uv run python sessions/course-02-agentic-ai/05-security/starter/check_policy.py --harness sessions/course-02-agentic-ai/05-security/starter/harness.py

Each request of data/requests.yaml becomes an ActionRequest (the same object the
harness gets from meridian.security.describe_action) and goes to authorize. The
decision must match; for "needs_approval", the approver role too. Target: 22/22.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from meridian.agent_runtime import identity_for, load_harness
from meridian.security import ActionRequest

REQUESTS = Path(__file__).resolve().parents[1] / "data" / "requests.yaml"


def build(item: dict, defaults: dict) -> ActionRequest:
    item = {**defaults, **item}
    approval = item["approval"]
    return ActionRequest(
        action=item["action"],
        target=item["target"],
        component=item["component"],
        critical=item["critical"],
        role=item["role"],
        actor=identity_for(item["role"])["name"],
        trading_session=item["trading_session"],
        time="2026-11-04 11:00" if item["trading_session"] else "2026-11-04 19:30",
        confirmation_ref="APR-0001" if approval else None,
        approval_status=approval[0] if approval else None,
        approver_role=approval[1] if approval else None,
        approval_matches=bool(approval and approval[2]),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--harness", required=True)
    args = parser.parse_args()
    harness = load_harness(args.harness)
    data = yaml.safe_load(REQUESTS.read_text(encoding="utf-8"))
    passed = 0
    for item in data["requests"]:
        decision = harness.policy.authorize(build(item, data["defaults"]))
        got = decision.get("decision")
        right = got == item["expect"] and (item["expect"] != "needs_approval" or decision.get("approver_role") == item.get("approver"))
        passed += right
        context = f"{item['role']:<17} {item['action']:<16} {item['target']:<18} {'session' if item.get('trading_session', True) else 'after hours':<11} approval {item.get('approval')}"
        print(f"  [{'x' if right else ' '}] {item['id']} {context}")
        if not right:
            expected = item["expect"] + (f" ({item['approver']})" if item.get("approver") else "")
            print(f"        got {got} ({decision.get('approver_role')}), expected {expected}. {item.get('why', '')}")
    total = len(data["requests"])
    print(f"\n{passed}/{total} decisions right   ->  {'PASS' if passed == total else 'FAIL'}")


if __name__ == "__main__":
    main()
