"""Error analysis: show the cases a system got wrong, next to the label, so failure modes can be
named (PLAN section 1: "read 10 failed cases and name the patterns").

    bash scripts/dev.sh python scripts/errors.py results/<run>.json [--axis B] [--limit 15]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from triagebench.data import load_labels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("result", type=Path)
    ap.add_argument("--axis", choices=["A", "B"], default="B")
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()
    run = json.loads(args.result.read_text(encoding="utf-8"))
    labels = load_labels()
    field = "failure_type" if args.axis == "A" else "cause_class"
    m = run["metrics"][f"axis_{args.axis}"]
    print(f"{run['system']} axis {args.axis}: {m['correct']}/{m['n']} correct")
    print("confusion (gold -> predicted counts):")
    for gold, row in m["confusion"].items():
        print(f"  {gold:<20} {dict(sorted(row.items(), key=lambda kv: -kv[1]))}")
    pairs = Counter()
    shown = 0
    for o in run["outcomes"]:
        lab = labels.get(o["case_id"])
        if lab is None:
            continue
        pred = (o["result"] or {}).get(field, "(no answer)")
        gold = getattr(lab, field)
        if pred == gold:
            continue
        pairs[(gold, pred)] += 1
        if shown >= args.limit:
            continue
        shown += 1
        res = o["result"] or {}
        print(f"\n--- {o['case_id']}  gold={gold}  pred={pred}  conf={res.get('confidence')}")
        print(f"    tools: {o['tool_calls']}  errors: {o['errors'][:1]}")
        print(f"    label: {lab.root_cause[:260]}")
        print(f"    model: {str(res.get('root_cause', ''))[:260]}")
        print(f"    label notes: {lab.notes[:160]}")
    print("\nmost common (gold, predicted) confusions:", pairs.most_common(6))


if __name__ == "__main__":
    main()
