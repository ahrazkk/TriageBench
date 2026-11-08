# TriageBench results

Every number here comes from a file in `results/`. Conditions are stated with each table.
**Label status matters:** until Ahraz has reviewed the AI-drafted labels (`triagebench review`),
every accuracy number below is **preliminary**.

## Environment

| Item | Value |
|---|---|
| Machine | Windows 11 + WSL2 Ubuntu 24.04, RTX 3060 Laptop GPU (6 GB), 16 GB RAM |
| Model | `claude-haiku-5-5`, effort `medium`, adaptive thinking, auto prompt caching |
| Prices (checked 2026-11-07) | $0.10 / $0.50 per 1M input / output tokens; cache reads 10%, cache writes 125% |
| Database | PostgreSQL 16 + pgvector 0.6.0 (apt, in WSL) |
| Embeddings | Qwen3-Embedding-0.6B (1,024 dims), local GPU |
| GitHub API | authenticated, 5,000 requests/hour; ~13 requests per collected case (measured) |

## Dataset v0 (collected 2026-11-07)

98 failed GitHub Actions jobs from 18 public repositories (Python, Rust, JS/TS, Go, Java, Docker),
from the last 30 days. 92 are runs whose latest attempt failed; 6 failed and then passed on a
re-run of the same commit. Logs: P50 869 lines, P95 17,671, max 170,464. 100 strings redacted
(emails, token-shaped strings). Split by time within each repo: 48 `kb` (retrieval memory), 50
`dev`. No `test` split yet (frozen in M7).

(Per-repo and per-label counts: `triagebench stats`.)

## MCP overhead (M6)

`results/20261107-mcp-overhead.json`: the MCP server started as a stdio subprocess (as Claude
Code does) returned byte-identical output to direct calls on 20 calls; median 21.9 ms per call
over MCP vs 28.8 ms direct, so the difference is within noise at this scale. Server start-up took
4.7 s, mostly WSL and Python imports.

## Main table (dev, preliminary)

Split `dev`, N = 50 real failures from 18 repos, dataset `04af762d0caa`, model
`claude-haiku-5-5` (effort medium), prompt `15ddbbae98b6`, budgets 8 model turns / 60,000 input
tokens, 2026-11-08, code not yet committed. **Labels: 0 human-verified, 50 AI-drafted** (Claude
Opus via Claude Code subagents, from label packs that include future-only hints), so every number
here is **preliminary**, and the drafter and the systems under test are both Claude models.

| System | Axis A acc [95% CI] | A macro-F1 | Axis B acc [95% CI] | B macro-F1 | Both right | Evidence valid | Confident errors (B) | Cost / triage | Latency P50 / P95 | Model calls | Result file |
|---|---|---|---|---|---|---|---|---|---|---|---|
| B0 regex rules | 42.0% [29.4–55.8%] | 0.377 | 30.0% [19.1–43.8%] | 0.098 | 22.0% | 100% (by construction) | 0.0% | $0 | 0.1 / 1.0 s | 0 | `results/20261108-025010-b0-dev.json` |
| B1 single LLM call | 88.0% [76.2–94.4%] | 0.712 | 76.0% [62.6–85.7%] | 0.641 | 68.0% | 100% | 4.0% | $0.0013 | 4.8 / 7.1 s | 1.0 | `results/20261108-031750-b1-dev.json` |
| B2 agent with tools | 92.0% [81.2–96.8%] | 0.843 | 80.0% [67.0–88.8%] | 0.687 | 74.0% | 100% | 6.0% | $0.0023 | 7.3 / 11.3 s | 3.2 | `results/20261108-031928-b2-dev.json` |
| B3 agent + retrieval | 88.0% [76.2–94.4%] | 0.849 | 72.0% [58.3–82.5%] | 0.592 | 68.0% | 100% | 0.0% | $0.0025 | 8.5 / 14.0 s | 3.4 | `results/20261108-032211-b3-dev.json` |

**Paired comparisons** (exact McNemar on the same 50 cases; "fixed / broken" counts cases the
second system got right and the first wrong, and vice versa):

| Comparison | Axis | Δ accuracy | Fixed / broken | McNemar p | Verdict |
|---|---|---|---|---|---|
| B1 vs B0 | A | +46.0 pts | 25 / 2 | < 0.001 | significant |
| B1 vs B0 | B | +46.0 pts | 24 / 1 | < 0.001 | significant |
| B2 vs B1 | A | +4.0 pts | 3 / 1 | 0.625 | not significant |
| B2 vs B1 | B | +4.0 pts | 6 / 4 | 0.754 | not significant |
| B3 vs B2 | A | −4.0 pts | 2 / 4 | 0.688 | not significant |
| B3 vs B2 | B | −8.0 pts | 2 / 6 | 0.289 | not significant |

