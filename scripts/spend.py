"""Real API spend so far: only live (non-replayed) model calls cost money.

bash scripts/dev.sh python scripts/spend.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

total = 0.0
for f in sorted((ROOT / "results").glob("*-b[0-9]-*.json")):
    run = json.loads(f.read_text(encoding="utf-8"))
    live = 0.0
    for o in run["outcomes"]:
        n = o["live_calls"] + o["replayed_calls"]
        if n:
            live += o["cost_usd"] * o["live_calls"] / n
    total += live
    print(f"{run['run_id']:<42} live spend ${live:.4f}")
print(f"TOTAL real API spend: ${total:.3f}")
