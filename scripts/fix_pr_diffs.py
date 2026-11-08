"""Re-snapshot changes.json for pull-request cases whose saved diff went past the tested commit
(the leak fixed in github/collect.py:change_summary). Keeps the old file as changes.leaky.json.

    bash scripts/dev.sh python scripts/fix_pr_diffs.py
"""

from __future__ import annotations

import json

from triagebench.config import secrets, settings
from triagebench.data import load_cases
from triagebench.github.client import GitHubClient
from triagebench.github.collect import change_summary

s = settings()
cache = s.data_dir / "_http_cache"
pr_head: dict[int, str] = {}
for f in cache.glob("*.json"):
    try:
        body = json.loads(f.read_text(encoding="utf-8"))["body"]
    except (json.JSONDecodeError, KeyError):
        continue
    for r in (body.get("workflow_runs") if isinstance(body, dict) else None) or []:
        if r.get("pull_requests"):
            pr_head[r["id"]] = r["pull_requests"][0]["head"]["sha"]

gh = GitHubClient(secrets().github_token, cache_dir=cache)
for c in load_cases():
    if c.event != "pull_request" or pr_head.get(c.run_id, c.head_sha) == c.head_sha:
        continue
    d = s.data_dir / str(c.job_id)
    old = json.loads((d / "changes.json").read_text(encoding="utf-8"))
    run = gh.get_run(c.repo, c.run_id)
    assert run["head_sha"] == c.head_sha, "the run's tested commit never changes"
    new = change_summary(gh, c.repo, run)
    (d / "changes.leaky.json").write_text(json.dumps(old, indent=1), encoding="utf-8")
    (d / "changes.json").write_text(json.dumps(new, indent=1), encoding="utf-8")
    old_files = {f["filename"] for f in old["files"]}
    new_files = {f["filename"] for f in new["files"]}
    leaked = sorted(old_files - new_files)
    print(
        f"{c.split:<4} {c.id}: commits {len(old['message'].splitlines())} -> "
        f"{len(new['message'].splitlines())}, files {old['total_files']} -> {new['total_files']}"
        + (f"; only in the leaky diff: {', '.join(leaked[:5])}" if leaked else "")
    )
