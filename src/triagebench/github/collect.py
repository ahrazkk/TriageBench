"""Snapshot failed GitHub Actions jobs before GitHub deletes them (public logs live <= 90 days).

For each repo we take two kinds of failure:
- failed_run:   runs whose latest attempt failed
- rerun_passed: runs that failed, were re-run, and passed (a flaky/environment candidate)

For each chosen job we save the redacted log plus what an on-call engineer could have seen at that
moment (the change under test, the workflow's recent history), and separately some *hints* from
the future (re-run outcome, the next green commit and the files it changed) for the labeller.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from triagebench.data import Case, Hints, write_snapshot
from triagebench.github.client import GitHubClient, GitHubError
from triagebench.logs.clean import clean_log
from triagebench.logs.redact import redact

MAX_PATCH_CHARS = 1500
MAX_FILES = 40
Log = Callable[[str], None]


def _iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def norm_job(name: str) -> str:
    """'test (3.12, ubuntu-latest)' -> 'test': matrix values vary, the job is the same."""
    return re.sub(r"\s*\(.*\)\s*$", "", name).strip().lower()


def parse_run_url(url: str) -> tuple[str, int, int | None]:
    """https://github.com/o/r/actions/runs/123[/job/456] -> ('o/r', 123, 456 or None)."""
    m = re.search(r"github\.com/([^/]+/[^/]+)/actions/runs/(\d+)(?:/job/(\d+))?", url)
    if not m:
        raise ValueError(f"not a GitHub Actions run URL: {url}")
    return m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None


# ---- snapshots of what was visible at failure time --------------------------------------------
def change_summary(gh: GitHubClient, repo: str, run: dict[str, Any]) -> dict[str, Any]:
    """The change under test: the PR's diff against its base, or the pushed commit.

    The diff always ends at run["head_sha"], the commit that was actually tested. The run's
    `pull_requests[0].head.sha` is the PR head *when we fetch it*, which can be later: it may
    include the fix, which would leak the answer to the agent. Compare is three-dot (from the
    merge base), so a base branch that moved on since doesn't matter.
    """
    data: dict[str, Any] | None = None
    kind = "commit"
    prs = run.get("pull_requests") or []
    base = head = None
    if run.get("event") == "pull_request" and prs:
        base, head = prs[0]["base"]["sha"], run["head_sha"]
        try:
            data = gh.compare(repo, base, head)
            kind = "pull_request"
        except GitHubError:
            data = None
    if data is None:
        try:
            data = gh.commit(repo, run["head_sha"])
        except GitHubError:
            return {"kind": "unavailable", "message": "", "total_files": 0, "files": []}
    files = data.get("files") or []
    if kind == "pull_request":
        commits = data.get("commits") or []
        msg = "\n".join((c.get("commit") or {}).get("message", "").split("\n")[0] for c in commits)
    else:
        msg = (data.get("commit") or {}).get("message") or ""
    msg, _ = redact(msg[:2000])
    out_files = []
    for f in files[:MAX_FILES]:
        patch, _ = redact((f.get("patch") or "")[:MAX_PATCH_CHARS])
        out_files.append(
            {
                "filename": f["filename"],
                "status": f.get("status"),
                "additions": f.get("additions"),
                "deletions": f.get("deletions"),
                "patch": patch,
            }
        )
    compared = (
        {"base": base, "head": head} if kind == "pull_request" else {"commit": run["head_sha"]}
    )
    return {
        "kind": kind,
        "compared": compared,
        "message": msg,
        "total_files": len(files),
        "files": out_files,
    }


