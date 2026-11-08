"""B0: hand-written regex rules, no model. The floor every LLM system has to beat.

failure_type: the first rule (in priority order) whose pattern matches an error-looking line.
cause_class:  a fixed mapping from failure_type, with network errors -> environment.
"""

from __future__ import annotations

import re
import time

from triagebench.agent.loop import Outcome
from triagebench.agent.schema import CauseClass, Evidence, FailureType, TriageResult
from triagebench.data import Case
from triagebench.logs.reduce import is_error_line
from triagebench.tools.impl import Toolbox

F = FailureType
RULES: list[tuple[FailureType, re.Pattern[str]]] = [
    (
        F.TIMEOUT_OR_OOM,
        re.compile(
            r"exceeded the maximum execution time|has timed out|timed out after|Timeout of \d+|"
            r"exit code 137|\bKilled\b|out of memory|MemoryError|The operation was canceled",
            re.I,
        ),
    ),
    (
        F.CI_INFRA_OR_CONFIG,
        re.compile(
            r"Invalid workflow file|Resource not accessible by integration|runner has received a "
            r"shutdown|lost communication with the server|Unable to resolve action|"
            r"secret.*(not found|not set|is empty)|codecov|upload-artifact|API rate limit exceeded",
            re.I,
        ),
    ),
    (
        F.DOCKER_BUILD,
        re.compile(
            r"ERROR: failed to (solve|build)|failed to compute cache key|"
            r"docker(/| )build",
            re.I,
        ),
    ),
    (
        F.DEPENDENCY_INSTALL,
        re.compile(
            r"ResolutionImpossible|No matching distribution found|Could not find a version that "
            r"satisfies|npm ERR! code E|ERESOLVE|ERR_PNPM|failed to select a version|"
            r"Unable to locate package|No solution found when resolving|because .* depends on|"
            r"error: failed to download|failed to fetch",
            re.I,
        ),
    ),
    (
        F.TYPE_ERROR,
        re.compile(
            r"error TS\d+:|Found \d+ errors? in \d+ files? \(checked|"
            r"\berror: .*\[[a-z-]+\]\s*$|pyright|mypy",
            re.I,
        ),
    ),
    (
        F.LINT_FAILURE,
        re.compile(
            r"would reformat|would be reformatted|\bruff\b|flake8|eslint|prettier|clippy|codespell|"
            r"pre-commit|Code style issues|lint|isort|black --check|gofmt|golangci",
            re.I,
        ),
    ),
    (
        F.COMPILE_ERROR,
        re.compile(
            r"error\[E\d{4}\]|could not compile|cannot find symbol|compilation failed|"
            r"\.go:\d+:\d+:|undefined reference|fatal error: .*\.h|BUILD FAILED|"
            r"Compilation failure|error CS\d+",
            re.I,
        ),
    ),
    (
        F.TEST_ASSERTION,
        re.compile(
            r"AssertionError|^FAILED |\d+ failed|--- FAIL:|test result: FAILED|Tests? failed|"
            r"FAILURES|^E\s{2,}|AssertionFailedError|expected .* (got|received)",
            re.I,
        ),
    ),
]
NETWORK = re.compile(
    r"Connection (reset|refused|aborted)|Could not resolve host|Temporary failure in name "
    r"resolution|503 Service|502 Bad Gateway|ETIMEDOUT|ECONNRESET|rate limit",
    re.I,
)
DEFAULT_CAUSE = {
    F.TIMEOUT_OR_OOM: CauseClass.FLAKY,
    F.CI_INFRA_OR_CONFIG: CauseClass.CI_CONFIG,
    F.DOCKER_BUILD: CauseClass.REGRESSION,
    F.DEPENDENCY_INSTALL: CauseClass.ENVIRONMENT,
    F.TYPE_ERROR: CauseClass.REGRESSION,
    F.LINT_FAILURE: CauseClass.REGRESSION,
    F.COMPILE_ERROR: CauseClass.REGRESSION,
    F.TEST_ASSERTION: CauseClass.REGRESSION,
}


def classify(lines: list[str]) -> tuple[FailureType, CauseClass, int] | None:
    """(failure_type, cause_class, 0-based evidence line) or None if no rule fires."""
    errs = [i for i, t in enumerate(lines) if is_error_line(t)]
    for ftype, rx in RULES:
        for i in errs:
            if rx.search(lines[i]):
                cause = DEFAULT_CAUSE[ftype]
                if any(NETWORK.search(lines[j]) for j in errs):
                    cause = CauseClass.ENVIRONMENT
                return ftype, cause, i
    return None


def run_regex(case: Case, box: Toolbox) -> Outcome:
    t0 = time.perf_counter()
    lines = box.store.lines(case.job_id)
    out = Outcome(case_id=case.id, system="b0")
    hit = classify(lines)
    if hit is None:
        # No rule fired: fall back to the last error-looking line (or the last line).
        errs = [i for i, t in enumerate(lines) if is_error_line(t) and t.strip()]
        i = errs[-1] if errs else len(lines) - 1
        ftype, cause = FailureType.TEST_ASSERTION, CauseClass.UNKNOWN
    else:
        ftype, cause, i = hit
    out.result = TriageResult(
        failure_type=ftype,
        cause_class=cause,
        root_cause=f"Rule match on line {i + 1}.",
        evidence=[Evidence(job_id=case.job_id, line=i + 1, text=lines[i].strip() or "(blank)")],
        next_step="Read the quoted line.",
        confidence=0.5,
    )
    out.evidence_total = out.evidence_valid = 1
    out.latency_s = round(time.perf_counter() - t0, 4)
    return out
