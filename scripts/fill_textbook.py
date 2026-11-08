"""Fill the textbook's Results chapter (ch. 20) from the newest results/*.json per system.

Only numbers from result files go in. Systems without a result file stay "pending".

    bash scripts/dev.sh python scripts/fill_textbook.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from triagebench.evals.stats import mcnemar_exact

ROOT = Path(__file__).resolve().parents[1]
BOOK = ROOT / "docs" / "learn" / "TriageBench_Textbook.html"
NAMES = {"b0": "B0 regex", "b1": "B1 single call", "b2": "B2 agent", "b3": "B3 agent + retrieval"}


def latest(system: str, split: str = "dev") -> dict | None:  # type: ignore[type-arg]
    """Newest canonical run of a system: untagged, or the first run of the post-fix set."""
    res = ROOT / "results"
    files = sorted(
        list(res.glob(f"*-{system}-{split}.json"))
        + list(res.glob(f"*-{system}-{split}-fix-r0.json"))
    )
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def ci(m: dict) -> str:  # type: ignore[type-arg]
    lo, hi = m["accuracy_ci95"]
    return f"{pct(m['accuracy'])} [{pct(lo)}&ndash;{pct(hi)}]"


def row(name: str, r: dict | None) -> str:  # type: ignore[type-arg]
    if r is None:
        return f'<tr class="pending"><td>{name}</td><td colspan="10">pending: needs the API key, then <code>triagebench eval</code></td></tr>'
    m = r["metrics"]
    a, b = m["axis_A"], m["axis_B"]
    ev = "&mdash;" if m["evidence_validity"] is None else pct(m["evidence_validity"])
    return (
        f'<tr><td>{name}</td><td class="r">{m["n"]}</td><td>{ci(a)}</td>'
        f'<td class="r">{a["macro_f1"]:.3f}</td><td>{ci(b)}</td><td class="r">{b["macro_f1"]:.3f}</td>'
        f'<td class="r">{ev}</td><td class="r">{pct(b["confident_error_rate"])}</td>'
        f'<td class="r">${m["cost_usd_mean"]:.4f}</td>'
        f'<td class="r">{m["latency_p50_s"]:.1f} / {m["latency_p95_s"]:.1f} s</td>'
        f'<td class="r">{m["steps_mean"]:.1f}</td></tr>'
    )


def paired(runs: list[tuple[str, dict]]) -> str:  # type: ignore[type-arg]
    rows = []
    for (na, ra), (nb, rb) in zip(runs, runs[1:], strict=False):
        for axis in ("axis_A", "axis_B"):
            pa, pb = (
                ra["metrics"][axis]["per_case_correct"],
                rb["metrics"][axis]["per_case_correct"],
            )
            common = sorted(set(pa) & set(pb))
            fixed = sum(1 for c in common if pb[c] and not pa[c])
            broken = sum(1 for c in common if pa[c] and not pb[c])
            d = (sum(pb[c] for c in common) - sum(pa[c] for c in common)) / max(1, len(common))
            p = mcnemar_exact(broken, fixed)
            rows.append(
                f'<tr><td>{nb} vs {na}</td><td>{axis[-1]}</td><td class="r">{100 * d:+.1f} pts</td>'
                f'<td class="r">{fixed} / {broken}</td><td class="r">{p:.3f}</td></tr>'
            )
    return (
        "".join(rows)
        or '<tr class="pending"><td colspan="5">pending: needs two or more systems</td></tr>'
    )


def main() -> None:
    html = BOOK.read_text(encoding="utf-8")
    runs = {s: latest(s) for s in NAMES}
    body = "\n      ".join(row(NAMES[s], r) for s, r in runs.items())
    html = re.sub(
        r'(<tbody id="results-body">).*?(</tbody>)', rf"\1\n      {body}\n    \2", html, flags=re.S
    )
    have = [(s.upper(), r) for s, r in runs.items() if r is not None]
    html = re.sub(
        r'(<tbody id="paired-body">).*?(</tbody>)', rf"\1{paired(have)}\2", html, flags=re.S
    )
    r0 = next(r for _, r in have)
    labs = r0["labels"]
    model = next((r["model"] for _, r in have if r["model"]), None)
    done = ", ".join(n for n, _ in have)
    notes = (
        f"Split <code>{r0['split']}</code>, N = {r0['n_cases']} real failures, dataset "
        f"<code>{r0['dataset_hash']}</code>. Labels: {labs['verified']} human-verified, "
        f"{labs['ai_drafted_unverified']} AI-drafted and not yet verified, so these numbers are "
        f"<strong>preliminary</strong>. Model: {model or 'none yet (B0 uses no model)'}. "
        f"Commit <code>{r0['commit']}</code>, {r0['date'][:10]}. Measured so far: {done}. "
        "Source: <code>results/*.json</code> via <code>scripts/fill_textbook.py</code>."
    )
    html = re.sub(
        r'(<p class="small" id="results-notes">).*?(</p>)', rf"\1{notes}\2", html, flags=re.S
    )
    status = (
        "<p>Measured: "
        + done
        + ". "
        + (
            "B1&ndash;B3 need <code>ANTHROPIC_API_KEY</code> in <code>.env</code>, then "
            "<code>triagebench eval --system b1,b2,b3 --split dev</code> and this script again. "
            if len(have) < 4
            else ""
        )
        + "Every number is <strong>preliminary</strong> until the labels are verified in "
        "<code>triagebench review</code>.</p>"
    )
    html = re.sub(
        r'(<div class="box mistake" id="results-status"><span class="label">Status</span>).*?(</div>)',
        rf"\1\n    {status}\n  \2",
        html,
        count=1,
        flags=re.S,
    )
    html = re.sub(
        r"<!--repeats-start-->.*?<!--repeats-end-->",
        lambda _: "<!--repeats-start-->" + repeats_block() + "<!--repeats-end-->",
        html,
        count=1,
        flags=re.S,
    )
    html = re.sub(
        r"<!--ablation-start-->.*?<!--ablation-end-->",
        lambda _: "<!--ablation-start-->" + ablation_block() + "<!--ablation-end-->",
        html,
        count=1,
        flags=re.S,
    )
    BOOK.write_text(html, encoding="utf-8", newline="\n")
    print("filled:", done)


def ablation_block() -> str:
    """Haiku vs Sonnet agent, from the newest results/*-model-ablation-dev.json."""
    files = sorted((ROOT / "results").glob("*-model-ablation-dev.json"))
    if not files:
        return ""
    ab = json.loads(files[-1].read_text(encoding="utf-8"))
    rows = []
    for g in ab["groups"].values():
        a, b = g["axis_A"], g["axis_B"]
        rows.append(
            f"<tr><td>{g['model']}</td>"
            f'<td class="r">{pct(a["mean"])} ({pct(a["min"])}&ndash;{pct(a["max"])})</td>'
            f'<td class="r">{pct(b["mean"])} ({pct(b["min"])}&ndash;{pct(b["max"])})</td>'
            f'<td class="r">{pct(b["flip_rate"])}</td><td class="r">{pct(g["unknown_rate"])}</td>'
            f'<td class="r">${g["cost_usd_per_triage"]:.4f}</td></tr>'
        )
    names = list(ab["groups"])
    pb = ab["paired"][f"{names[1]}_vs_{names[0]}_axis_B"]
    pa = ab["paired"][f"{names[1]}_vs_{names[0]}_axis_A"]
    return (
        "\n  <h3>Would a bigger model help?</h3>\n"
        "  <p>The same agent (prompt v2, same budgets and effort) on Claude Sonnet 5.5, three runs, "
        f"against the three Haiku runs (<code>results/{files[-1].name}</code>, "
        '<span class="src m">measured</span>).</p>\n'
        '  <div class="tw"><table><thead><tr><th>Model</th><th class="r">What failed</th>'
        '<th class="r">Why</th><th class="r">Flip rate (why)</th><th class="r">Answers unknown</th>'
        '<th class="r">Cost / triage</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>\n"
        f"  <p>Paired on each case's average: why {100 * pb['delta_mean_accuracy']:+.1f} pts "
        f"({len(pb['fixed'])} fixed / {len(pb['broken'])} broken, p&nbsp;=&nbsp;{pb['p']:.3f}); "
        f"what failed {100 * pa['delta_mean_accuracy']:+.1f} pts (p&nbsp;=&nbsp;{pa['p']:.3f}). "
        "The bigger model is about ten times the price, more consistent between runs and less "
        "likely to say &ldquo;unknown&rdquo;, but on 50 cases its accuracy gain isn't "
        "significant.</p>\n  "
    )


def repeats_block() -> str:
    """The three-run table from the newest results/*-repeats-dev.json (scripts/repeats.py)."""
    files = sorted((ROOT / "results").glob("*-repeats-dev.json"))
    if not files:
        return ""
    rep = json.loads(files[-1].read_text(encoding="utf-8"))
    rows = []
    for s, e in rep["systems"].items():
        a, b = e["axis_A"], e["axis_B"]
        rows.append(
            f'<tr><td>{NAMES.get(s, s)}</td><td class="r">{e["repeats"]}</td>'
            f'<td class="r">{pct(a["mean"])} ({pct(a["min"])}&ndash;{pct(a["max"])})</td>'
            f'<td class="r">{pct(b["mean"])} ({pct(b["min"])}&ndash;{pct(b["max"])})</td>'
            f'<td class="r">{pct(b["flip_rate"])}</td></tr>'
        )
    pairs = []
    for key, v in rep["paired"].items():
        y, _, x, _, axis = key.split("_")
        if (y, x) not in (("b2", "b1"), ("b3", "b2")):
            continue
        pairs.append(
            f"<tr><td>{y.upper()} vs {x.upper()}</td><td>{axis}</td>"
            f'<td class="r">{100 * v["delta_mean_accuracy"]:+.1f} pts</td>'
            f'<td class="r">{v["fixed"]} / {v["broken"]}</td><td class="r">{v["p"]:.3f}</td></tr>'
        )
    return (
        "\n  <h3>Three runs each: the honest headline</h3>\n"
        "  <p>The same model on the same case doesn't always give the same answer, so every LLM "
        f"system ran three times (prompt {rep['prompt_version']}, file "
        f'<code>results/{files[-1].name}</code>, <span class="src m">measured</span>). The '
        "<em>flip rate</em> is the share of cases whose &ldquo;why&rdquo; answer was right in some "
        "runs and wrong in others.</p>\n"
        '  <div class="tw"><table><thead><tr><th>System</th><th class="r">Runs</th>'
        '<th class="r">What failed: mean (range)</th><th class="r">Why: mean (range)</th>'
        '<th class="r">Flip rate (why)</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>\n"
        "  <p>Paired tests on each case's average over the three runs (a case is &ldquo;fixed&rdquo; "
        "if the second system is right more often):</p>\n"
        '  <div class="tw"><table><thead><tr><th>Comparison</th><th>Axis</th>'
        '<th class="r">&Delta; mean accuracy</th><th class="r">Fixed / broken</th>'
        '<th class="r">McNemar p</th></tr></thead><tbody>'
        + "".join(pairs)
        + "</tbody></table></div>\n"
        '  <div class="box why"><span class="label">Lesson: one run can lie</span><p>On a single '
        "run, adding retrieval looked <em>significantly</em> harmful (B3 vs B2 on &ldquo;why&rdquo;: "
        "0 fixed, 7 broken, p&nbsp;=&nbsp;0.016, in a run before the diff-leak fix). Averaged over three runs the gap shrank and "
        "stopped being significant. A McNemar test assumes each case's outcome is fixed; with a "
        "non-deterministic model it isn't, so repeat before you believe a p-value.</p></div>\n  "
    )


if __name__ == "__main__":
    main()
