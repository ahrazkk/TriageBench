#!/usr/bin/env bash
# Git-Bash/MSYS helper: run a command in the WSL environment without path mangling.
# Maps this repo's Git-Bash path (/c/Users/...) to its WSL path (/mnt/c/Users/...).
export MSYS_NO_PATHCONV=1
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec wsl.exe -d Ubuntu-24.04 -u root -- bash "/mnt${REPO}/scripts/dev.sh" "$@"
