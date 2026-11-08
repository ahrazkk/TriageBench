"""Deterministic checks on a model's answer: parse, validate, and verify every evidence quote.

A quote is valid when it appears verbatim on the line it names: either the whole line, or a
substring of at least MIN_QUOTE characters (long lines are truncated in excerpts, so the model may
only have seen the start). Anything else is a hallucinated or misplaced quote.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import ValidationError

from triagebench.agent.schema import Evidence, TriageResult
from triagebench.logs.reduce import TRUNC_MARK

MIN_QUOTE = 15
LinesFor = Callable[[int], list[str]]


def quote_ok(quote: str, line_text: str) -> bool:
    q = quote.replace(TRUNC_MARK, "").strip()
    line = line_text.strip()
    if not q:
        return False
    if q == line:
        return True
    return len(q) >= MIN_QUOTE and q in line


def check_evidence(ev: Evidence, lines_for: LinesFor) -> str | None:
    """None if valid, else a message the model can act on."""
    try:
        lines = lines_for(ev.job_id)
    except (KeyError, FileNotFoundError):
        return f"evidence cites job {ev.job_id}, which isn't a job in this case"
    if not 1 <= ev.line <= len(lines):
        return f"evidence line {ev.line} is outside the log (1-{len(lines)})"
    if not quote_ok(ev.text, lines[ev.line - 1]):
        # Help the repair: is the quote on a nearby line?
        near = [
            n
            for n in range(max(1, ev.line - 5), min(len(lines), ev.line + 5) + 1)
            if quote_ok(ev.text, lines[n - 1])
        ]
        hint = f" (it does appear on line {near[0]})" if near else ""
        return (
            f"evidence line {ev.line} doesn't contain the quoted text{hint}; "
            f"line {ev.line} is: {lines[ev.line - 1][:200]!r}"
        )
    return None


@dataclass
class Checked:
    result: TriageResult | None
    errors: list[str] = field(default_factory=list)
    evidence_total: int = 0
    evidence_valid: int = 0

    @property
    def ok(self) -> bool:
        return self.result is not None and not self.errors


def _extract_json(text: str) -> str:
    """Models sometimes wrap JSON in ``` fences or add a sentence; take the outermost object."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


def parse_and_verify(text: str, lines_for: LinesFor) -> Checked:
    try:
        data = json.loads(_extract_json(text))
        result = TriageResult.model_validate(data)
    except json.JSONDecodeError as e:
        return Checked(None, [f"the answer isn't valid JSON: {e}"])
    except ValidationError as e:
        msgs = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        return Checked(None, ["schema: " + "; ".join(msgs[:5])])
    errors = [m for ev in result.evidence if (m := check_evidence(ev, lines_for))]
    total = len(result.evidence)
    return Checked(result, errors, total, total - len(errors))
