"""The dataset: cases (one failed job each), their on-disk snapshots, labels and splits.

- data/cases.jsonl   metadata + labeller hints (committed; no log text)
- data/labels.jsonl  human-verified labels (committed)
- data/raw/<job_id>/ log.txt.gz, changes.json, history.json (git-ignored snapshots)

Everything the *agent* may see is a snapshot of what existed when the job failed. Hints that come
from the future (did a re-run pass? what did the fix change?) are for the labeller only.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from triagebench.config import settings
from triagebench.logs.clean import clean_log

Split = Literal["kb", "dev", "test", "external"]


class Hints(BaseModel):
    """Signals for the labeller. Never shown to any system under test (that would leak labels)."""

    rerun_succeeded: bool | None = None
    next_success_sha: str | None = None
    next_success_same_sha: bool | None = None
    fix_files: list[str] = Field(default_factory=list)
    same_day_repo_failures: int | None = None


class Case(BaseModel):
    id: str  # "{owner}/{repo}/jobs/{job_id}"
    repo: str
    run_id: int
    run_attempt: int
    job_id: int
    job_name: str
    workflow: str
    workflow_id: int
    event: str
    head_sha: str
    head_branch: str | None = None
    pr_number: int | None = None
    created_at: datetime
    html_url: str
    failed_steps: list[str] = Field(default_factory=list)
    log_lines: int = 0
    redactions: dict[str, int] = Field(default_factory=dict)
    source: Literal["failed_run", "rerun_passed", "live", "fixture"] = "failed_run"
    split: Split | None = None
    fetched_at: datetime
    hints: Hints = Field(default_factory=Hints)


class Evidence(BaseModel):
    line: int
    text: str


class Label(BaseModel):
    case_id: str
    version: int = 1
    failure_type: str
    cause_class: str
    root_cause: str
    evidence_lines: list[int] = Field(default_factory=list)
    duplicate_group: str | None = None
    labeller: str
    ai_drafted: bool = False
    verified: bool = False  # True only after Ahraz reviewed it
    notes: str = ""
    created_at: datetime


# ---- jsonl helpers -----------------------------------------------------------------------------
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    path.write_text(text, encoding="utf-8")


def load_cases(path: Path | None = None) -> list[Case]:
    return [Case.model_validate(r) for r in read_jsonl(path or settings().cases_file)]


def save_cases(cases: list[Case], path: Path | None = None) -> None:
    rows = [c.model_dump(mode="json") for c in sorted(cases, key=lambda c: (c.repo, c.created_at))]
    write_jsonl(path or settings().cases_file, rows)


def load_labels(path: Path | None = None) -> dict[str, Label]:
    """Latest version of each case's label."""
    out: dict[str, Label] = {}
    for r in read_jsonl(path or settings().labels_file):
        lab = Label.model_validate(r)
        if lab.case_id not in out or lab.version >= out[lab.case_id].version:
            out[lab.case_id] = lab
    return out


# ---- snapshots on disk -------------------------------------------------------------------------
class CaseStore:
    """Reads the snapshot of each case. Tools and baselines only ever read through this."""

    def __init__(self, cases: list[Case], data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or settings().data_dir
        self.by_job: dict[int, Case] = {c.job_id: c for c in cases}
        self.by_id: dict[str, Case] = {c.id: c for c in cases}
        self._lines: dict[int, list[str]] = {}

    def add(self, case: Case) -> None:
        self.by_job[case.job_id] = case
        self.by_id[case.id] = case

    def case_dir(self, job_id: int) -> Path:
        return self.data_dir / str(job_id)

    def get(self, job_id: int) -> Case:
        if job_id not in self.by_job:
            raise KeyError(f"job {job_id} isn't in the snapshot store")
        return self.by_job[job_id]

    def lines(self, job_id: int) -> list[str]:
        if job_id not in self._lines:
            path = self.case_dir(job_id) / "log.txt.gz"
            if not path.exists():
                raise FileNotFoundError(
                    f"job {job_id} has no saved log (expired before collection?)"
                )
            raw = gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")
            self._lines[job_id] = clean_log(raw)
        return self._lines[job_id]

    def snapshot(self, job_id: int, name: str) -> Any:
        path = self.case_dir(job_id) / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_snapshot(data_dir: Path, job_id: int, log_text: str, extras: dict[str, Any]) -> None:
    d = data_dir / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "log.txt.gz").write_bytes(gzip.compress(log_text.encode("utf-8")))
    for name, obj in extras.items():
        (d / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")


@lru_cache
def default_store() -> CaseStore:
    return CaseStore(load_cases())
