"""Markdown tables for docs/RESULTS.md and the README."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from triagebench.data import Case, Label
from triagebench.evals.stats import mcnemar_exact, percentile


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def _ci(m: dict[str, Any]) -> str:
    lo, hi = m["accuracy_ci95"]
    return f"{_pct(m['accuracy'])} [{_pct(lo)}–{_pct(hi)}]"


def main_table(runs: list[dict[str, Any]]) -> str:
    head = (
        "| System | N | Axis A acc [95% CI] | A macro-F1 | Axis B acc [95% CI] | B macro-F1 | "
        "Both right | Evidence valid | Confident-error (B) | Cost / triage | Latency P50 / P95 | "
        "Avg model calls |\n|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [head]
    for r in runs:
        m = r["metrics"]
        a, b = m["axis_A"], m["axis_B"]
        ev = m["evidence_validity"]
        rows.append(
            f"| {r['system']} {r.get('system_desc') or ''} | {m['n']} | {_ci(a)} | "
            f"{a['macro_f1']:.3f} | {_ci(b)} | {b['macro_f1']:.3f} | {_pct(m['both_correct'])} | "
            f"{'—' if ev is None else _pct(ev)} | {_pct(b['confident_error_rate'])} | "
            f"${m['cost_usd_mean']:.4f} | {m['latency_p50_s']:.1f}s / {m['latency_p95_s']:.1f}s | "
            f"{m['steps_mean']:.1f} |"
        )
    return "\n".join(rows)


def paired_table(runs: list[dict[str, Any]]) -> str:
    rows = [
        "| Comparison | Axis | Δ accuracy | Fixed / broken | McNemar p |",
        "|---|---|---|---|---|",
    ]
    for i in range(1, len(runs)):
        base, new = runs[i - 1], runs[i]
        for axis in ("axis_A", "axis_B"):
            pa = base["metrics"][axis]["per_case_correct"]
            pb = new["metrics"][axis]["per_case_correct"]
            common = sorted(set(pa) & set(pb))
            fixed = sum(1 for c in common if pb[c] and not pa[c])
            broken = sum(1 for c in common if pa[c] and not pb[c])
            d = (sum(pb[c] for c in common) - sum(pa[c] for c in common)) / max(1, len(common))
            p = mcnemar_exact(broken, fixed)
            rows.append(
                f"| {new['system']} vs {base['system']} | {axis[-1]} | {d * 100:+.1f} pts | "
                f"{fixed} / {broken} | {p:.3f} |"
            )
    return "\n".join(rows)


def render_report(paths: list[Path], compare: bool = True) -> str:
    runs = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    r0 = runs[0]
    labs = r0["labels"]
    out = [
        f"Split `{r0['split']}`, N = {r0['n_cases']}, dataset `{r0['dataset_hash']}`, "
        f"labels: {labs['verified']} human-verified, {labs['ai_drafted_unverified']} AI-drafted "
        f"and not yet verified. Model `{next((r['model'] for r in runs if r['model']), '—')}` "
        f"(effort {next((r['effort'] for r in runs if r['effort']), '—')}), prompt "
        f"`{r0['prompt_hash']}`, commit `{r0['commit']}`, {r0['date'][:10]}.",
        "",
        main_table(runs),
    ]
    if compare and len(runs) > 1:
        out += ["", paired_table(runs)]
    return "\n".join(out)


def dataset_stats(cases: list[Case], labels: dict[str, Label]) -> str:
    lens = [c.log_lines for c in cases]
    lines = [
        f"cases: {len(cases)} from {len({c.repo for c in cases})} repos; "
        f"log lines P50 {percentile(lens, 0.5):.0f}, P95 {percentile(lens, 0.95):.0f}, "
        f"max {max(lens) if lens else 0}",
        f"splits: {dict(Counter(c.split for c in cases))}",
        f"sources: {dict(Counter(c.source for c in cases))}",
        f"labelled: {sum(c.id in labels for c in cases)} "
        f"(verified {sum(lab.verified for lab in labels.values())})",
        "",
        "| repo | cases |",
        "|---|---|",
    ]
    lines += [f"| {r} | {n} |" for r, n in Counter(c.repo for c in cases).most_common()]
    for axis in ("failure_type", "cause_class"):
        cnt = Counter(getattr(lab, axis) for lab in labels.values())
        lines += ["", f"| {axis} | count |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in cnt.most_common()]
    return "\n".join(lines)
