"""Statistics for small test sets: is a difference real, or noise?

- wilson():        95% confidence interval for a proportion (accuracy). Behaves well at small n.
- mcnemar_exact(): paired test for two systems on the same cases, using only the disagreements.
- bootstrap_ci():  interval for any statistic (macro-F1, mean cost) by resampling cases.
- cohen_kappa():   agreement between two labellings, corrected for chance.
"""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable, Sequence


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson score interval for k successes out of n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value.

    b = cases system A got right and B got wrong; c = the reverse. Under "no difference", each
    disagreement is a fair coin flip, so min(b, c) ~ Binomial(b + c, 0.5).
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return float(min(1.0, 2 * tail))


def bootstrap_ci[T](
    items: Sequence[T],
    stat: Callable[[Sequence[T]], float],
    n_boot: int = 10_000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap: resample the cases with replacement, recompute, take quantiles."""
    if not items:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(items)
    vals = sorted(stat([items[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot))
    lo = vals[int(alpha / 2 * n_boot)]
    hi = vals[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return (lo, hi)


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """kappa = (p_o - p_e) / (1 - p_e)."""
    if len(a) != len(b) or not a:
        raise ValueError("need two equal-length, non-empty label lists")
    n = len(a)
    p_o = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    p_e = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if p_e == 1 else (p_o - p_e) / (1 - p_e)


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    idx = (len(v) - 1) * q
    lo, hi = math.floor(idx), math.ceil(idx)
    return v[lo] + (v[hi] - v[lo]) * (idx - lo)
