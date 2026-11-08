"""Compare named groups of repeated runs (e.g. Haiku B2 x3 vs Sonnet B2 x3) on the same cases.

For each group: mean / range accuracy per axis, flip rate, cost and latency per triage, model
calls, and how often it answers "unknown". Then paired exact McNemar tests (first group vs each
other group) on each case's mean correctness over the group's runs. Cases where the groups
disagree are written to data/raw/_disagreements/<job_id>.json with the label and every answer,
for case-by-case review. Saves results/<ts>-<name>.json.

    bash scripts/dev.sh python scripts/compare_runs.py --name model-ablation \
        --group haiku results/A.json results/B.json results/C.json \
        --group sonnet results/D.json results/E.json results/F.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from triagebench.config import settings
from triagebench.data import load_cases, load_labels
from triagebench.evals.stats import mcnemar_exact, percentile

ROOT = Path(__file__).resolve().parents[1]
AXES = {"axis_A": "failure_type", "axis_B": "cause_class"}


def load(paths: list[str]) -> list[dict[str, Any]]:
    return [json.loads((ROOT / p).read_text(encoding="utf-8")) for p in paths]


def per_case(runs: list[dict[str, Any]], axis: str) -> dict[str, float]:
    ids = runs[0]["metrics"][axis]["per_case_correct"].keys()
    return {c: mean(float(r["metrics"][axis]["per_case_correct"][c]) for r in runs) for c in ids}


def group_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "runs": [r["run_id"] for r in runs],
        "model": runs[0]["model"],
        "prompt_version": runs[0].get("prompt_version"),
    }
    for axis in AXES:
        accs = [r["metrics"][axis]["accuracy"] for r in runs]
        pc = per_case(runs, axis)
        out[axis] = {
            "accuracy_per_run": accs,
            "mean": mean(accs),
            "min": min(accs),
            "max": max(accs),
            "flip_rate": sum(1 for v in pc.values() if 0 < v < 1) / len(pc),
        }
    outcomes = [o for r in runs for o in r["outcomes"]]
    answered = [o for o in outcomes if o["result"]]
    out["cost_usd_per_triage"] = mean(o["cost_usd"] for o in outcomes)
    out["latency_p50_s"] = percentile([o["latency_s"] for o in outcomes], 0.5)
    out["model_calls_mean"] = mean(o["steps"] for o in outcomes)
    out["tool_calls_mean"] = mean(len(o["tool_calls"]) for o in outcomes)
    out["unknown_rate"] = sum(1 for o in answered if o["result"]["cause_class"] == "unknown") / max(
        1, len(outcomes)
    )
    out["errors"] = sum(1 for o in outcomes if o["errors"])
    out["confidence_mean"] = mean(o["result"]["confidence"] for o in answered) if answered else 0
    return out


def answers(runs: list[dict[str, Any]], case_id: str) -> list[dict[str, Any]]:
    res = []
    for r in runs:
        o = next(x for x in r["outcomes"] if x["case_id"] == case_id)
        rr = o["result"] or {}
        res.append(
            {
                "run": r["run_id"],
                "failure_type": rr.get("failure_type"),
                "cause_class": rr.get("cause_class"),
                "confidence": rr.get("confidence"),
                "root_cause": rr.get("root_cause"),
                "next_step": rr.get("next_step"),
                "tool_calls": o["tool_calls"],
                "errors": o["errors"],
            }
        )
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--group", nargs="+", action="append", required=True, metavar="NAME FILE")
    args = ap.parse_args()
    groups = {g[0]: load(g[1:]) for g in args.group}
    names = list(groups)
    summary: dict[str, Any] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "name": args.name,
        "groups": {n: group_summary(rs) for n, rs in groups.items()},
        "paired": {},
    }
    for n, g in summary["groups"].items():
        a, b = g["axis_A"], g["axis_B"]
        print(
            f"{n:<10} {g['model']:<18} {len(g['runs'])} runs | A {a['mean']:.1%} "
            f"({a['min']:.0%}-{a['max']:.0%}) | B {b['mean']:.1%} ({b['min']:.0%}-{b['max']:.0%}), "
            f"flips {b['flip_rate']:.0%} | ${g['cost_usd_per_triage']:.4f}/triage | "
            f"P50 {g['latency_p50_s']:.1f}s | calls {g['model_calls_mean']:.1f} | "
            f"tools {g['tool_calls_mean']:.1f} | unknown {g['unknown_rate']:.0%} | "
            f"errors {g['errors']}"
        )
    base = names[0]
    labels = load_labels()
    jobs = {c.id: c.job_id for c in load_cases()}
    out_dir = settings().data_dir / "_disagreements"
    out_dir.mkdir(parents=True, exist_ok=True)
    disagree: Counter[str] = Counter()
    for other in names[1:]:
        for axis in AXES:
            px, py = per_case(groups[base], axis), per_case(groups[other], axis)
            fixed = [c for c in px if py[c] > px[c]]
            broken = [c for c in px if py[c] < px[c]]
            delta = mean(py.values()) - mean(px.values())
            p = mcnemar_exact(len(broken), len(fixed))
            summary["paired"][f"{other}_vs_{base}_{axis}"] = {
                "delta_mean_accuracy": delta,
                "fixed": fixed,
                "broken": broken,
                "p": p,
            }
            print(
                f"  {other} vs {base} {axis[-1]}: {delta * 100:+.1f} pts, "
                f"fixed {len(fixed)} / broken {len(broken)}, p = {p:.3f}"
            )
            for c in fixed + broken:
                disagree[c] += 1
                lab = labels[c]
                record = {
                    "case_id": c,
                    "job_id": jobs[c],
                    "label_pack": f"data/raw/_label_packs/{jobs[c]}.md",
                    "label": {
                        "failure_type": lab.failure_type,
                        "cause_class": lab.cause_class,
                        "root_cause": lab.root_cause,
                        "evidence_lines": lab.evidence_lines,
                        "notes": lab.notes,
                    },
                    "answers": {n: answers(groups[n], c) for n in names},
                    "mean_correct": {
                        n: {ax: per_case(groups[n], ax)[c] for ax in AXES} for n in names
                    },
                }
                (out_dir / f"{jobs[c]}.json").write_text(
                    json.dumps(record, indent=1), encoding="utf-8"
                )
    summary["disagreement_cases"] = sorted(disagree)
    path = settings().results_dir / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{args.name}.json"
    path.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"{len(disagree)} disagreement cases written to {out_dir}; saved {path.name}")


if __name__ == "__main__":
    main()
