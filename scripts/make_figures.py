"""Build the README's charts as hand-written SVG, one light and one dark file per chart, straight
from results/*.json and data/cases.jsonl, and print every number they show (for the README text).

    bash scripts/dev.sh python scripts/make_figures.py

Charts follow the repo's data-viz rules: thin bars (<= 24 px) with 4 px rounded data-ends and
square baselines, a 2 px gap between touching bars, hairline recessive grids, text in text
colours (never the series colour), a legend whenever there are two series, selective direct
labels, and a validated two-colour palette (blue = "what failed", orange = "whose problem").
"""

from __future__ import annotations

import json
import math
from html import escape
from pathlib import Path
from statistics import median
from typing import Any

from triagebench.data import load_cases
from triagebench.evals.stats import percentile

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
OUT = ROOT / "docs" / "img"

THEMES = {
    "light": {
        "text": "#1f2328",
        "text2": "#59636e",
        "grid": "#d1d9e0",
        "axis": "#818b98",
        "s1": "#2a78d6",
        "s2": "#eb6834",
        "ring": "#ffffff",
    },
    "dark": {
        "text": "#f0f6fc",
        "text2": "#9198a1",
        "grid": "#3d444d",
        "axis": "#656c76",
        "s1": "#3987e5",
        "s2": "#d95926",
        "ring": "#0d1117",
    },
}
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def load(name: str) -> dict[str, Any]:
    return json.loads((RES / name).read_text(encoding="utf-8"))


def newest(pattern: str) -> dict[str, Any]:
    return json.loads(sorted(RES.glob(pattern))[-1].read_text(encoding="utf-8"))


# ---- the numbers -------------------------------------------------------------------------------
def facts() -> dict[str, Any]:
    b0 = newest("*-b0-dev.json")
    rep = newest("*-repeats-dev.json")  # Haiku B1-B3, 3 runs each (post-fix set)
    abl = newest("*-model-ablation-dev.json")  # Haiku B2 vs Sonnet B2, 3 runs each
    systems: dict[str, dict[str, Any]] = {
        "B0": {
            "label": "Regex rules (B0)",
            "A": (b0["metrics"]["axis_A"]["accuracy"],) * 3,
            "B": (b0["metrics"]["axis_B"]["accuracy"],) * 3,
            "flip_B": None,
            "cost": 0.0,
        }
    }
    names = {"b1": "One AI call (B1)", "b2": "Agent (B2)", "b3": "Agent + memory (B3)"}
    for key, label in names.items():
        e = rep["systems"][key]
        systems[key.upper()] = {
            "label": label,
            "A": (e["axis_A"]["mean"], e["axis_A"]["min"], e["axis_A"]["max"]),
            "B": (e["axis_B"]["mean"], e["axis_B"]["min"], e["axis_B"]["max"]),
            "flip_B": e["axis_B"]["flip_rate"],
            "cost": e["cost_usd_all_repeats"] / (e["repeats"] * 50),
        }
    g = abl["groups"]["sonnet"]
    systems["SONNET"] = {
        "label": "Agent on Sonnet 5.5",
        "A": (g["axis_A"]["mean"], g["axis_A"]["min"], g["axis_A"]["max"]),
        "B": (g["axis_B"]["mean"], g["axis_B"]["min"], g["axis_B"]["max"]),
        "flip_B": g["axis_B"]["flip_rate"],
        "cost": g["cost_usd_per_triage"],
    }
    cases = load_cases()
    lens = [c.log_lines for c in cases]
    return {
        "systems": systems,
        "paired": {**rep["paired"], **{f"ablation_{k}": v for k, v in abl["paired"].items()}},
        "logs": {
            "n": len(lens),
            "median": median(lens),
            "p95": percentile(lens, 0.95),
            "max": max(lens),
            "values": lens,
        },
        "files": {"b0": b0["run_id"], "repeats": rep["date"], "ablation": abl["date"]},
    }


# ---- SVG helpers -------------------------------------------------------------------------------
def svg_open(w: int, h: int, title: str, desc: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-labelledby="t d" font-family="{FONT}">',
        f'<title id="t">{escape(title)}</title><desc id="d">{escape(desc)}</desc>',
    ]


def text(
    x: float, y: float, s: str, fill: str, size: int = 12, anchor: str = "start", weight: int = 400
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" '
        f'text-anchor="{anchor}" font-weight="{weight}" dominant-baseline="middle">{escape(s)}</text>'
    )


