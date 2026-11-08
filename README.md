# TriageBench

**An AI agent that diagnoses failing GitHub Actions jobs, and the evaluation harness that proves
whether it actually works.**

Given a failed CI job, TriageBench answers the questions an on-call engineer asks first: *what*
failed (a test, a compile error, a lint check, a dependency install…), *why and whose problem*
(a real regression, a flaky test, an environment change, a broken test, a CI config mistake),
*what's the evidence* (exact log lines, checked against the real log), *have we seen it before*
(hybrid search over past failures in pgvector), and *what to do next*.

Most agent projects never show they work. This one compares four systems on the same set of real,
hand-labelled failures, with confidence intervals and paired significance tests.

> **Status: MVP (milestones M0–M6), November 2026.** All four systems are built, tested and
> measured on 50 real dev failures. The labels are AI-drafted and awaiting human review, so the
> numbers below are **preliminary**. See [docs/RESULTS.md](docs/RESULTS.md).

## How it works

```
GitHub REST API (read-only) ─► collector: redacted log + what was visible at failure time
                                         + labeller-only hints from the future
                                          │
               data/cases.jsonl, data/labels.jsonl (git)  ·  PostgreSQL + pgvector (search index)
                                          │
        7 read-only tools (tools/impl.py) ─┬─► agent loop (Claude Haiku 5.5, budgets, repair)
                                           └─► MCP server (same tools for Claude Code & any MCP host)
                                          │
   B0 regex · B1 single call · B2 agent · B3 agent + hybrid retrieval (full-text + Qwen3 embeddings, RRF)
                                          │
   evals: accuracy, macro-F1, confusion, evidence validity, confident errors, cost, latency,
          Wilson / bootstrap 95% CIs, exact McNemar  ─►  results/*.json  ─►  docs/RESULTS.md
```

Design choices worth knowing (details and reasons in [docs/design/M0-M6.md](docs/design/M0-M6.md)):

- **No leakage from the future.** The agent only sees what existed when the job failed. Whether a
  re-run passed, and what the next green commit changed, are *labeller hints*, never model inputs.
- **Evidence is verified, not trusted.** Every quoted log line is checked verbatim against the log
  at the stated line number; one repair attempt, then it counts as a hallucination.
- **Log reduction first.** Logs here run up to 170,464 lines (P95 17,671). Tools return numbered,
  budgeted excerpts with truncation notes, and the model can search for more.
- **Logs are untrusted input.** Tool output is wrapped in `<untrusted_log>`; all tools are read-only.
- **Record / replay.** Every model call is cached by a hash of the exact request, so re-running an
  eval is free and deterministic.

## Results so far (preliminary)

50 dev failures from 18 public repos; prompt v2; each LLM system run 3 times (fresh model calls);
labels AI-drafted and not yet human-verified (2026-11-08):

| System | Model | What failed, mean (range) | Why, mean (range) | "Why" flips between runs | Cost / triage |
|---|---|---|---|---|---|
| B0 regex rules | — | 42.0% | 30.0% | — | $0 |
| B1 single LLM call | Haiku 5.5 | 89.3% (88–90%) | 74.7% (74–76%) | 24% of cases | $0.0011 |
| **B2 agent with tools** | Haiku 5.5 | **93.3%** (92–94%) | 82.0% (80–84%) | 10% | $0.0021 |
| B3 agent + retrieval | Haiku 5.5 | 91.3% (90–92%) | 78.7% (76–82%) | 16% | $0.0025 |
| B2 agent with tools | Sonnet 5.5 | 91.3% (88–94%) | **88.0%** (86–90%) | **4%** | $0.0214 |

