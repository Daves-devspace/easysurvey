#!/bin/sh
set -e

log() { echo "$(date +'%Y-%m-%d %H:%M:%S') - $1"; }

RUN_MODE="${RUN_MODE:-web}"  # web, worker, beat
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"
RUN_COLLECT_STATIC="${RUN_COLLECT_STATIC:-true}"

is_truthy() {
  value=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
  case "$value" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

# -------------------------
# DB environment mapping
# -------------------------
DB_HOST="${DB_HOST:-${POSTGRES_HOST}}"
DB_PORT="${DB_PORT:-${POSTGRES_PORT}}"
DB_NAME="${DB_NAME:-${POSTGRES_DB}}"
DB_USER="${DB_USER:-${POSTGRES_USER}}"
: "${DB_HOST:?DB_HOST or POSTGRES_HOST required}"
: "${DB_PORT:?DB_PORT or POSTGRES_PORT required}"

# -------------------------
# Wait for DB
# -------------------------
log "Waiting for DB at $DB_HOST:$DB_PORT..."
while ! nc -z "$DB_HOST" "$DB_PORT"; do sleep 1; done
log "DB is ready!"

# -------------------------
# Optional Redis
# -------------------------
if [ "$WAIT_FOR_REDIS" = "true" ]; then
  log "Waiting for Redis at $REDIS_HOST:$REDIS_PORT..."
  while ! nc -z "$REDIS_HOST" "$REDIS_PORT"; do sleep 1; done
  log "Redis is ready!"
fi

# -------------------------
# Check DB connection
# -------------------------
log "Checking database connection..."
python manage.py check --database default
log "✅ Database connection verified"

# -------------------------
# Instance info
# -------------------------
log "RUN_MODE=$RUN_MODE, INSTANCE_NAME=${INSTANCE_NAME:-default}, REDIS_DB=${REDIS_DB:-0}"

# -------------------------
# Unified static folder
# -------------------------
STATIC_ROOT="${STATIC_ROOT:-/app/staticfiles}"
MEDIA_ROOT="${MEDIA_ROOT:-/app/media}"

mkdir -p "$STATIC_ROOT" "$MEDIA_ROOT" 2>/dev/null || true
# Don't chmod mounted volumes

# -------------------------
# Web-only operations
# -------------------------
if [ "$RUN_MODE" = "web" ]; then
  log "🟢 Running WEB-only startup steps"

  # # Firebase SW generation
  # TEMPLATE_PATH="${TEMPLATE_PATH:-static/assets/js/utils/firebase-messaging-sw.js}"
  # OUTPUT_PATH="$STATIC_ROOT/firebase-messaging-sw.js"
  # if [ -f "$TEMPLATE_PATH" ] && [ -n "$FIREBASE_API_KEY" ] && [ -n "$FIREBASE_PROJECT_ID" ]; then
  #     log "📝 Generating firebase SW..."
  #     envsubst < "$TEMPLATE_PATH" > "$OUTPUT_PATH"
  #     chmod 644 "$OUTPUT_PATH"
  #     log "✅ Firebase SW written to $OUTPUT_PATH"
  # else
  #     log "⏭ Skipping Firebase SW generation"
  # fi

  # Migrations (django-tenants requires migrate_schemas, not plain migrate)
  if is_truthy "$RUN_MIGRATIONS"; then
    log "Applying shared schema migrations..."
    python manage.py migrate_schemas --shared --noinput
    log "✅ Shared schema migrations applied"

    DEFAULT_DEMO_DOMAIN="demo.localhost"
    if [ -n "$TENANT_DEV_BASE_DOMAIN" ]; then
      DEFAULT_DEMO_DOMAIN="demo.${TENANT_DEV_BASE_DOMAIN#.}"
    fi

    log "Bootstrapping public tenant (idempotent)..."
    python manage.py create_public_tenant \
      --domain "${PUBLIC_TENANT_DOMAIN:-localhost}" \
      --name "${PUBLIC_TENANT_NAME:-PlotSync Public}" \
      --admin-email "${PUBLIC_TENANT_EMAIL:-admin@plotsync.com}" \
      --create-demo \
      --demo-domain "${DEMO_TENANT_DOMAIN:-$DEFAULT_DEMO_DOMAIN}" \
      --superadmin-username "${SUPERADMIN_USERNAME:-}" \
      --superadmin-email "${SUPERADMIN_EMAIL:-}" \
      --superadmin-password "${SUPERADMIN_PASSWORD:-}"
    log "✅ Public tenant ready"
  else
    log "⏭ Skipping migrations (RUN_MIGRATIONS=$RUN_MIGRATIONS)"
  fi

  # Collect static files
  if is_truthy "$RUN_COLLECT_STATIC"; then
    log "Collecting static files..."
    python manage.py collectstatic --noinput
    log "✅ Static files collected"
  else
    log "⏭ Skipping collectstatic (RUN_COLLECT_STATIC=$RUN_COLLECT_STATIC)"
  fi
else
  log "⏭ Skipping web-only steps"
fi

# -------------------------
# Start main process
# -------------------------
log "Starting main process..."
if [ $# -eq 0 ]; then
  case "$RUN_MODE" in
    web) set -- daphne -b 0.0.0.0 -p 8000 GGI.asgi:application ;;
    worker) set -- celery -A GGI worker -Q celery_${INSTANCE_NAME:-default} --loglevel=info ;;
    beat) set -- celery -A GGI beat -S django --loglevel=info ;;
    *) log "❌ Unknown RUN_MODE $RUN_MODE"; exit 1 ;;
  esac
fi
exec "$@"