def hbar(x0: float, x1: float, y: float, h: float, fill: str) -> str:
    """Horizontal bar: square at the baseline (x0), 4 px rounded data-end (x1)."""
    r = min(4.0, (x1 - x0) / 2, h / 2)
    if x1 - x0 < 0.5:
        return ""
    return (
        f'<path d="M{x0:.1f},{y:.1f} H{x1 - r:.1f} A{r},{r} 0 0 1 {x1:.1f},{y + r:.1f} '
        f'V{y + h - r:.1f} A{r},{r} 0 0 1 {x1 - r:.1f},{y + h:.1f} H{x0:.1f} Z" fill="{fill}"/>'
    )


def vbar(x: float, w: float, y_top: float, y_base: float, fill: str) -> str:
    """Column: square at the baseline, 4 px rounded top."""
    r = min(4.0, w / 2, (y_base - y_top) / 2)
    if y_base - y_top < 0.5:
        return ""
    return (
        f'<path d="M{x:.1f},{y_base:.1f} V{y_top + r:.1f} A{r},{r} 0 0 1 {x + r:.1f},{y_top:.1f} '
        f'H{x + w - r:.1f} A{r},{r} 0 0 1 {x + w:.1f},{y_top + r:.1f} V{y_base:.1f} Z" '
        f'fill="{fill}"/>'
    )


def line(x1: float, y1: float, x2: float, y2: float, stroke: str, w: float = 1.0) -> str:
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" '
        f'stroke-width="{w}" stroke-linecap="round"/>'
    )


def swatch(x: float, y: float, fill: str) -> str:
    return f'<rect x="{x:.1f}" y="{y - 5:.1f}" width="10" height="10" rx="2" fill="{fill}"/>'


def pct(v: float, decimals: int = 1) -> str:
    return f"{100 * v:.{decimals}f}%"


def write(name: str, theme: str, parts: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}-{theme}.svg").write_text("\n".join(parts + ["</svg>"]) + "\n", encoding="utf-8")


# ---- chart 1: accuracy (hero) -------------------------------------------------------------------
def accuracy_chart(f: dict[str, Any], theme: str) -> None:
    c = THEMES[theme]
    order = ["B0", "B1", "B2", "B3", "SONNET"]
    W, left, right, top = 780, 190, 700, 92
    row_h, bar_h, gap = 46, 14, 2
    H = top + row_h * len(order) + 44
    x = lambda v: left + v * (right - left)  # noqa: E731
    desc = "; ".join(
        f"{f['systems'][k]['label']}: what failed {pct(f['systems'][k]['A'][0])}, "
        f"whose problem {pct(f['systems'][k]['B'][0])}"
        for k in order
    )
    p = svg_open(W, H, "Accuracy on 50 real CI failures", desc)
    p.append(text(16, 22, "Accuracy on 50 real CI failures", c["text"], 15, weight=600))
    p.append(
        text(
            16,
            42,
            "Mean of 3 runs. Whiskers show run-to-run spread, not statistical uncertainty: "
            "with 50 cases, gaps under ~10 points can be noise.",
            c["text2"],
            11,
        )
    )
    # legend: two series, so identity never relies on colour alone
    p += [
        swatch(16, 66, c["s1"]),
        text(32, 66, "What failed", c["text"], 12),
        swatch(126, 66, c["s2"]),
        text(142, 66, "Whose problem (why)", c["text"], 12),
    ]
    # recessive hairline grid with percentage ticks
    plot_bottom = top + row_h * len(order)
    for t in (0, 0.25, 0.5, 0.75, 1.0):
        p.append(line(x(t), top - 6, x(t), plot_bottom, c["grid"]))
        p.append(text(x(t), plot_bottom + 14, f"{int(t * 100)}%", c["text2"], 11, "middle"))
    for i, k in enumerate(order):
        s = f["systems"][k]
        y0 = top + i * row_h + (row_h - (2 * bar_h + gap)) / 2
        p.append(
            text(
                left - 12,
                y0 + bar_h + gap / 2,
                s["label"],
                c["text"],
                12,
                "end",
                weight=600 if k == "B2" else 400,
            )
        )
        for j, (ax, col) in enumerate((("A", c["s1"]), ("B", c["s2"]))):
            mean_v, lo, hi = s[ax]
            yb = y0 + j * (bar_h + gap)
            p.append(hbar(x(0), x(mean_v), yb, bar_h, col))
            end = x(mean_v)
            if hi > lo:  # whisker: lowest to highest run
                yc = yb + bar_h / 2
                p.append(line(x(lo), yc, x(hi), yc, c["text"], 1.5))
                p.append(line(x(lo), yc - 4, x(lo), yc + 4, c["text"], 1.5))
                p.append(line(x(hi), yc - 4, x(hi), yc + 4, c["text"], 1.5))
                end = max(end, x(hi))
            p.append(text(end + 6, yb + bar_h / 2, pct(mean_v), c["text"], 11))
    write("accuracy", theme, p)


