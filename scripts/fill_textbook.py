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
    files = sorted((ROOT / "results").glob(f"*-{system}-{split}.json"))
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
    BOOK.write_text(html, encoding="utf-8", newline="\n")
    print("filled:", done)


if __name__ == "__main__":
    main()
