"""Shrink a huge CI log to the few hundred lines that explain the failure.

LogDx-CI (2026) found that a grep + tail excerpt (~20K tokens) diagnosed better than the raw log
(~275K tokens). Three strategies, all returning numbered lines from the cleaned log:

- tail:   the last lines, up to the budget
- errors: windows around lines that look like errors
- hybrid: step headers + error windows + the tail (the default)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ERROR_PATTERN = re.compile(
    r"(##\[error\]|\berror\b|\berror\[|\bERROR\b|\bFAILED\b|\bFAIL\b|\bfailed\b|Traceback|"
    r"exit code [1-9]|exit status [1-9]|npm ERR!|panicked at|\bfatal\b|Exception\b|"
    r"AssertionError|^E\s{2,}|timed? ?out|\bKilled\b|out of memory|OOM|Segmentation fault|"
    r"cannot find|not found|No such file|ModuleNotFoundError|ImportError|SyntaxError|"
    r"Could not (find|resolve|connect)|Connection (refused|reset)|ResolutionImpossible|"
    r"would reformat|Found \d+ errors?|\d+ (failed|errors?)\b)",
    re.IGNORECASE,
)
# A few error-looking phrases that are almost always noise.
NOISE_PATTERN = re.compile(
    r"(-Werror|error_?handling|errors?=0|0 errors?|0 failed|--fail-fast|continue-on-error|"
    r"if: failure\(\)|::error file=.*::$)",
    re.IGNORECASE,
)
STEP_HEADER = re.compile(r"^##\[group\]Run ")
MAX_LINE_CHARS = 400
TRUNC_MARK = " …[truncated]"


def est_tokens(text: str) -> int:
    """Rough token estimate (4 characters per token). Labelled an estimate wherever it's shown."""
    return max(1, len(text) // 4)


@dataclass
class Excerpt:
    lines: list[tuple[int, str]]  # (1-based line number, text)
    total_lines: int
    strategy: str
    notes: list[str] = field(default_factory=list)

    def render(self) -> str:
        out: list[str] = []
        prev = 0
        for n, text in self.lines:
            if n > prev + 1:
                out.append(f"      … ({n - prev - 1} lines omitted) …")
            if len(text) > MAX_LINE_CHARS:
                text = text[:MAX_LINE_CHARS] + TRUNC_MARK
            out.append(f"{n:>6}| {text}")
            prev = n
        if prev < self.total_lines:
            out.append(f"      … ({self.total_lines - prev} lines omitted) …")
        return "\n".join(out)

    @property
    def kept(self) -> int:
        return len(self.lines)

    def header(self) -> str:
        tok = est_tokens(self.render())
        extra = f"; {'; '.join(self.notes)}" if self.notes else ""
        return (
            f"[excerpt strategy={self.strategy}: kept {self.kept} of {self.total_lines} lines; "
            f"~{tok} tokens (estimate){extra}]"
        )


def _line_cost(text: str) -> int:
    return est_tokens(text[:MAX_LINE_CHARS]) + 3  # +3 for the line-number gutter


def is_error_line(text: str) -> bool:
    return bool(ERROR_PATTERN.search(text)) and not NOISE_PATTERN.search(text)


def _collapse_repeats(idx: list[int], lines: list[str]) -> tuple[list[int], int]:
    """Drop consecutive identical lines (keeps the first, and always the log's last line).
    Returns (kept indices, dropped count)."""
    kept: list[int] = []
    dropped = 0
    last = len(lines) - 1
    for i in idx:
        repeat = kept and kept[-1] == i - 1 and lines[i] == lines[i - 1] and lines[i].strip()
        if repeat and i != last:
            dropped += 1
            continue
        kept.append(i)
    return kept, dropped


def _windows(lines: list[str], before: int, after: int) -> list[tuple[int, int]]:
    """Merged [start, end) windows around error lines."""
    wins: list[tuple[int, int]] = []
    for i, text in enumerate(lines):
        if is_error_line(text):
            s, e = max(0, i - before), min(len(lines), i + after + 1)
            if wins and s <= wins[-1][1]:
                wins[-1] = (wins[-1][0], max(wins[-1][1], e))
            else:
                wins.append((s, e))
    return wins


def reduce_log(lines: list[str], strategy: str = "hybrid", max_tokens: int = 4000) -> Excerpt:
    n = len(lines)
    if strategy not in {"tail", "errors", "hybrid"}:
        raise ValueError(f"unknown strategy {strategy!r}; use tail, errors or hybrid")
    budget = max_tokens
    chosen: set[int] = set()
    notes: list[str] = []

    def take(indices: range | list[int]) -> bool:
        """Add indices while the budget allows; return False once it's exhausted."""
        nonlocal budget
        for i in indices:
            if i in chosen:
                continue
            c = _line_cost(lines[i])
            if c > budget:
                return False
            chosen.add(i)
            budget -= c
        return True

    if strategy == "tail":
        start = n
        while start > 0 and _line_cost(lines[start - 1]) <= budget:
            start -= 1
            budget -= _line_cost(lines[start])
        chosen.update(range(start, n))
    else:
        # The tail always matters: the final error and the exit code are there.
        tail_budget = max_tokens // 4 if strategy == "hybrid" else max_tokens // 10
        start, spent = n, 0
        while start > 0 and spent + _line_cost(lines[start - 1]) <= tail_budget:
            start -= 1
            spent += _line_cost(lines[start])
        take(range(start, n))
        if strategy == "hybrid":
            headers = [i for i, t in enumerate(lines) if STEP_HEADER.match(t)]
            take(headers[-30:])  # step names give structure ("Run pytest", "Run ruff check")
        wins = _windows(lines, before=3, after=2)
        # Take windows alternately from the front (the first error is often the cause) and the back
        # (the last error is closest to the exit), until the budget runs out.
        order: list[tuple[int, int]] = []
        lo, hi = 0, len(wins) - 1
        while lo <= hi:
            order.append(wins[lo])
            if lo != hi:
                order.append(wins[hi])
            lo, hi = lo + 1, hi - 1
        skipped = 0
        for s, e in order:
            if not take(range(s, e)):
                skipped += 1
        if skipped:
            notes.append(f"{skipped} of {len(wins)} error regions didn't fit; use search_log")
    idx, dropped = _collapse_repeats(sorted(chosen), lines)
    if dropped:
        notes.append(f"collapsed {dropped} repeated lines")
    return Excerpt([(i + 1, lines[i]) for i in idx], n, strategy, notes)
