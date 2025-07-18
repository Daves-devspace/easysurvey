#!/bin/bash
set -e

log() {
  echo "$(date +'%Y-%m-%d %H:%M:%S') - $1"
}

log "Waiting for the database at $DB_HOST:$DB_PORT..."
while ! nc -z $DB_HOST $DB_PORT; do
  sleep 1
done
log "Database is ready!"

if [ "$WAIT_FOR_REDIS" = "true" ]; then
  log "Waiting for Redis at $REDIS_HOST:$REDIS_PORT..."
  while ! nc -z $REDIS_HOST $REDIS_PORT; do
    sleep 1
  done
  log "Redis is ready!"
fi

# Only run migrations in main web container
if [ "$RUN_MIGRATIONS" = "true" ]; then
  log "Checking for unapplied migrations..."
  
  # Test database connection first
  if ! python manage.py check --database default; then
    log "❌ Database connection check failed"
    exit 1
  fi
  
  PENDING=$(python manage.py showmigrations --plan | grep '\[ \]' || true)
  if [ -n "$PENDING" ]; then
    log "Applying pending migrations..."
    if ! python manage.py migrate --noinput; then
      log "❌ Migration failed — this is a critical error"
      exit 1
    fi
    log "✅ Migrations applied successfully"
  else
    log "No unapplied migrations — skipping."
  fi

  log "Collecting static files..."
  if ! python manage.py collectstatic --noinput; then
    log "❌ Static file collection failed"
    exit 1
  fi
  log "✅ Static files collected successfully"
fi

log "Starting service..."
log "Executing: $@"

if [ $# -eq 0 ]; then
  set -- gunicorn GGI.wsgi:application --bind 0.0.0.0:8000
fi

exec "$@"