# ---- chart 2: cost vs accuracy --------------------------------------------------------------------
def cost_chart(f: dict[str, Any], theme: str) -> None:
    c = THEMES[theme]
    W, H = 780, 340
    left, right, top, bottom = 70, 740, 70, 290
    lx0, lx1 = math.log10(0.0005), math.log10(0.05)
    y0, y1 = 0.60, 0.95
    X = lambda v: left + (math.log10(v) - lx0) / (lx1 - lx0) * (right - left)  # noqa: E731
    Y = lambda v: bottom - (v - y0) / (y1 - y0) * (bottom - top)  # noqa: E731
    pts = [
        ("B1", "One AI call", "right"),
        ("B2", "Agent", "left"),
        ("B3", "Agent + memory", "right"),
        ("SONNET", "Agent on Sonnet 5.5", "left"),
    ]
    desc = "; ".join(
        f"{lab}: {pct(f['systems'][k]['B'][0])} at ${f['systems'][k]['cost']:.4f} per triage"
        for k, lab, _ in pts
    )
    p = svg_open(W, H, "Cost per triage vs accuracy on whose problem", desc)
    p.append(text(16, 22, "What does a better answer cost?", c["text"], 15, weight=600))
    p.append(
        text(
            16,
            42,
            "Accuracy on “whose problem” vs cost per triage (log scale). Mean of "
            "3 runs; whiskers = lowest and highest run.",
            c["text2"],
            11,
        )
    )
    for t in (0.6, 0.7, 0.8, 0.9):
        p.append(line(left, Y(t), right, Y(t), c["grid"]))
        p.append(text(left - 8, Y(t), f"{int(t * 100)}%", c["text2"], 11, "end"))
    for t, lab in (
        (0.001, "$0.001"),
        (0.002, "$0.002"),
        (0.005, "$0.005"),
        (0.01, "$0.01"),
        (0.02, "$0.02"),
        (0.05, "$0.05"),
    ):
        p.append(line(X(t), bottom, X(t), bottom + 4, c["axis"]))
        p.append(text(X(t), bottom + 16, lab, c["text2"], 11, "middle"))
    p.append(line(left, bottom, right, bottom, c["axis"]))
    for k, lab, side in pts:
        s = f["systems"][k]
        cx, (m, lo, hi) = X(s["cost"]), s["B"]
        p.append(line(cx, Y(lo), cx, Y(hi), c["text"], 1.5))
        p.append(
            f'<circle cx="{cx:.1f}" cy="{Y(m):.1f}" r="6" fill="{c["s2"]}" '
            f'stroke="{c["ring"]}" stroke-width="2"/>'
        )
        dx, anchor = (12, "start") if side == "right" else (-12, "end")
        p.append(text(cx + dx, Y(m), f"{lab} · {pct(m)}", c["text"], 12, anchor))
    b0 = f["systems"]["B0"]["B"][0]
    p.append(
        text(
            16,
            H - 14,
            f"Regex rules (B0) score {pct(b0)} at $0 and sit off this chart. "
            "Costs include prompt caching.",
            c["text2"],
            11,
        )
    )
    write("cost", theme, p)