The whole dev run (B1 + B2 + B3, 150 triages) cost **$0.30** (sum of `cost_usd_total`). Every
quoted evidence line in every answer was verified verbatim against its log (0 hallucinated
quotes; one B3 answer needed the repair turn).

### What the numbers say

1. **The LLM is the big win.** One call on a reduced log beats the regex rules by 46 points on
   both axes, which is far outside the noise.
2. **The agent's +4 points over the single call is not significant at N = 50.** B2 fixed 6 axis-B
   cases and broke 4. With 50 cases, a real gain this size can't be told apart from noise; it
   needs the 100–150-case test set (M7).
3. **Retrieval didn't help on dataset v0** and leaned negative (−8 points on axis B, p = 0.29).
   That matches the retrieval-only result below: across 18 repos, past failures rarely repeat
   within 30 days, so the nearest neighbours point at the right cause only ~24–34% of the time.
4. **Rules can't do axis B:** B0 calls almost everything a `regression` (the majority class, 22 of
   50 dev labels), so its B macro-F1 is 0.098.

### Error analysis (axis B)

- **B1's 12 misses: 10 are "unknown"** (it abstains rather than guessing). Four times the
  decisive line was cut from the excerpt ("the actual Go module error is in the omitted
  lines"), 2–3 times it needed the diff or history, and 3 are PR-policy gates whose label
  convention is still open (see LABELING_GUIDE).
- **B2's 6 fixes are exactly those:** every one used `get_change_summary` / `get_run_history`
  and often `search_log` / `read_log_lines` to find the cut line. Example: a Trivy scan failed on
  `golang.org/x/net`; the diff showed the PR never touched dependencies → `environment`.
- **B2's 4 breaks:** three became "unknown" after more evidence (more cautious), one is a case
  whose drafted label was itself low-confidence (concurrent docs pushes: flaky vs ci_config).
- **Budget exhaustion on huge logs:** 1 B2 case and 3 B3 cases hit the 60,000 input-token budget
  (docker/compose and home-assistant logs; the agent kept searching). No answer counts as wrong.
- **Retrieval steering wrong:** in aiohttp 128398488166 the agent cited a "similar" kb case
  labelled `ci_config` and copied its cause; the real cause was an outside image change.

Next fixes to try (one at a time, re-measured on dev): tell the agent how much budget it has
left so it answers before running out; tell it to treat similar cases as hints only when the
error text matches; then grow the dataset so retrieval has repeats to find.

## Retrieval on its own (M5)

`results/20261108-030203-retrieval-dev.json`: each of the 50 dev cases queries the 48-case `kb`
with its own error region (lines capped at 400 characters, labels never in the query), with
Qwen3-Embedding-0.6B on the GPU and pgvector 0.6.0.

| Mode | Top-1 same failure_type | Top-1 same cause_class | Top-5 majority same failure_type | Latency P50 |
|---|---|---|---|---|
| keyword (Postgres full-text, `simple`) | 32% | 30% | 44% | 26 ms |
| vector (cosine, HNSW) | 48% | 28% | 42% | 95 ms |
| hybrid (RRF, k = 60) | 34% | 24% | 52% | 103 ms |

- **HNSW top-5 = exact top-5 on all 50 queries (recall 1.0).** At 98 vectors exact search would be
  just as fast; the index is there to learn it and to show the check.
- Only **1** dev case has a labelled duplicate in `kb` (found at top-5 by all three modes), so
  recall@5 rests on one query and isn't a meaningful number yet.
- Finding: across 18 different repos, past failures rarely repeat within 30 days, so the
  retrieved neighbours agree with the true cause class only ~24–34% of the time. B3 may not beat
  B2 on dataset v0; more cases per repo (M7) is what gives retrieval something to find.

## Resume bullet (draft, from the numbers above)

Finalize only after the labels are reviewed: replace "AI-drafted" wording and re-run if any
label changes.

> Built an LLM agent that triages failing GitHub Actions jobs with read-only tools over logs,
> diffs and run history, exposed as an MCP server usable from Claude Code; on 50 labelled real
> failures from 18 public repos it reached 92% failure-type and 80% root-cause accuracy vs 42% /
> 30% for a regex baseline, at $0.002 per triage, with every quoted evidence line verified
> against the log; paired McNemar tests showed the single-call workflow captured most of the gain.
