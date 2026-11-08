#!/usr/bin/env bash
# Run any command inside the TriageBench WSL environment, from the repo root.
#
#   From PowerShell:  .\scripts\wsl.ps1 pytest -q
#   Inside WSL:       bash scripts/dev.sh pytest -q
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${TRIAGEBENCH_VENV:-/root/.venvs/triagebench}"

export VIRTUAL_ENV="$VENV"
export UV_PROJECT_ENVIRONMENT="$VENV"
export PATH="$VENV/bin:$HOME/.local/bin:$PATH"
export PYTHONUNBUFFERED=1

# Postgres doesn't auto-start in WSL; start it quietly if it's down.
if command -v pg_isready >/dev/null && ! pg_isready -q 2>/dev/null; then
  service postgresql start >/dev/null 2>&1 || true
fi

# GitHub token: .env wins; otherwise borrow the Windows gh CLI login (read-only use only).
if [ -z "${GITHUB_TOKEN:-}" ] && ! grep -qs '^GITHUB_TOKEN=.' "$REPO/.env"; then
  GH_EXE="/mnt/c/Program Files/GitHub CLI/gh.exe"
  if [ -x "$GH_EXE" ]; then
    GITHUB_TOKEN="$("$GH_EXE" auth token 2>/dev/null | tr -d '\r')" && export GITHUB_TOKEN
  fi
fi

cd "$REPO"
exec "$@"
