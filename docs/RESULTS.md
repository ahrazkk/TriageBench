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

## Current numbers (prompt v2, three runs each, after the diff-leak fix; dev, preliminary)

Same 50 dev cases, labels 0 human-verified / 50 AI-drafted, prompt v2, effort medium, budgets 8
turns / 60,000 input tokens. Haiku: `results/20261108-042045-repeats-dev.json` over
`*-b{1,2,3}-dev-fix-r{0,1,2}.json`. Sonnet: `results/20261108-042057-model-ablation-dev.json`
over `*-b2-dev-fix-sonnet-r{0,1,2}.json`. Latency is from the earlier live runs (the post-fix
re-runs mostly replayed from recordings, so their timings mean nothing).

| System | Model | Axis A: what failed, mean (range) | Axis B: why, mean (range) | Flip rate B | Answers "unknown" | Cost / triage | Latency P50 |
|---|---|---|---|---|---|---|---|
| B0 regex rules | — | 42.0% | 30.0% | — | — | $0 | 0.1 s |
| B1 single call | Haiku 5.5 | 89.3% (88–90%) | 74.7% (74–76%) | 24% | — | $0.0011 | 4.8 s |
| **B2 agent** | Haiku 5.5 | **93.3%** (92–94%) | 82.0% (80–84%) | 10% | 13% | $0.0021 | 7.2 s |
| B3 agent + retrieval | Haiku 5.5 | 91.3% (90–92%) | 78.7% (76–82%) | 16% | — | $0.0025 | 8.5 s |
| B2 agent | Sonnet 5.5 | 91.3% (88–94%) | **88.0%** (86–90%) | **4%** | 5% | $0.0214 | 6.5 s |

Paired exact McNemar on each case's mean correctness over its three runs:

| Comparison | Axis | Δ mean accuracy | Fixed / broken | p | Verdict |
|---|---|---|---|---|---|
| B1 vs B0 | A / B | +47 / +45 pts | — | < 0.001 | significant |
| B2 vs B1 (Haiku) | A | +4.0 pts | 4 / 2 | 0.688 | not significant |
| B2 vs B1 (Haiku) | B | +7.3 pts | 11 / 5 | 0.210 | not significant |
| B3 vs B2 (Haiku) | A | −2.0 pts | 2 / 5 | 0.453 | not significant |
| B3 vs B2 (Haiku) | B | −3.3 pts | 3 / 6 | 0.508 | not significant |
| B2 Sonnet vs B2 Haiku | A | −2.0 pts | 3 / 5 | 0.727 | not significant |
| B2 Sonnet vs B2 Haiku | B | +6.0 pts | 7 / 1 | 0.070 | not significant (close) |

Every evidence quote in every answer verified (0 hallucinated quotes, 0 errors in any run).

**What it says:** one LLM call beats rules by ~45 points; the agent and the bigger model each add a
few points on the hard "why" axis, but at N = 50 neither gain is significant (Sonnet's p = 0.07 is
the closest); retrieval doesn't help on dataset v0. Sonnet is ~10× the price and far more
consistent between runs (4% vs 10% flip rate).

### The diff leak (found 2026-11-08, fixed)

An AI reviewer checking a case noticed that the saved "change under test" for some pull-request
runs went *past* the commit that was tested: the collector compared the PR's base with the PR's
head **at collection time**, which can include later commits, sometimes the fix itself. Through
`get_change_summary` the agent could see the future, which breaks the design rule (see
docs/design/M0-M6.md). 9 cases were affected (4 dev, 5 kb); labels were not (labellers may see
the future). Fix: `change_summary` now compares against `run["head_sha"]` (three-dot, from the
merge base), records what it compared, and a regression test pins it
(`test_pr_diff_stops_at_the_tested_commit_not_the_current_pr_head`). The 9 snapshots were rebuilt
with `scripts/fix_pr_diffs.py` (old files kept as `changes.leaky.json`) and every LLM system was
re-run; unchanged cases replayed from recordings, so the re-run cost $0.18.

