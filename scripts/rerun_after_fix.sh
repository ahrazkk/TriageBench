#!/usr/bin/env bash
# Re-measure every LLM system after the PR-diff leak fix. Unchanged cases replay from the
# cassettes (free); only cases whose tool output changed make fresh model calls.
set -uo pipefail
for k in 0 1 2; do
  triagebench eval --system b1,b2,b3 --split dev --concurrency 4 --repeat "$k" --tag "fix-r$k"
done
export TRIAGEBENCH_MODEL=claude-sonnet-5-5 TRIAGEBENCH_MAX_OUTPUT_TOKENS=8000
for k in 0 1 2; do
  triagebench eval --system b2 --split dev --concurrency 4 --repeat "$k" --tag "fix-sonnet-r$k"
done
echo "RERUN DONE"
