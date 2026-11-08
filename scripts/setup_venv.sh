#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export UV_PROJECT_ENVIRONMENT=/root/.venvs/triagebench
export UV_LINK_MODE=copy
uv sync --python 3.12 --extra dev "$@" 2>&1 | tail -5
/root/.venvs/triagebench/bin/python -c "import anthropic, mcp, psycopg; from importlib.metadata import version as v; print('anthropic', v('anthropic'), 'mcp', v('mcp'), 'psycopg', v('psycopg'))"