Impact: Haiku B2 axis B 84.0% → 82.0% (it had answered two cases right only by seeing the fix:
ruff 127975085701 3/3 → 1/3 runs, pydantic 128410383632 2/3 → 0/3); Sonnet unchanged. Small,
but it was inflating exactly the system and axis the project is about.

### Independent review of the Haiku/Sonnet disagreements

A workflow (`wf_ce8608dd-861`, 20 AI agents) reviewed the 14 cases where the two models disagreed:
for each, an independent judge formed its own label from the raw evidence before seeing the
draft or the answers; where it disputed the draft, a skeptic tried to defend the draft. Result:
**8 drafts upheld, 4 upheld after a challenge (genuinely borderline), 2 likely wrong**:
docker/compose 126137262246 (`docker_build` → `compile_error`: a Hugo site build failing inside
the image, which our own rule classifies by the inner error) and pydantic 128410383632
(`regression` → `test_defect`: the PR's new GC tests are wrong for GraalPy). These are flags in
`data/label_flags.jsonl` for the human review (`triagebench review` shows them first, without
saying which label the reviewers preferred); no label was changed. The judges split on which
model was closer: Sonnet 7, Haiku 5, both defensible 2.

**Label noise is as big as the model gaps.** If just those 2 labels were corrected, Sonnet's
axis A would move 91.3% → 89.3% and its axis B 88.0% → 90.0%; Haiku B2 axis B 82.0% → 82.7%.
That is why the label review comes before any further model comparison.

**Total real API spend so far: $4.33** (`scripts/spend.py`).

## Superseded: prompt v2, three runs each, before the diff-leak fix

`results/20261108-034832-repeats-dev.json` (from `scripts/repeats.py`), over the canonical v2
runs `results/20261108-0332*-b1-dev.json`, `…033400-b2-dev.json`, `…033615-b3-dev.json` and the
fresh repeats `*-dev-r1.json`, `*-dev-r2.json`. Same 50 dev cases, same labels (0 verified),
`claude-haiku-5-5`, effort medium, code at commit `2b6184f` plus the uncommitted v2 changes.
The flip rate is the share of cases whose correctness differs between the three runs.

| System | Runs | Axis A mean (range) | Axis B mean (range) | Flip rate A | Flip rate B |
|---|---|---|---|---|---|
| B0 regex rules | 1 (deterministic) | 42.0% | 30.0% | — | — |
| B1 single call | 3 | 89.3% (88–90%) | 76.0% (74–78%) | 4% | 22% |
| B2 agent | 3 | **92.7%** (92–94%) | **84.0%** (82–88%) | 8% | 12% |
| B3 agent + retrieval | 3 | 92.0% (90–94%) | 78.7% (74–82%) | 14% | 20% |

Paired comparisons on each case's mean correctness over the three runs (exact McNemar on the
cases where one system is right more often):

| Comparison | Axis | Δ mean accuracy | Fixed / broken | p | Verdict |
|---|---|---|---|---|---|
| B2 vs B1 | A | +3.3 pts | 4 / 2 | 0.688 | not significant |
| B2 vs B1 | B | +8.0 pts | 10 / 4 | 0.180 | not significant |
| B3 vs B2 | A | −0.7 pts | 2 / 3 | 1.000 | not significant |
| B3 vs B2 | B | −5.3 pts | 3 / 9 | 0.146 | not significant |

**One run can lie.** On the single canonical v2 run, B3 vs B2 on axis B was 0 fixed / 7 broken,
p = 0.016, which looked like "retrieval significantly hurts". Over three runs it is 3 / 9,
p = 0.146. A McNemar test treats each case's outcome as fixed; with a non-deterministic model
it isn't, so one run understates the noise. All later comparisons use repeats.

### Agent v1 → v2 (what changed and what it did)

