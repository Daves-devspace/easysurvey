#!/bin/sh
set -e

log() {
  echo "$(date +'%Y-%m-%d %H:%M:%S') - $1"
}

# -------------------------
# Ensure required env vars for DB
# -------------------------
: "${DB_HOST:?DB_HOST is required}"
: "${DB_PORT:?DB_PORT is required}"
: "${DB_NAME:?DB_NAME is required}"
: "${DB_USER:?DB_USER is required}"
: "${DB_PASS:?DB_PASS is required}"
export PGPASSWORD="$DB_PASS"

# -------------------------
# Wait for DB
# -------------------------
log "Waiting for the database at $DB_HOST:$DB_PORT..."
while ! nc -z "$DB_HOST" "$DB_PORT"; do
  sleep 1
done
log "Database is ready!"

# Optional: wait for Redis
if [ "$WAIT_FOR_REDIS" = "true" ]; then
  log "Waiting for Redis at $REDIS_HOST:$REDIS_PORT..."
  while ! nc -z "$REDIS_HOST" "$REDIS_PORT"; do
    sleep 1
  done
  log "Redis is ready!"
fi

# -------------------------
# psql helper (idempotent)
# -------------------------
psql_exec() {
  # Usage: psql_exec "SQL HERE"
  echo "$1" | psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1
}

psql_try() {
  # Run SQL but ignore failure (useful for checks that may fail)
  echo "$1" | psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" || true
}

# -------------------------
# Schema patch runner
# - execute numbered SQL files in /app/db/patches in lexical order
# - each patch MUST be idempotent (CREATE IF NOT EXISTS / ALTER ... IF NOT EXISTS / DO $$ guard $$)
# -------------------------
PATCH_DIR=/app/db/patches

run_patches() {
  if [ ! -d "$PATCH_DIR" ]; then
    log "No DB patches directory ($PATCH_DIR) found — skipping patching."
    return
  fi

  log "Applying DB patches from $PATCH_DIR (idempotent)"
  for f in $(ls "$PATCH_DIR"/*.sql 2>/dev/null | sort); do
    log "Running patch: $(basename "$f")"
    if ! psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f "$f"; then
      log "❌ Patch failed: $f"
      exit 1
    fi
  done
  log "✅ DB patches applied"
}

# -------------------------
# Safety DB check before Django runs
# -------------------------
log "Checking database connection..."
if ! psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c '\q' 2>/dev/null; then
  log "❌ Database connection failed"
  exit 1
fi

# -------------------------
# Run patches (healing)
# -------------------------
run_patches

# -------------------------
# Optional: run fake-initial to align Django with pre-existing schema
# We attempt --fake-initial first then migrate normally.
# -------------------------
log "Checking for unapplied migrations..."
PENDING=$(python manage.py showmigrations --plan | grep '\[ \]' || true)
if [ -n "$PENDING" ]; then
  log "Applying pending migrations (fake-initial attempt)..."
  # Try a safe fake-initial first (idempotent)
  if ! python manage.py migrate --fake-initial --noinput; then
    log "⚠️ fake-initial failed, will attempt normal migrate now"
  fi

  log "Running normal migrate..."
  if ! python manage.py migrate --noinput; then
    log "❌ Migration failed — please check DB and migration conflicts"
    exit 1
  fi
  log "✅ Migrations applied successfully"
else
  log "No unapplied migrations — skipping migrate."
fi

# -------------------------
# Collect static files
# -------------------------
log "Collecting static files..."
if ! python manage.py collectstatic --noinput; then
  log "❌ Static file collection failed"
  exit 1
fi
log "✅ Static files collected successfully"

# -------------------------
# Start the main process
# -------------------------
log "Starting service..."
log "Executing: $@"

if [ $# -eq 0 ]; then
  set -- gunicorn GGI.wsgi:application --bind 0.0.0.0:8000
fi

exec "$@"
