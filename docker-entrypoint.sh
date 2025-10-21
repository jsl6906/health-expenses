#!/usr/bin/env bash
set -euo pipefail

echo "Starting container entrypoint: waiting for DB and applying migrations"

# Wait for DB to be ready using the project's test_connection helper
MAX_TRIES=${MAX_TRIES:-30}
SLEEP_SECONDS=${SLEEP_SECONDS:-2}
TRY=0

while ! python -c "from src.database_config import test_connection; import sys; sys.exit(0 if test_connection() else 1)" >/dev/null 2>&1; do
  TRY=$((TRY+1))
  if [ "$TRY" -ge "$MAX_TRIES" ]; then
    echo "Timed out waiting for DB after $MAX_TRIES attempts"
    exit 1
  fi
  echo "DB not ready yet (attempt $TRY/$MAX_TRIES). Sleeping ${SLEEP_SECONDS}s..."
  sleep $SLEEP_SECONDS
done

echo "DB is reachable. Running Alembic migrations using /app/alembic.ini..."

# Run Alembic upgrade using the environment's python (the image sets PATH to the venv)
# Use an explicit config file path so the CLI doesn't depend on the current working directory.
if ! python -m alembic -c /app/alembic.ini upgrade head; then
  echo "Alembic migration failed. Showing alembic output above. Exiting with non-zero status."
  exit 1
fi

echo "Migrations applied. Starting application..."

# Exec the container CMD (by default `python main.py`) so signals are forwarded
exec "$@"