def run_history(gh: GitHubClient, repo: str, run: dict[str, Any], job_name: str) -> dict[str, Any]:
    """Earlier runs of the same workflow (same branch, then the default branch), strictly before."""
    before = run["created_at"]
    wid = run["workflow_id"]
    branches: list[str] = [b for b in [run.get("head_branch")] if b]
    default = (run.get("repository") or {}).get("default_branch") or "main"
    if default not in branches:
        branches.append(default)
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for br in branches:
        try:
            runs = gh.workflow_runs(repo, wid, branch=br, created=f"<{before}", per_page=15)
        except GitHubError:
            continue
        for r in runs:
            if r["id"] in seen or r["id"] == run["id"]:
                continue
            seen.add(r["id"])
            rows.append(
                {
                    "run_id": r["id"],
                    "branch": r.get("head_branch"),
                    "event": r.get("event"),
                    "sha": r["head_sha"][:10],
                    "conclusion": r.get("conclusion"),
                    "run_attempt": r.get("run_attempt"),
                    "created_at": r["created_at"],
                }
            )
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    # Which jobs failed in the most recent earlier failures? The same job failing on unrelated
    # commits is the classic flaky signal.
    target = norm_job(job_name)
    for r in [r for r in rows if r["conclusion"] == "failure"][:6]:
        try:
            jobs = gh.attempt_jobs(repo, r["run_id"], int(r.get("run_attempt") or 1))
        except GitHubError:
            continue
        failed = [j["name"] for j in jobs if j.get("conclusion") == "failure"]
        r["failed_jobs"] = failed[:8]
        r["same_job_failed"] = any(norm_job(n) == target for n in failed)
    return {"default_branch": default, "runs": rows[:25]}


# ---- labeller hints (from the future) ----------------------------------------------------------
def hints_for(
    gh: GitHubClient, repo: str, run: dict[str, Any], source: str, same_day: int
) -> Hints:
    h = Hints(rerun_succeeded=(source == "rerun_passed"), same_day_repo_failures=same_day)
    if source == "rerun_passed":
        h.next_success_sha = run["head_sha"]
        h.next_success_same_sha = True
        return h
    try:
        later = gh.workflow_runs(
            repo,
            run["workflow_id"],
            branch=run.get("head_branch"),
            status="success",
            created=f">={run['created_at']}",
            per_page=20,
        )
    except GitHubError:
        return h
    later = sorted(later, key=lambda r: r["created_at"])
    if later:
        nxt = later[0]
        h.next_success_sha = nxt["head_sha"]
        h.next_success_same_sha = nxt["head_sha"] == run["head_sha"]
        if not h.next_success_same_sha:
            try:
                cmp = gh.compare(repo, run["head_sha"], nxt["head_sha"])
                h.fix_files = [f["filename"] for f in (cmp.get("files") or [])][:60]
            except GitHubError:
                pass
    return h


# ---- one job -> one case -------------------------------------------------------------------------
def snapshot_job(
    gh: GitHubClient,
    repo: str,
    run: dict[str, Any],
    job: dict[str, Any],
    data_dir: Path,
    source: str,
    same_day: int = 0,
    with_hints: bool = True,
) -> Case | None:
    try:
        raw = gh.job_log(repo, job["id"])
    except GitHubError:
        return None  # expired or not accessible
    if not raw.strip():
        return None
    text, counts = redact(raw)
    lines = clean_log(text)
    changes = change_summary(gh, repo, run)
    history = run_history(gh, repo, run, job["name"])
    hints = hints_for(gh, repo, run, source, same_day) if with_hints else Hints()
    prs = run.get("pull_requests") or []
    failed_steps = [s["name"] for s in job.get("steps") or [] if s.get("conclusion") == "failure"]
    case = Case(
        id=f"{repo}/jobs/{job['id']}",
        repo=repo,
        run_id=run["id"],
        run_attempt=int(job.get("run_attempt") or run.get("run_attempt") or 1),
        job_id=job["id"],
        job_name=job["name"],
        workflow=run.get("name") or "",
        workflow_id=run["workflow_id"],
        event=run.get("event") or "",
        head_sha=run["head_sha"],
        head_branch=run.get("head_branch"),
        pr_number=prs[0]["number"] if prs else None,
        created_at=_iso(job.get("started_at") or run["created_at"]),
        html_url=job.get("html_url") or run.get("html_url") or "",
        failed_steps=failed_steps,
        log_lines=len(lines),
        redactions=counts,
        source=source,  # type: ignore[arg-type]
        fetched_at=datetime.now(UTC),
        hints=hints,
    )
    jobs = run.get("_jobs") or [job]
    overview = {
        "run_name": run.get("name"),
        "display_title": redact((run.get("display_title") or "")[:200])[0],
        "run_attempt": case.run_attempt,
        "jobs": [{"name": j["name"], "conclusion": j.get("conclusion")} for j in jobs][:40],
    }
    write_snapshot(
        data_dir, job["id"], text, {"changes": changes, "history": history, "overview": overview}
    )
    return case