# ---- chart 3: consistency ----------------------------------------------------------------------
def flip_chart(f: dict[str, Any], theme: str) -> None:
    c = THEMES[theme]
    order = ["B1", "B2", "B3", "SONNET"]
    W, left, right, top, row_h, bar_h = 780, 190, 700, 64, 34, 16
    H = top + row_h * len(order) + 40
    xmax = 0.30
    x = lambda v: left + v / xmax * (right - left)  # noqa: E731
    desc = "; ".join(f"{f['systems'][k]['label']}: {pct(f['systems'][k]['flip_B'])}" for k in order)
    p = svg_open(W, H, "How often the answer flips between identical runs", desc)
    p.append(text(16, 22, "Same question, same data, different answer?", c["text"], 15, weight=600))
    p.append(
        text(
            16,
            42,
            "Share of the 50 cases where the “whose problem” answer was right "
            "in some of the 3 identical runs and wrong in others.",
            c["text2"],
            11,
        )
    )
    bottom = top + row_h * len(order)
    for t in (0, 0.1, 0.2, 0.3):
        p.append(line(x(t), top - 6, x(t), bottom, c["grid"]))
        p.append(text(x(t), bottom + 14, f"{int(t * 100)}%", c["text2"], 11, "middle"))
    for i, k in enumerate(order):
        s = f["systems"][k]
        yb = top + i * row_h + (row_h - bar_h) / 2
        p.append(text(left - 12, yb + bar_h / 2, s["label"], c["text"], 12, "end"))
        p.append(hbar(x(0), x(s["flip_B"]), yb, bar_h, c["s1"]))
        p.append(text(x(s["flip_B"]) + 6, yb + bar_h / 2, pct(s["flip_B"], 0), c["text"], 11))
    write("consistency", theme, p)


# ---- chart 4: log sizes ------------------------------------------------------------------------
def logs_chart(f: dict[str, Any], theme: str) -> None:
    c = THEMES[theme]
    edges = [0, 100, 300, 1_000, 3_000, 10_000, 30_000, 100_000, 10**9]
    labels = ["<100", "100–300", "300–1K", "1K–3K", "3K–10K", "10K–30K", "30K–100K", "≥100K"]
    vals = f["logs"]["values"]
    counts = [
        sum(1 for v in vals if lo <= v < hi) for lo, hi in zip(edges, edges[1:], strict=False)
    ]
    W, H, left, right, top, bottom = 780, 320, 60, 760, 78, 260
    ymax = max(5, math.ceil(max(counts) / 5) * 5)
    Y = lambda v: bottom - v / ymax * (bottom - top)  # noqa: E731
    slot = (right - left) / len(counts)
    bw = min(24.0, slot * 0.5)
    desc = ", ".join(f"{lab} lines: {n} logs" for lab, n in zip(labels, counts, strict=True))
    p = svg_open(W, H, "How long real CI logs are", desc)
    p.append(text(16, 22, "Real CI logs are long", c["text"], 15, weight=600))
    p.append(
        text(
            16,
            42,
            f"Lines per log across the {f['logs']['n']} collected failures. Median "
            f"{int(f['logs']['median']):,}; 5% are longer than {round(f['logs']['p95']):,}; "
            f"the longest has {f['logs']['max']:,}.",
            c["text2"],
            11,
        )
    )
    step = 5 if ymax <= 30 else 10
    for t in range(0, ymax + 1, step):
        p.append(line(left, Y(t), right, Y(t), c["grid"]))
        p.append(text(left - 8, Y(t), str(t), c["text2"], 11, "end"))
    p.append(text(left - 8, top - 16, "logs", c["text2"], 11, "end"))
    for i, (n, lab) in enumerate(zip(counts, labels, strict=True)):
        cx = left + slot * i + slot / 2
        p.append(vbar(cx - bw / 2, bw, Y(n), bottom, c["s1"]))
        p.append(text(cx, bottom + 16, lab, c["text2"], 11, "middle"))
    p.append(text((left + right) / 2, H - 12, "lines in the log", c["text2"], 11, "middle"))
    write("logs", theme, p)


def main() -> None:
    f = facts()
    for theme in THEMES:
        accuracy_chart(f, theme)
        cost_chart(f, theme)
        flip_chart(f, theme)
        logs_chart(f, theme)
    print("charts written to", OUT)
    for k, s in f["systems"].items():
        a, b = s["A"], s["B"]
        flip = "-" if s["flip_B"] is None else pct(s["flip_B"])
        print(
            f"{k:<7} A {pct(a[0])} ({pct(a[1])}-{pct(a[2])})  B {pct(b[0])} "
            f"({pct(b[1])}-{pct(b[2])})  flipB {flip}  cost/triage ${s['cost']:.4f}"
        )
    lg = f["logs"]
    print(f"logs: n={lg['n']} median={lg['median']} p95={lg['p95']:.0f} max={lg['max']}")
    for k, v in f["paired"].items():
        fx = v["fixed"] if isinstance(v["fixed"], int) else len(v["fixed"])
        br = v["broken"] if isinstance(v["broken"], int) else len(v["broken"])
        print(f"paired {k}: {100 * v['delta_mean_accuracy']:+.1f} pts, {fx}/{br}, p={v['p']:.3f}")


if __name__ == "__main__":
    main()
