#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-.}"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "created .env from .env.example"
fi

# Prefer project venv if present
if [ -x .venv/bin/python ]; then
  PY=.venv/bin/python
elif [ -x .venv/Scripts/python.exe ]; then
  PY=.venv/Scripts/python.exe
else
  PY=python
fi

# No Postgres to hand? Fall back to a local SQLite file so the stack still
# comes up. Same models, same migrations.
if [ -z "${DATABASE_URL:-}" ] && ! grep -qE '^DATABASE_URL=.+' .env 2>/dev/null; then
  export DATABASE_URL="sqlite+aiosqlite:///./creatorloop.db"
  echo "DATABASE_URL unset — using ${DATABASE_URL}"
fi

# Schema first: a service that starts against an old schema fails in confusing
# places rather than at the point the schema is actually wrong.
echo "applying migrations…"
$PY -m alembic upgrade head

$PY -m uvicorn mcp.server:app --port 8085 --reload &
$PY -m uvicorn opportunity_finder.app:app --port 8081 --reload &
$PY -m uvicorn pipeline_manager.app:app --port 8082 --reload &
$PY -m uvicorn engagement_listener.app:app --port 8083 --reload &
$PY -m uvicorn cdr.app:app --port 8084 --reload &
# The real UI: accounts, onboarding, and the tenant-signing proxy.
# (ui_client/server.py is the keyless fixture demo and cannot sign anyone in.)
$PY -m uvicorn ui_client.app:app --port 8000 --reload &

cat <<EOF

  UI          http://localhost:8000
  sign in     http://localhost:8000/signin
  demo user   $PY scripts/seed_demo_user.py

  Finder :8081  Pipeline :8082  Engagement :8083  CDR :8084  MCP :8085
  USE_FIXTURES=${USE_FIXTURES:-0}   DATABASE_URL=${DATABASE_URL:-from .env}

EOF
wait