def _pick_failed_job(jobs: list[dict[str, Any]], taken: set[str]) -> dict[str, Any] | None:
    """The first failed job whose (matrix-normalised) name we haven't collected yet."""
    failed = [j for j in jobs if j.get("conclusion") == "failure"]
    fresh = [j for j in failed if norm_job(j["name"]) not in taken]
    return fresh[0] if fresh else None


def _spread(runs: list[dict[str, Any]], known: set[int]) -> list[dict[str, Any]]:
    """Order runs round-robin across workflows, and newest-to-oldest-evenly within each, so the
    first picks cover different workflows and different days. One run per commit."""
    by_wf: dict[int, list[dict[str, Any]]] = {}
    for r in sorted(runs, key=lambda r: r["created_at"]):
        by_wf.setdefault(int(r["workflow_id"]), []).append(r)
    queues: list[list[dict[str, Any]]] = []
    for group in by_wf.values():
        # interleave from both ends and the middle so one workflow's picks span its time range
        order: list[dict[str, Any]] = []
        lo, hi = 0, len(group) - 1
        while lo <= hi:
            order.append(group[hi])
            if lo != hi:
                order.append(group[lo])
            lo, hi = lo + 1, hi - 1
        queues.append(order)
    queues.sort(key=len, reverse=True)
    out: list[dict[str, Any]] = []
    seen_sha: set[str] = set()
    while any(queues):
        for q in queues:
            if not q:
                continue
            r = q.pop(0)
            if r["head_sha"] in seen_sha or r["id"] in known:
                continue
            seen_sha.add(r["head_sha"])
            out.append(r)
    return out


def collect_repo(
    gh: GitHubClient,
    repo: str,
    data_dir: Path,
    days: int = 30,
    max_cases: int = 6,
    known_runs: set[int] | None = None,
    known_job_names: set[str] | None = None,
    log: Log = print,
) -> list[Case]:
    since = (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d")
    known = known_runs or set()
    events = ("push", "pull_request")
    try:
        failed = gh.list_runs(repo, status="failure", created=f">={since}")
        passed = gh.list_runs(repo, status="success", created=f">={since}")
    except GitHubError as e:
        log(f"  {repo}: {e}")
        return []
    failed = [r for r in failed if r.get("event") in events]
    reran = [r for r in passed if int(r.get("run_attempt") or 1) >= 2 and r.get("event") in events]
    per_day = Counter(r["created_at"][:10] for r in failed)

    n_rerun = min(len(reran), max(1, max_cases // 3))
    # Cap how many candidates we inspect: each costs a jobs call, and many re-run candidates
    # turn out to have a cancelled (not failed) first attempt.
    plan = [(r, "rerun_passed") for r in _spread(reran, known)[: 4 * n_rerun]] + [
        (r, "failed_run") for r in _spread(failed, known)[: 4 * max_cases]
    ]
    cases: list[Case] = []
    rerun_taken = 0
    taken_jobs: set[str] = set(known_job_names or set())
    for run, source in plan:
        if len(cases) >= max_cases:
            break
        if source == "rerun_passed" and rerun_taken >= n_rerun:
            continue
        attempt = 1 if source == "rerun_passed" else int(run.get("run_attempt") or 1)
        try:
            jobs = gh.attempt_jobs(repo, run["id"], attempt)
        except GitHubError:
            continue
        job = _pick_failed_job(jobs, taken_jobs)
        if job is None:
            continue
        taken_jobs.add(norm_job(job["name"]))
        run = {**run, "_jobs": jobs, "run_attempt": attempt}
        case = snapshot_job(gh, repo, run, job, data_dir, source, per_day[run["created_at"][:10]])
        if case is None:
            continue
        cases.append(case)
        rerun_taken += source == "rerun_passed"
        log(f"  + {case.id} [{source}] {case.job_name[:50]} ({case.log_lines} lines)")
    return cases
