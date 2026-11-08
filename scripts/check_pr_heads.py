"""Leak check: for pull-request cases, was the saved diff computed against the commit that was
actually tested (the run's head_sha), or against the PR's head at collection time (possibly
later, including the fix)? Uses the collector's HTTP cache only (no API calls)."""

from __future__ import annotations

import json

from triagebench.config import settings
from triagebench.data import load_cases

cache = settings().data_dir / "_http_cache"
pr_head: dict[int, str] = {}
for f in cache.glob("*.json"):
    try:
        body = json.loads(f.read_text(encoding="utf-8"))["body"]
    except (json.JSONDecodeError, KeyError):
        continue
    runs = body.get("workflow_runs") if isinstance(body, dict) else None
    for r in runs or []:
        prs = r.get("pull_requests") or []
        if prs:
            pr_head[r["id"]] = prs[0]["head"]["sha"]

cases = [c for c in load_cases() if c.event == "pull_request"]
known = [c for c in cases if c.run_id in pr_head]
moved = [c for c in known if pr_head[c.run_id] != c.head_sha]
print(
    f"pull_request cases: {len(cases)}; PR info cached for {len(known)}; "
    f"PR head != tested commit: {len(moved)}"
)
for c in moved:
    ch = json.loads((settings().data_dir / str(c.job_id) / "changes.json").read_text())
    print(
        f"  {c.split:<4} {c.id}  tested {c.head_sha[:10]} vs PR head {pr_head[c.run_id][:10]}"
        f"  kind={ch['kind']}"
    )
