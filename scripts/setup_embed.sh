#!/usr/bin/env bash
# Install the local embedding model's dependencies (torch + sentence-transformers) and smoke-test
# Qwen3-Embedding-0.6B on the GPU. Run inside WSL: bash scripts/dev.sh bash scripts/setup_embed.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_PROJECT_ENVIRONMENT=/root/.venvs/triagebench UV_LINK_MODE=copy
uv sync --python 3.12 --extra dev --extra embed 2>&1 | tail -3
/root/.venvs/triagebench/bin/python -c "
from triagebench.retrieval.embed import QwenEmbedder
e = QwenEmbedder()
v = e.embed_query('ModuleNotFoundError: No module named numpy')
print('embed ok', e.name, e.dim, len(v))
"