What it shows (paired McNemar tests on each case's average over its 3 runs):

- **The LLM is the big win:** one call beats the rules by ~45 points on both axes (p < 0.001).
- **The agent is best on "what failed", but its edge over one call isn't proven at N = 50**
  (+7.3 points on "why", 11 cases fixed / 5 broken, p = 0.21). The cases it fixes are the ones
  where the deciding log line was cut from the excerpt or the diff was needed.
- **A bigger model helps a little and is much steadier, for 10× the price:** Sonnet 5.5 +6 points
  on "why" (7 fixed / 1 broken, p = 0.07), and only 4% of its answers flip between runs.
- **Retrieval didn't help** (−3.3 points vs the agent, p = 0.51): across 18 repos, past failures
  rarely repeat within a month.
- **One run can lie.** On a single run retrieval once looked *significantly* harmful (p = 0.016);
  over three runs it wasn't.
- **A leak, found and fixed.** An AI reviewer noticed that for some pull-request runs the saved
  diff included commits pushed *after* the failure (sometimes the fix). Fixed in the collector
  with a regression test, 9 cases re-snapshotted, everything re-measured: it had inflated the
  agent's "why" score by 2 points.
- **Labels matter as much as models:** correcting just 2 likely-wrong draft labels moves scores by
  up to 2 points, as much as the model gaps. Label review comes next.
- Not one hallucinated quote: every evidence line in every answer matched the log verbatim.

Retrieval alone: HNSW matched exact search on all 50 queries. The MCP server returns
byte-identical results to direct calls (stdio, ~22 ms per call). Everything so far, including
three runs of every system, the Sonnet comparison and the post-fix re-runs, cost **$4.33** in
API calls.

## Dataset

98 failed jobs collected on 2026-11-07 from 18 public repositories (pydantic, mypy, pytest,
pandas, scikit-learn, Home Assistant, Airflow, aiohttp, ruff, tokio, Vite, ESLint, Prettier, Gin,
Prometheus, golangci-lint, Kafka, Docker Compose), split by time within each repo into a
retrieval memory (`kb`, 48) and a `dev` set (50). Labels follow
[docs/LABELING_GUIDE.md](docs/LABELING_GUIDE.md): two axes, evidence lines and a root cause.
Raw logs are not committed.

## Run it

Windows + WSL2 (Ubuntu 24.04), Python 3.12 via `uv`, PostgreSQL 16 + pgvector:

```bash
bash scripts/setup_wsl.sh          # once, inside WSL: Postgres + pgvector
bash scripts/setup_venv.sh         # once: the virtualenv
echo "ANTHROPIC_API_KEY=..." >> .env
triagebench doctor                 # GitHub, database, model, GPU
triagebench eval --system b0,b1,b2,b3 --split dev
triagebench report results/<b0>.json results/<b1>.json results/<b2>.json results/<b3>.json
triagebench triage https://github.com/<owner>/<repo>/actions/runs/<id> --system b2
```

From PowerShell, prefix any command with `.\scripts\wsl.ps1`.

**In Claude Code:** the repo's `.mcp.json` registers the `triagebench` MCP server, so Claude Code
can call the same seven tools ("triage this failed run: <URL>").

## Tests

`python -m pytest -q`: 41 tests. They cover reducer properties (Hypothesis), the evidence verifier,
statistics against known values, the agent loop driven by a scripted fake model (tool errors,
budgets, repair), the GitHub client against mocked HTTP, the MCP contract (every tool over MCP ==
direct call), and pgvector leakage (no dev case or same-run case is ever retrievable). CI runs
ruff, mypy `--strict` and the tests with a `pgvector/pgvector:pg17` service container. No test
calls a paid API.

## What's next

Verify the labels; grow to 100–150 cases with a frozen test set and Cohen's κ on a blind relabel;
the live eval gate in CI; OpenTelemetry tracing and a prompt-injection flip rate; an external check
on the LogDx-CI benchmark. The full plan is [docs/PLAN.md](docs/PLAN.md).

Credits: LogDx-CI (log-reduction findings and the failure-type taxonomy), LogChunks, LogSage;
Anthropic's "Building effective agents" and "Writing effective tools for agents"; Cormack et al.
(RRF); Malkov & Yashunin (HNSW).
