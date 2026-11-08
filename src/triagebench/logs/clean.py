"""Turn a raw GitHub Actions job log into clean, numbered lines.

Cleaning only removes the timestamp prefix and ANSI colour codes, so line N of the cleaned log is
line N of the raw log. Evidence line numbers therefore mean the same thing everywhere.
"""

from __future__ import annotations

import re

# "2026-11-01T12:34:56.1234567Z message"  (GitHub prefixes every line with an ISO timestamp)
_TS = re.compile(r"^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z ?")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")


def clean_line(line: str) -> str:
    line = _TS.sub("", line, count=1)
    line = _ANSI.sub("", line)
    return line.rstrip("\r\n").rstrip()


def clean_log(raw: str) -> list[str]:
    """Return cleaned lines (index 0 = line 1)."""
    lines = raw.splitlines()
    return [clean_line(x) for x in lines]


def normalize_for_compare(text: str) -> str:
    """The comparison the evidence verifier uses: ignore surrounding whitespace only."""
    return text.strip()
