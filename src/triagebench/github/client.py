"""A small, polite GitHub REST client: auth, a disk cache with ETags, and rate-limit backoff.

Conditional requests (If-None-Match) that come back 304 don't count against the 5,000/hour limit,
so re-running the collector is nearly free.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

API = "https://api.github.com"


class GitHubError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"GitHub {status}: {message}")
        self.status = status


class GitHubClient:
    def __init__(
        self,
        token: str,
        cache_dir: Path | None = None,
        transport: httpx.BaseTransport | None = None,
        max_wait_s: float = 900,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "triagebench-collector",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=API, headers=headers, timeout=30, follow_redirects=True, transport=transport
        )
        self.cache_dir = cache_dir
        self.max_wait_s = max_wait_s
        self.calls = 0
        self.remaining: int | None = None

    def close(self) -> None:
        self._http.close()

    # ---- low level -------------------------------------------------------------------------
    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def _request(self, url: str, headers: dict[str, str] | None = None) -> httpx.Response:
        for attempt in range(6):
            self.calls += 1
            t0 = time.perf_counter()
            try:
                resp = self._http.get(url, headers=headers or {})
            except httpx.TimeoutException:
                print(f"    [timeout] {url[:120]}", file=sys.stderr)
                if attempt >= 2:
                    raise GitHubError(504, f"timed out: {url[:120]}") from None
                continue
            dt = time.perf_counter() - t0
            if dt > 3:
                print(f"    [slow {dt:.1f}s {resp.status_code}] {url[:120]}", file=sys.stderr)
            rem = resp.headers.get("x-ratelimit-remaining")
            if rem is not None:
                self.remaining = int(rem)
            if resp.status_code in (403, 429) and (
                self.remaining == 0 or "retry-after" in resp.headers
            ):
                reset = float(resp.headers.get("x-ratelimit-reset", time.time() + 60))
                wait = float(resp.headers.get("retry-after", max(1.0, reset - time.time())))
                if wait > self.max_wait_s:
                    raise GitHubError(resp.status_code, f"rate limited for {wait:.0f}s")
                time.sleep(wait + 1)
                continue
            if resp.status_code >= 500:
                print(f"    [{resp.status_code}, retrying] {url[:120]}", file=sys.stderr)
                time.sleep(2**attempt)
                continue
            return resp
        raise GitHubError(503, f"gave up after retries: {url[:120]}")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = str(self._http.build_request("GET", path, params=params).url)
        cpath = self._cache_path(url)
        cached: dict[str, Any] | None = None
        headers: dict[str, str] = {}
        if cpath and cpath.exists():
            cached = json.loads(cpath.read_text(encoding="utf-8"))
            if cached and cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
        resp = self._request(url, headers)
        if resp.status_code == 304 and cached is not None:
            return cached["body"]
        if resp.status_code >= 400:
            raise GitHubError(resp.status_code, resp.text[:200])
        body = resp.json()
        if cpath:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            cpath.write_text(
                json.dumps({"etag": resp.headers.get("etag"), "body": body}), encoding="utf-8"
            )
        return body

    def get_text(self, path: str) -> str:
        """Plain-text download (job logs). GitHub answers with a redirect to short-lived storage."""
        resp = self._request(path)
        if resp.status_code >= 400:
            raise GitHubError(resp.status_code, resp.text[:200])
        return resp.text

    # ---- endpoints -------------------------------------------------------------------------
    def list_runs(self, repo: str, **params: Any) -> list[dict[str, Any]]:
        params.setdefault("per_page", 100)
        return list(self.get_json(f"/repos/{repo}/actions/runs", params)["workflow_runs"])

    def workflow_runs(self, repo: str, workflow_id: int, **params: Any) -> list[dict[str, Any]]:
        params.setdefault("per_page", 30)
        path = f"/repos/{repo}/actions/workflows/{workflow_id}/runs"
        return list(self.get_json(path, params)["workflow_runs"])

    def get_run(self, repo: str, run_id: int) -> dict[str, Any]:
        return dict(self.get_json(f"/repos/{repo}/actions/runs/{run_id}"))

    def attempt_jobs(self, repo: str, run_id: int, attempt: int) -> list[dict[str, Any]]:
        path = f"/repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs"
        return list(self.get_json(path, {"per_page": 100})["jobs"])

    def job_log(self, repo: str, job_id: int) -> str:
        return self.get_text(f"/repos/{repo}/actions/jobs/{job_id}/logs")

    def commit(self, repo: str, sha: str) -> dict[str, Any]:
        return dict(self.get_json(f"/repos/{repo}/commits/{sha}"))

    def compare(self, repo: str, base: str, head: str) -> dict[str, Any]:
        return dict(self.get_json(f"/repos/{repo}/compare/{base}...{head}"))

    def repo_info(self, repo: str) -> dict[str, Any]:
        return dict(self.get_json(f"/repos/{repo}"))

    def rate_limit(self) -> dict[str, Any]:
        return dict(self.get_json("/rate_limit")["resources"]["core"])
