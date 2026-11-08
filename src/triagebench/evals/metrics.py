"""Scoring: compare each system's outcomes to the labels.

A case with no valid answer (model error, budget exhausted, unparseable) counts as wrong. Cases
are never silently dropped, so every system is scored on the same denominator.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from triagebench.agent.loop import Outcome
from triagebench.data import Label
from triagebench.evals.stats import bootstrap_ci, percentile, wilson

AXES = {"A": "failure_type", "B": "cause_class"}
CONFIDENT = 0.8


def predicted(o: Outcome, field: str) -> str:
    if o.result is None:
        return "(no answer)"
    return str(getattr(o.result, field).value)


def macro_f1(gold: Sequence[str], pred: Sequence[str]) -> float:
    """F1 per gold class, averaged equally (classes that never occur in gold are ignored)."""
    classes = sorted(set(gold))
    f1s = []
    for c in classes:
        tp = sum(g == c and p == c for g, p in zip(gold, pred, strict=True))
        fp = sum(g != c and p == c for g, p in zip(gold, pred, strict=True))
        fn = sum(g == c and p != c for g, p in zip(gold, pred, strict=True))
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


def confusion(gold: Sequence[str], pred: Sequence[str]) -> dict[str, dict[str, int]]:
    m: dict[str, Counter[str]] = {}
    for g, p in zip(gold, pred, strict=True):
        m.setdefault(g, Counter())[p] += 1
    return {g: dict(c) for g, c in sorted(m.items())}


def axis_metrics(pairs: list[tuple[Outcome, Label]], field: str) -> dict[str, Any]:
    gold = [getattr(lab, field) for _, lab in pairs]
    pred = [predicted(o, field) for o, _ in pairs]
    n = len(pairs)
    correct = [g == p for g, p in zip(gold, pred, strict=True)]
    k = sum(correct)
    lo, hi = wilson(k, n)
    f1_lo, f1_hi = bootstrap_ci(
        list(zip(gold, pred, strict=True)),
        lambda xs: macro_f1([g for g, _ in xs], [p for _, p in xs]),
        n_boot=2000,
    )
    confident_wrong = sum(
        1
        for (o, _), ok in zip(pairs, correct, strict=True)
        if not ok and o.result is not None and o.result.confidence >= CONFIDENT
    )
    return {
        "n": n,
        "correct": k,
        "accuracy": k / n if n else 0.0,
        "accuracy_ci95": [lo, hi],
        "macro_f1": macro_f1(gold, pred),
        "macro_f1_ci95": [f1_lo, f1_hi],
        "confident_error_rate": confident_wrong / n if n else 0.0,
        "confusion": confusion(gold, pred),
        "per_case_correct": {o.case_id: ok for (o, _), ok in zip(pairs, correct, strict=True)},
    }


def score(outcomes: list[Outcome], labels: dict[str, Label]) -> dict[str, Any]:
    pairs = [(o, labels[o.case_id]) for o in outcomes if o.case_id in labels]
    n = len(pairs)
    answered = [o for o, _ in pairs if o.result is not None]
    ev_total = sum(o.evidence_total for o, _ in pairs)
    ev_valid = sum(o.evidence_valid for o, _ in pairs)
    lat = [o.latency_s for o, _ in pairs]
    tools = Counter(t for o, _ in pairs for t in o.tool_calls)
    return {
        "n": n,
        "answered": len(answered),
        "coverage_B": (
            sum(1 for o in answered if o.result and o.result.cause_class.value != "unknown") / n
            if n
            else 0.0
        ),
        "axis_A": axis_metrics(pairs, AXES["A"]),
        "axis_B": axis_metrics(pairs, AXES["B"]),
        "both_correct": (
            sum(
                predicted(o, "failure_type") == lab.failure_type
                and predicted(o, "cause_class") == lab.cause_class
                for o, lab in pairs
            )
            / n
            if n
            else 0.0
        ),
        "evidence_validity": ev_valid / ev_total if ev_total else None,
        "evidence_quotes": ev_total,
        "repaired": sum(o.repaired for o, _ in pairs),
        "errors": sum(1 for o, _ in pairs if o.errors),
        "cost_usd_mean": sum(o.cost_usd for o, _ in pairs) / n if n else 0.0,
        "cost_usd_total": sum(o.cost_usd for o, _ in pairs),
        "input_tokens_mean": (
            sum(o.input_tokens + o.cache_read_tokens + o.cache_write_tokens for o, _ in pairs) / n
            if n
            else 0.0
        ),
        "latency_p50_s": percentile(lat, 0.5),
        "latency_p95_s": percentile(lat, 0.95),
        "steps_mean": sum(o.steps for o, _ in pairs) / n if n else 0.0,
        "tool_mix": dict(tools.most_common()),
    }
