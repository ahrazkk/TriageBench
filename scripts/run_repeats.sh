#!/usr/bin/env bash
# Repeats 1 and 2 of B1-B3 on dev (repeat 0 is the canonical run). Each repeat makes fresh calls.
set -uo pipefail
for k in 1 2; do
  triagebench eval --system b1,b2,b3 --split dev --concurrency 4 --repeat "$k"
done
echo "REPEATS DONE"
