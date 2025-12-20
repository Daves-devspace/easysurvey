#!/bin/sh
set -e

log() {
  echo "$(date +'%Y-%m-%d %H:%M:%S') - $1"
}

# -------------------------
# Map environment variables
# -------------------------
# Your Django settings use POSTGRES_* but docker-compose uses DB_*
# Let's support both
DB_HOST="${DB_HOST:-${POSTGRES_HOST}}"
DB_PORT="${DB_PORT:-${POSTGRES_PORT}}"
DB_NAME="${DB_NAME:-${POSTGRES_DB}}"
DB_USER="${DB_USER:-${POSTGRES_USER}}"

# Ensure at least one set is defined
: "${DB_HOST:?Either DB_HOST or POSTGRES_HOST is required}"
: "${DB_PORT:?Either DB_PORT or POSTGRES_PORT is required}"

# -------------------------
# Wait for DB
# -------------------------
log "Waiting for the database at $DB_HOST:$DB_PORT..."
while ! nc -z "$DB_HOST" "$DB_PORT"; do
  sleep 1
done
log "Database is ready!"

# -------------------------
# Optional: wait for Redis
# -------------------------
if [ "$WAIT_FOR_REDIS" = "true" ]; then
  log "Waiting for Redis at $REDIS_HOST:$REDIS_PORT..."
  while ! nc -z "$REDIS_HOST" "$REDIS_PORT"; do
    sleep 1
  done
  log "Redis is ready!"
fi

# -------------------------
# Check Django can connect to DB
# -------------------------
log "Checking database connection..."
if ! python manage.py check --database default; then
  log "❌ Database connection failed"
  exit 1
fi
log "✅ Database connection verified"

# -------------------------
# Firebase Service Worker Generation
# -------------------------
log "🚀 Preparing container startup..."

STATIC_ROOT=${STATIC_ROOT:-/app/staticfiles}
TEMPLATE_PATH=${TEMPLATE_PATH:-static/assets/js/utils/firebase-messaging-sw.js}
OUTPUT_PATH="${STATIC_ROOT}/firebase-messaging-sw.js"

# Ensure static directory exists
mkdir -p "$STATIC_ROOT"

# Validate and generate Firebase SW if template exists
if [ -f "$TEMPLATE_PATH" ]; then
  if [ -n "$FIREBASE_API_KEY" ] && [ -n "$FIREBASE_PROJECT_ID" ]; then
    : "${FIREBASE_AUTH_DOMAIN:=${FIREBASE_PROJECT_ID}.firebaseapp.com}"
    : "${FIREBASE_STORAGE_BUCKET:=${FIREBASE_PROJECT_ID}.appspot.com}"

    log "📝 Rendering firebase-messaging-sw.js from template..."
    envsubst < "$TEMPLATE_PATH" > "$OUTPUT_PATH"
    chmod 644 "$OUTPUT_PATH"
    log "✅ firebase-messaging-sw.js written to ${OUTPUT_PATH}"
  else
    log "⚠️ Firebase env vars not set, skipping SW generation"
  fi
else
  log "⚠️ No firebase SW template found at ${TEMPLATE_PATH}"
fi

# -------------------------
# Run migrations
# -------------------------
log "Checking for unapplied migrations..."
PENDING=$(python manage.py showmigrations --plan | grep '\[ \]' || true)
if [ -n "$PENDING" ]; then
  log "Applying pending migrations..."
  if ! python manage.py migrate --noinput; then
    log "❌ Migration failed — check for conflicts"
    exit 1
  fi
  log "✅ Migrations applied successfully"
else
  log "✅ No unapplied migrations"
fi

# -------------------------
# Collect static files
# -------------------------
log "Collecting static files..."
python manage.py collectstatic --noinput
log "✅ Static files collected successfully"

# -------------------------
# Start the main process
# -------------------------
log "Starting service..."
log "Executing: $@"

# Default command if none is provided
if [ $# -eq 0 ]; then
  set -- gunicorn GGI.wsgi:application --bind 0.0.0.0:8000
fi

exec "$@"