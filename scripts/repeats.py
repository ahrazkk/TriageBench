"""Summarise repeated runs: how much do results move when nothing changes but the dice?

Repeat 0 is the canonical run (the newest untagged `*-<system>-dev.json` with the current prompt
version); repeats k > 0 are `*-<system>-dev-r<k>.json`, made with fresh model calls.

For each system: accuracy per repeat, mean and range, and the flip rate (share of cases whose
correctness differs between repeats). Comparisons use each case's mean correctness over repeats:
a case counts as "fixed" when the second system is right more often, "broken" when less often,
then exact McNemar on those counts. Saves results/<ts>-repeats-dev.json.

    bash scripts/dev.sh python scripts/repeats.py
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from triagebench.config import settings
from triagebench.evals.stats import mcnemar_exact

ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = ["b1", "b2", "b3"]


def runs_for(system: str, version: str, tag: str = "") -> list[dict]:  # type: ignore[type-arg]
    res = ROOT / "results"
    if tag:  # tagged repeat set, e.g. --tag fix -> *-b2-dev-fix-r0.json, -r1, -r2
        found = []
        for k in range(5):
            files = sorted(res.glob(f"*-{system}-dev-{tag}-r{k}.json"))
            found += [json.loads(files[-1].read_text(encoding="utf-8"))] if files else []
        return found
    canon = [
        json.loads(p.read_text(encoding="utf-8")) for p in sorted(res.glob(f"*-{system}-dev.json"))
    ]
    canon = [r for r in canon if r.get("prompt_version") == version]
    out = canon[-1:]
    for k in (1, 2, 3, 4):
        files = sorted(res.glob(f"*-{system}-dev-r{k}.json"))
        out += [json.loads(files[-1].read_text(encoding="utf-8"))] if files else []
    return out


def per_case(runs: list[dict], axis: str) -> dict[str, float]:  # type: ignore[type-arg]
    ids = runs[0]["metrics"][axis]["per_case_correct"].keys()
    return {c: mean(float(r["metrics"][axis]["per_case_correct"][c]) for r in runs) for c in ids}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="", help="use the tagged repeat set *-dev-<tag>-r<k>.json")
    tag = ap.parse_args().tag
    version = settings().prompt_version
    summary: dict[str, object] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "prompt_version": version,
        "tag": tag,
        "systems": {},
        "paired": {},
    }
    means: dict[str, dict[str, dict[str, float]]] = {}
    total_cost = 0.0
    for s in SYSTEMS:
        runs = runs_for(s, version, tag)
        if not runs:
            continue
        entry: dict[str, object] = {"repeats": len(runs), "files": [r["run_id"] for r in runs]}
        means[s] = {}
        for axis in ("axis_A", "axis_B"):
            accs = [r["metrics"][axis]["accuracy"] for r in runs]
            pc = per_case(runs, axis)
            flips = sum(1 for v in pc.values() if 0 < v < 1)
            entry[axis] = {
                "accuracy_per_repeat": accs,
                "mean": mean(accs),
                "min": min(accs),
                "max": max(accs),
                "flip_rate": flips / len(pc),
                "flipping_cases": flips,
            }
            means[s][axis] = pc
        cost = sum(r["metrics"]["cost_usd_total"] for r in runs)
        entry["cost_usd_all_repeats"] = cost
        total_cost += cost
        summary["systems"][s] = entry  # type: ignore[index]
        a, b = entry["axis_A"], entry["axis_B"]  # type: ignore[index]
        print(
            f"{s}: {len(runs)} repeats | A {a['mean']:.1%} (range {a['min']:.0%}-{a['max']:.0%}),"
            f" flips {a['flip_rate']:.0%} | B {b['mean']:.1%} (range {b['min']:.0%}-{b['max']:.0%}),"
            f" flips {b['flip_rate']:.0%} | ${cost:.3f}"
        )
    for x, y in (("b1", "b2"), ("b2", "b3"), ("b1", "b3")):
        if x not in means or y not in means:
            continue
        for axis in ("axis_A", "axis_B"):
            px, py = means[x][axis], means[y][axis]
            fixed = sum(1 for c in px if py[c] > px[c])
            broken = sum(1 for c in px if py[c] < px[c])
            delta = mean(py.values()) - mean(px.values())
            p = mcnemar_exact(broken, fixed)
            summary["paired"][f"{y}_vs_{x}_{axis}"] = {  # type: ignore[index]
                "delta_mean_accuracy": delta,
                "fixed": fixed,
                "broken": broken,
                "p": p,
            }
            print(
                f"  {y} vs {x} {axis[-1]}: {delta * 100:+.1f} pts, fixed {fixed} / broken {broken}, p = {p:.3f}"
            )
    summary["total_cost_usd"] = total_cost
    path = settings().results_dir / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-repeats-dev.json"
    path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print("saved", path.name, f"(total cost of these runs ${total_cost:.3f})")


if __name__ == "__main__":
    main()
