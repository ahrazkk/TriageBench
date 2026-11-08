#!/usr/bin/env bash
# Model ablation: the B2 agent (prompt v2) on Claude Sonnet 5.5, three runs on the dev split.
# Same prompts, effort and budgets as the Haiku runs; only the output cap is raised (8,000 tokens)
# because a bigger model may think longer before answering (Haiku never hit its 4,000 cap).
set -uo pipefail
export TRIAGEBENCH_MODEL=claude-sonnet-5-5
export TRIAGEBENCH_MAX_OUTPUT_TOKENS=8000
for k in 0 1 2; do
  triagebench eval --system b2 --split dev --concurrency 4 --repeat "$k" --tag "sonnet-r$k"
done
echo "SONNET DONE"
