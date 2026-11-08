"""The agent's read-only tools, as plain Python functions over the case snapshots.

Design rules (Anthropic, "Writing effective tools for agents"):
- few, purposeful tools; the description says *when* to use each one
- token-efficient output: numbered lines, short tables, truncation notes that say what to do next
- errors the model can recover from ("pattern isn't a valid regex: ..."), never stack traces
- read-only: nothing here can change GitHub
- everything that came from a CI log is wrapped in <untrusted_log>: it's data, never instructions

Each tool is exposed twice from this one implementation: called directly by the agent loop and
tests, and over MCP by mcp_server.py.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from triagebench.config import secrets, settings
from triagebench.data import Case, CaseStore, load_cases
from triagebench.logs.reduce import MAX_LINE_CHARS, TRUNC_MARK, reduce_log

MAX_MATCHES = 50
MAX_READ_LINES = 200


class ToolError(Exception):
    """A failure the model should see and can recover from."""


class ToolRunner(Protocol):
    """What the agent loop needs: run a tool by name, and read logs to verify evidence.
    Implemented by Toolbox (direct calls) and McpToolbox (calls over MCP)."""

    store: CaseStore
    calls: list[str]

    def call(self, name: str, args: dict[str, Any]) -> str: ...


def _clip(text: str) -> str:
    return text if len(text) <= MAX_LINE_CHARS else text[:MAX_LINE_CHARS] + TRUNC_MARK


def _untrusted(job_id: int, body: str) -> str:
    return f'<untrusted_log job_id="{job_id}">\n{body}\n</untrusted_log>'


@dataclass
class Toolbox:
    store: CaseStore
    retriever: Any | None = None  # retrieval.search.Retriever, only for B3
    allow_live: bool = False  # fetch unknown runs from GitHub (MCP / CLI use, never in evals)
    calls: list[str] = field(default_factory=list)

    @classmethod
    def default(cls, with_retrieval: bool = False, allow_live: bool = True) -> Toolbox:
        retriever = None
        if with_retrieval:
            from triagebench.retrieval.search import Retriever

            retriever = Retriever()
        return cls(CaseStore(load_cases()), retriever=retriever, allow_live=allow_live)

    # ---- helpers ---------------------------------------------------------------------------
    def _case(self, job_id: int) -> Case:
        try:
            return self.store.get(int(job_id))
        except (KeyError, ValueError):
            raise ToolError(
                f"job {job_id} isn't known. Call get_run_overview with the run URL first."
            ) from None

    def _lines(self, job_id: int) -> list[str]:
        self._case(job_id)
        try:
            return self.store.lines(int(job_id))
        except FileNotFoundError as e:
            raise ToolError(str(e)) from None

    def resolve(self, target: str) -> int:
        """A job id, a case id, or a run URL -> a job id in the store (fetching live if allowed)."""
        t = target.strip()
        if t.isdigit():
            if int(t) in self.store.by_job:
                return int(t)
            raise ToolError(f"job {t} isn't in the snapshot store; pass the run URL instead")
        if t in self.store.by_id:
            return self.store.by_id[t].job_id
        if "github.com" in t and "/actions/runs/" in t:
            return self._fetch_live(t)
        raise ToolError(f"can't resolve {t!r}: expected a run URL, a job id or a case id")

    def _fetch_live(self, url: str) -> int:
        from triagebench.github.client import GitHubClient
        from triagebench.github.collect import parse_run_url, snapshot_job

        repo, run_id, job_id = parse_run_url(url)
        for c in self.store.by_job.values():
            if c.run_id == run_id and (job_id is None or c.job_id == job_id):
                return c.job_id
        if not self.allow_live:
            raise ToolError("live fetching is disabled (evaluation mode)")
        gh = GitHubClient(secrets().github_token, cache_dir=settings().data_dir / "_http_cache")
        run = gh.get_run(repo, run_id)
        attempt = int(run.get("run_attempt") or 1)
        jobs = gh.attempt_jobs(repo, run_id, attempt)
        failed = [j for j in jobs if j.get("conclusion") == "failure"]
        pick = next((j for j in failed if j["id"] == job_id), failed[0] if failed else None)
        if pick is None:
            raise ToolError(
                f"run {run_id} has no failed jobs (conclusion: {run.get('conclusion')})"
            )
        case = snapshot_job(
            gh, repo, {**run, "_jobs": jobs}, pick, self.store.data_dir, "live", with_hints=False
        )
        if case is None:
            raise ToolError("couldn't download that job's log (expired, or no access)")
        self.store.add(case)
        return case.job_id

    # ---- tools -----------------------------------------------------------------------------
    def get_run_overview(self, target: str) -> str:
        job_id = self.resolve(str(target))
        c = self._case(job_id)
        ov = self.store.snapshot(job_id, "overview") or {}
        ch = self.store.snapshot(job_id, "changes") or {}
        jobs = ov.get("jobs") or []
        failed = [j["name"] for j in jobs if j.get("conclusion") == "failure"]
        passed = sum(1 for j in jobs if j.get("conclusion") == "success")
        title = (ch.get("message") or "").split("\n")[0][:150]
        lines = [
            f"repo: {c.repo}",
            f"workflow: {c.workflow}   trigger: {c.event}   branch: {c.head_branch}"
            + (f"   PR #{c.pr_number}" if c.pr_number else ""),
            f"commit: {c.head_sha[:10]}  {title!r}",
            f"run: {c.run_id} (attempt {c.run_attempt})"
            f"   started: {c.created_at:%Y-%m-%d %H:%M} UTC",
            f"job under triage: {c.job_name!r}  job_id={c.job_id}",
            f"failed step(s): {', '.join(c.failed_steps) or 'unknown'}",
            f"log: {c.log_lines} lines",
            f"other jobs in this run: {passed} passed, {len(failed)} failed"
            + (f" ({', '.join(failed[:10])})" if failed else ""),
        ]
        return "\n".join(lines)

    def get_log_excerpt(self, job_id: int, strategy: str = "hybrid", max_tokens: int = 4000) -> str:
        lines = self._lines(job_id)
        max_tokens = max(500, min(int(max_tokens), 12_000))
        try:
            ex = reduce_log(lines, strategy, max_tokens)
        except ValueError as e:
            raise ToolError(str(e)) from None
        return ex.header() + "\n" + _untrusted(int(job_id), ex.render())

    def search_log(self, job_id: int, pattern: str, context: int = 2) -> str:
        lines = self._lines(job_id)
        context = max(0, min(int(context), 5))
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            raise ToolError(
                f"pattern isn't a valid Python regex ({e}); escape special characters"
            ) from None
        hits = [i for i, t in enumerate(lines) if rx.search(t)]
        if not hits:
            return f"no lines match {pattern!r} in {len(lines)} lines"
        shown = hits[:MAX_MATCHES]
        keep: set[int] = set()
        for i in shown:
            keep.update(range(max(0, i - context), min(len(lines), i + context + 1)))
        out: list[str] = []
        prev = -2
        for i in sorted(keep):
            if i != prev + 1:
                out.append("      …")
            mark = ">" if i in hits else " "
            out.append(f"{i + 1:>6}{mark} {_clip(lines[i])}")
            prev = i
        note = f"{len(hits)} matching lines"
        if len(hits) > MAX_MATCHES:
            note += f"; showing the first {MAX_MATCHES}, narrow the pattern to see others"
        return f"[{note}; '>' marks a match]\n" + _untrusted(int(job_id), "\n".join(out))

    def read_log_lines(self, job_id: int, start: int, end: int) -> str:
        lines = self._lines(job_id)
        start, end = max(1, int(start)), min(len(lines), int(end))
        if end < start:
            raise ToolError(f"empty range; the log has lines 1-{len(lines)}")
        note = ""
        if end - start + 1 > MAX_READ_LINES:
            end = start + MAX_READ_LINES - 1
            note = f" (capped at {MAX_READ_LINES} lines; ask for the next range if needed)"
        body = "\n".join(f"{n:>6}| {_clip(lines[n - 1])}" for n in range(start, end + 1))
        return f"[lines {start}-{end} of {len(lines)}{note}]\n" + _untrusted(int(job_id), body)

    def get_change_summary(self, job_id: int) -> str:
        self._case(job_id)
        ch = self.store.snapshot(int(job_id), "changes")
        if not ch or ch.get("kind") == "unavailable":
            return "no change information was saved for this run"
        files = ch.get("files") or []
        head = (
            f"change under test ({ch['kind']}): {ch.get('total_files', len(files))} files changed"
        )
        out = [head, "messages: " + (ch.get("message") or "")[:600].replace("\n", " | ")]
        budget = 6000
        for f in files:
            out.append(
                f"- {f['filename']} ({f.get('status')},"
                f" +{f.get('additions')}/-{f.get('deletions')})"
            )
            patch = f.get("patch") or ""
            if patch and budget > 0:
                snippet = patch[: min(800, budget)]
                budget -= len(snippet)
                out.append("  " + snippet.replace("\n", "\n  "))
        if len(files) < ch.get("total_files", 0) or budget <= 0:
            out.append("[diff truncated]")
        return "<untrusted_data>\n" + "\n".join(out) + "\n</untrusted_data>"

    def get_run_history(self, job_id: int) -> str:
        c = self._case(job_id)
        hist = self.store.snapshot(int(job_id), "history") or {}
        runs = hist.get("runs") or []
        if not runs:
            return "no earlier runs of this workflow were found"
        concl = [r.get("conclusion") for r in runs]
        same = [r for r in runs if r.get("same_job_failed")]
        out = [
            f"earlier runs of workflow {c.workflow!r} before this failure "
            f"(newest first; branch {c.head_branch!r} and default branch "
            f"{hist.get('default_branch')!r}):",
            f"summary: {concl.count('success')} success, {concl.count('failure')} failure, "
            f"{len(runs) - concl.count('success') - concl.count('failure')} other; "
            f"job {c.job_name!r} also failed in {len(same)} of the checked earlier failures",
        ]
        for r in runs[:20]:
            extra = ""
            if "failed_jobs" in r:
                extra = f"  failed jobs: {', '.join(r['failed_jobs'][:4])}"
            out.append(
                f"- {r['created_at'][:16]} {r.get('branch')!s:<20} {r['sha']} "
                f"{r.get('conclusion')!s:<10} attempt {r.get('run_attempt')}{extra}"
            )
        return "\n".join(out)

    def search_similar_failures(self, job_id: int, query: str, k: int = 5) -> str:
        if self.retriever is None:
            raise ToolError("retrieval isn't enabled for this system")
        c = self._case(job_id)
        hits = self.retriever.search(query, k=max(1, min(int(k), 10)), exclude_run=c.run_id)
        if not hits:
            return "no similar past failures found"
        out = ["similar past failures (from the knowledge base of older, labelled cases):"]
        for h in hits:
            out.append(
                f"- {h.case_id} [{h.repo}] score={h.score:.4f} failure_type={h.failure_type} "
                f"cause_class={h.cause_class}\n  root cause: {h.root_cause[:300]}"
            )
        return "<untrusted_data>\n" + "\n".join(out) + "\n</untrusted_data>"

    # ---- dispatch --------------------------------------------------------------------------
    def call(self, name: str, args: dict[str, Any]) -> str:
        fn: Callable[..., str] | None = getattr(self, name, None) if name in TOOL_NAMES else None
        if fn is None:
            raise ToolError(f"unknown tool {name!r}; available: {', '.join(TOOL_NAMES)}")
        self.calls.append(name)
        try:
            return fn(**args)
        except TypeError as e:
            raise ToolError(f"bad arguments for {name}: {e}") from None


_JOB = {"type": "integer", "description": "GitHub Actions job id (from get_run_overview)"}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_run_overview",
        "description": (
            "Start here. Summarise a failed GitHub Actions job: repo, workflow, trigger, branch, "
            "commit, which step failed, and which other jobs in the run failed. Accepts a run "
            "URL, a job id or a case id; returns the job_id the other tools need."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "run URL, job id or case id"}
            },
            "required": ["target"],
        },
    },
    {
        "name": "get_log_excerpt",
        "description": (
            "The job's log reduced to the lines that matter, with line numbers: step headers, "
            "windows around error-looking lines, and the tail. Use this before anything else in "
            "the log. strategy: hybrid (default), errors, or tail."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": _JOB,
                "strategy": {"type": "string", "enum": ["hybrid", "errors", "tail"]},
                "max_tokens": {"type": "integer", "description": "excerpt budget, 500-12000"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "search_log",
        "description": (
            "Search the full log with a case-insensitive Python regex. Returns up to 50 matching "
            "lines with line numbers and context. Use it when the excerpt doesn't show the first "
            "error, a specific test name, or an exit code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": _JOB,
                "pattern": {"type": "string"},
                "context": {"type": "integer", "description": "lines of context, 0-5"},
            },
            "required": ["job_id", "pattern"],
        },
    },
    {
        "name": "read_log_lines",
        "description": "Read exact log lines start..end (max 200), e.g. to expand around a match.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": _JOB,
                "start": {"type": "integer"},
                "end": {"type": "integer"},
            },
            "required": ["job_id", "start", "end"],
        },
    },
    {
        "name": "get_change_summary",
        "description": (
            "The change under test: files changed by the pull request or pushed commit, with "
            "small diff hunks. Use it to judge whether the change could have caused the failure "
            "(regression), touched only tests (test_defect) or only CI files (ci_config)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"job_id": _JOB},
            "required": ["job_id"],
        },
    },
    {
        "name": "get_run_history",
        "description": (
            "Earlier runs of the same workflow before this failure, and whether this job failed "
            "in them too. Check it before calling anything flaky or environmental: the same job "
            "failing on unrelated commits suggests flakiness; a green history then a red run on "
            "this change suggests a regression."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"job_id": _JOB},
            "required": ["job_id"],
        },
    },
    {
        "name": "search_similar_failures",
        "description": (
            "Find similar past failures in a knowledge base of older, labelled CI failures "
            "(hybrid keyword + semantic search). Query with the key error message, test name or "
            "package. Each hit carries its labels and root cause."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": _JOB,
                "query": {"type": "string"},
                "k": {"type": "integer", "description": "number of results, 1-10"},
            },
            "required": ["job_id", "query"],
        },
    },
]
TOOL_NAMES = [t["name"] for t in TOOL_SPECS]


def tool_specs(with_retrieval: bool) -> list[dict[str, Any]]:
    return [t for t in TOOL_SPECS if with_retrieval or t["name"] != "search_similar_failures"]
