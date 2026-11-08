# Learning log

Two or three lines per milestone: what surprised us, with the number.

## M0–M1 (2026-11-07)

- GitHub's `/actions/runs` listing returns the *latest attempt* of each run, so a flaky failure
  that passed on re-run shows up as a **success** with `run_attempt >= 2`. To find flaky
  candidates you have to look at successes and fetch attempt 1's jobs. Only 6 of 98 collected
  cases came that way: flaky failures are rare in a random sample, so the class needs targeted
  collection (M7).
- Most of the collector's time wasn't API latency (0.2–1 s per call) but inspecting re-run
  candidates whose first attempt was *cancelled*, not failed (~3 s per jobs call on big matrices).
  Capping candidates per repo and running 4 repos in parallel cut collection from ~1 hour to ~15 min.
- Hypothesis found a reducer bug in seconds: collapsing repeated lines could drop the log's last
  line when it duplicated the line before it (`['ok', 'ok']`).

## M3–M6 (2026-11-08)

- Labelling surfaced gaps in the taxonomy before any model saw it: security scanners and
  PR-policy gates (missing label, draft PR, release notes) had no category, and the guide and the
  prompt disagreed on errors *inside* `docker build`. Fixed in labels_v1 before the first eval, so
  no measured prompt changed.
- The pgvector test rebuilt the `chunks` table in the real database: running `pytest` silently
  wiped the index. Tests now use their own database (`triagebench_test`).
- Some CI logs have single lines over 100,000 characters (minified JS): embedding them ran the
  6 GB GPU out of memory. Every consumer of log lines now caps them at 400 characters.
- Smoke test (3 dev cases): the agent used parallel tool calls (overview, excerpt, history, diff
  in one turn) and finished in 3 model turns for ~$0.0016; prompt caching served ~9–11K input
  tokens per agent run from cache. The single call (B1) *writes* its whole prompt to the cache
  (1.25× price) and never reads it back: auto-caching only pays when a prefix is reused.
- First full dev run (50 cases, Haiku 5.5, $0.30 for 150 triages): one LLM call beat the regex
  rules by 46 points on both axes (p < 0.001), but the agent's +4 over the single call was not
  significant (6 fixed, 4 broken; p = 0.75 on axis B). The single call's misses were mostly an
  honest "unknown" when the deciding line had been cut from the excerpt; the agent's fixes were
  exactly those cases. Lesson: tools pay off on the cases where the context is missing, and you
  need far more than 50 cases to prove it.
- Retrieval *hurt* a little (−8 points on axis B, p = 0.29): with 18 repos and 30 days of data,
  failures rarely repeat, and once the agent copied the label of a "similar" case. Retrieval
  needs a memory with real repeats, and the prompt must say similar cases are hints, not answers.
- The 60K input-token budget counts every re-sent turn, so on 100K-line logs an agent that keeps
  searching runs out (1 of 50 for B2, 3 of 50 for B3, where retrieval output adds tokens).