v2 = two fixes from the v1 error analysis: (1) a budget-aware loop: after every tool round the
model sees how many turns and input tokens it has left, and when the next turn could overrun the
60,000-token budget, that turn is made final with tools switched off (`tool_choice: none`);
(2) the retrieval step frames similar cases as hints, never as labels to copy. v1 stays
reproducible: `TRIAGEBENCH_PROMPT_VERSION=v1 triagebench eval ... --replay-only` replayed every
v1 number exactly from the recordings, with no API calls (`results/*-dev-v1replay.json`).

| System | Budget failures v1 → v2 | Axis B, one run v1 → v2 | v1 vs v2 paired (axis B) |
|---|---|---|---|
| B2 agent | 1 → 0 | 80.0% → 88.0% | 5 fixed / 1 broken, p = 0.219 |
| B3 agent + retrieval | 3 → 0 | 72.0% → 74.0% | 5 fixed / 4 broken, p = 1.000 |

The budget fix does what it was meant to: the docker/compose case that used to crash now
answers `environment` (correct) at confidence 0.45. Whether v2 is *more accurate* overall isn't
established: the per-run differences are inside the run-to-run noise above. In the 7 cases B3
"broke" on the single v2 run it cited **no** similar case, so the hint wording stops label
copying; the remaining B3 deficit looks like distraction plus noise, not copying.

**Cost so far:** $1.032 in real API calls (live calls only; replays are free), from
`scripts/spend.py`. Prompt caching made repeat 2 cheaper than repeat 1 (B1 $0.0006 vs $0.0013 per
triage) because the shared prompt prefix was still cached; outputs are still freshly generated.

## Superseded: model ablation before the diff-leak fix

`results/20261108-040054-model-ablation-dev.json` (from `scripts/compare_runs.py`): the same
B2 agent, prompt v2, effort medium, budgets 8 turns / 60,000 input tokens, three runs each on the
same 50 dev cases. Haiku runs: `…033400-b2-dev.json`, `…034122-b2-dev-r1.json`,
`…034613-b2-dev-r2.json`. Sonnet runs: `…035746-b2-dev-sonnet-r0.json`,
`…035914-b2-dev-sonnet-r1.json`, `…040044-b2-dev-sonnet-r2.json`. Only the output cap differed
(Sonnet 8,000 tokens; Haiku never reached its 4,000). Refusals would have been recorded as
failures, not routed to another model; there were none.

| | Haiku 5.5 | Sonnet 5.5 |
|---|---|---|
| Axis A: what failed, mean (range) | **92.7%** (92–94%) | 91.3% (88–94%) |
| Axis B: why, mean (range) | 84.0% (82–88%) | **88.0%** (86–90%) |
| Flip rate, axis B | 12% | **4%** |
| Answers "unknown" | 11% | 5% |
| Cost per triage (mean over runs, with caching) | **$0.0020** | $0.0208 |
| Latency P50 | 7.2 s | 6.5 s |
| Model calls / tool calls per triage | 3.2 / 4.3 | 2.8 / 3.3 |
| Errors | 0 | 0 |

Paired on per-case means: axis B **+4.0 pts, 7 fixed / 2 broken, p = 0.180**; axis A −1.3 pts,
3 / 5, p = 0.727. Neither is significant at N = 50.

Reading it: Sonnet is about **10× the price** for a gain we can't distinguish from noise yet. Its
clearest advantages are behavioural: it is three times more consistent between runs (4% vs 12%
flip rate on axis B), abstains half as often, and gets there with fewer tool calls. Its first run
cost $0.034 per triage and later runs ~$0.014, because repeats read the shared prompt prefix from
the cache. The Sonnet comparison cost $3.10 in real API calls (smoke test included).

## History: prompt v1, single run (dev, preliminary)

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
> failures from 18 public repos it reached 93% failure-type and 82% root-cause accuracy (mean of
> 3 runs) vs 42% / 30% for a regex baseline, at ~$0.002 per triage, with every quoted evidence
> line verified against the log; repeated runs and paired McNemar tests showed a single LLM call
> captured most of the gain, and that a 10× pricier model's +6 points was not yet significant.
