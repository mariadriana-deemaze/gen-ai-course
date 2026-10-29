"""Exercise 2: score a harness's mask and check_message, without a model.

    uv run python sessions/course-02-agentic-ai/05-security/starter/check_privacy.py --harness sessions/course-02-agentic-ai/05-security/starter/harness.py

For each text of data/privacy.yaml, mask must remove what is in `hide` and keep
what is in `keep`. For each message, check_message must allow or block it.
Target: everything hidden, at least 95 % of what must stay kept, every message right.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from meridian.agent_runtime import load_harness

DATA = Path(__file__).resolve().parents[1] / "data" / "privacy.yaml"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--harness", required=True)
    args = parser.parse_args()
    harness = load_harness(args.harness)
    data = yaml.safe_load(DATA.read_text(encoding="utf-8"))

    print("== mask")
    leaked = hide_total = kept = keep_total = 0
    for item in data["mask"]:
        masked = harness.privacy.mask(item["text"])
        left = [value for value in item.get("hide", []) if value in masked]
        lost = [value for value in item.get("keep", []) if value not in masked]
        leaked += len(left)
        hide_total += len(item.get("hide", []))
        kept += len(item.get("keep", [])) - len(lost)
        keep_total += len(item.get("keep", []))
        print(f"  [{'x' if not left and not lost else ' '}] {masked[:100]}")
        if left:
            print(f"        still there: {left}")
        if lost:
            print(f"        lost: {lost}")

    print("\n== messages")
    messages_ok = 0
    for item in data["messages"]:
        reason = harness.privacy.check_message(item["channel"], item["text"])
        got = "block" if reason else "allow"
        right = got == item["expect"]
        messages_ok += right
        print(f"  [{'x' if right else ' '}] {item['channel']:<34} {item['text'][:50]:<50} -> {got}")
        if not right:
            print(f"        expected {item['expect']}. {item.get('why', '')}")

    kept_share = kept / keep_total
    passed = leaked == 0 and kept_share >= 0.95 and messages_ok == len(data["messages"])
    print(f"\nhidden {hide_total - leaked}/{hide_total}   kept {kept}/{keep_total} ({kept_share:.0%}, target 95%)   messages {messages_ok}/{len(data['messages'])}   ->  {'PASS' if passed else 'FAIL'}")


if __name__ == "__main__":
    main()
