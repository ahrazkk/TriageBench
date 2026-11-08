#!/usr/bin/env bash
# Git-Bash/MSYS helper (used by Claude): run a command in the WSL env without path mangling.
export MSYS_NO_PATHCONV=1
exec wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/c/Users/1kibr/Desktop/ResumeProjects/TriageBench/scripts/dev.sh "$@"
