#!/usr/bin/env bash
# One-time setup inside WSL Ubuntu-24.04 (run as root): PostgreSQL 16 + pgvector, a database and user.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql-16 postgresql-16-pgvector >/dev/null
service postgresql start
su postgres -c "psql -tc \"SELECT 1 FROM pg_roles WHERE rolname='triage'\" | grep -q 1 || psql -c \"CREATE ROLE triage LOGIN PASSWORD 'triage' SUPERUSER\""
su postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='triagebench'\" | grep -q 1 || createdb -O triage triagebench"
su postgres -c "psql -tc \"SELECT 1 FROM pg_database WHERE datname='triagebench_test'\" | grep -q 1 || createdb -O triage triagebench_test"
su postgres -c "psql -d triagebench -c 'CREATE EXTENSION IF NOT EXISTS vector'"
su postgres -c "psql -d triagebench_test -c 'CREATE EXTENSION IF NOT EXISTS vector'"
su postgres -c "psql -d triagebench -tc \"SELECT extversion FROM pg_extension WHERE extname='vector'\""
echo "postgres ready"
