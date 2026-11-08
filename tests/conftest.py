"""Shared fixtures: a tiny synthetic dataset on disk, so no test needs the network or real data."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from triagebench.data import Case, CaseStore, Label, write_snapshot
from triagebench.tools.impl import Toolbox

TS = "2026-11-01T12:00:00.0000000Z "


def make_log(body: list[str], noise: int = 200) -> str:
    lines = ["##[group]Run actions/checkout@v4"] + [f"setup line {i}" for i in range(noise)]
    lines += ["##[endgroup]", "##[group]Run pytest -q"] + body
    lines += ["##[error]Process completed with exit code 1."]
    return "\n".join(TS + x for x in lines) + "\n"


PYTEST_BODY = [
    "============================= test session starts ==============================",
    "tests/test_dates.py::test_parse_dates FAILED",
    "=================================== FAILURES ===================================",
    "    def test_parse_dates():",
    ">       assert parse('2026-01-02') == date(2026, 1, 3)",
    "E       AssertionError: assert datetime.date(2026, 1, 2) == datetime.date(2026, 1, 3)",
    "FAILED tests/test_dates.py::test_parse_dates - AssertionError: assert datetime.date",
    "========================= 1 failed, 41 passed in 3.21s =========================",
]
PIP_BODY = [
    "Collecting numpy==9.9.9",
    "ERROR: Could not find a version that satisfies the requirement numpy==9.9.9",
    "ERROR: No matching distribution found for numpy==9.9.9",
]
RUFF_BODY = [
    "src/app.py:12:5: F841 Local variable `x` is assigned to but never used",
    "Found 1 error.",
]


def _case(job_id: int, run_id: int, name: str, split: str, created: str, log: str) -> Case:
    n = len(log.splitlines())
    return Case(
        id=f"acme/widgets/jobs/{job_id}",
        repo="acme/widgets",
        run_id=run_id,
        run_attempt=1,
        job_id=job_id,
        job_name=name,
        workflow="CI",
        workflow_id=7,
        event="pull_request",
        head_sha="abc1234567890",
        head_branch="feature",
        pr_number=42,
        created_at=datetime.fromisoformat(created).replace(tzinfo=UTC),
        html_url=f"https://github.com/acme/widgets/actions/runs/{run_id}/job/{job_id}",
        failed_steps=["Run pytest -q"],
        log_lines=n,
        source="fixture",
        split=split,  # type: ignore[arg-type]
        fetched_at=datetime(2026, 11, 7, tzinfo=UTC),
    )


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[list[Case], dict[str, Label], Path]:
    data_dir = tmp_path / "raw"
    specs = [
        (101, 11, "test (3.12)", "kb", "2026-10-01", make_log(PYTEST_BODY)),
        (102, 12, "deps", "kb", "2026-10-02", make_log(PIP_BODY, noise=20)),
        (103, 13, "lint", "kb", "2026-10-03", make_log(RUFF_BODY, noise=5)),
        (201, 21, "test (3.13)", "dev", "2026-10-20", make_log(PYTEST_BODY, noise=50)),
        (202, 13, "lint (again)", "dev", "2026-10-21", make_log(RUFF_BODY, noise=5)),
    ]
    cases, labels = [], {}
    kinds = {
        "test": ("test_assertion", "regression"),
        "deps": ("dependency_install", "environment"),
        "lint": ("lint_failure", "regression"),
    }
    for job, run, name, split, created, log in specs:
        c = _case(job, run, name, split, created, log)
        cases.append(c)
        hist = {
            "default_branch": "main",
            "runs": [
                {
                    "run_id": 1,
                    "branch": "main",
                    "event": "push",
                    "sha": "0123456789",
                    "conclusion": "success",
                    "run_attempt": 1,
                    "created_at": "2026-09-30T00:00:00Z",
                },
            ],
        }
        changes = {
            "kind": "pull_request",
            "message": "Fix date parsing",
            "total_files": 1,
            "files": [
                {
                    "filename": "src/dates.py",
                    "status": "modified",
                    "additions": 3,
                    "deletions": 1,
                    "patch": "@@ -1 +1 @@\n-old\n+new",
                }
            ],
        }
        overview = {
            "run_name": "CI",
            "run_attempt": 1,
            "jobs": [{"name": name, "conclusion": "failure"}],
        }
        write_snapshot(
            data_dir, job, log, {"history": hist, "changes": changes, "overview": overview}
        )
        ftype, cause = kinds[name.split()[0]]
        labels[c.id] = Label(
            case_id=c.id,
            failure_type=ftype,
            cause_class=cause,
            root_cause=f"{ftype} in {name}",
            labeller="test",
            created_at=c.created_at,
        )
    return cases, labels, data_dir


@pytest.fixture
def box(dataset: tuple[list[Case], dict[str, Label], Path]) -> Toolbox:
    cases, _, data_dir = dataset
    return Toolbox(CaseStore(cases, data_dir), allow_live=False)


def answer_json(job_id: int, line: int, text: str, **over: object) -> str:
    d: dict[str, object] = {
        "failure_type": "test_assertion",
        "cause_class": "regression",
        "root_cause": "test_parse_dates fails: parse() returns the wrong day.",
        "evidence": [{"job_id": job_id, "line": line, "text": text}],
        "similar_cases": [],
        "next_step": "Fix parse() in src/dates.py.",
        "confidence": 0.9,
    }
    d.update(over)
    return json.dumps(d)
