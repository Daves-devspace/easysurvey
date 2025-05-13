#!/bin/bash
set -e

echo "Starting container: $0 $@"

# Wait for PostgreSQL
echo "Waiting for database at $DB_HOST:$DB_PORT..."
dockerize -wait tcp://$DB_HOST:$DB_PORT -timeout 60s
echo "Database is up."

# Optionally wait for Redis
if [ "$WAIT_FOR_REDIS" = "true" ]; then
  echo "Waiting for Redis at $REDIS_HOST:$REDIS_PORT..."
  dockerize -wait tcp://$REDIS_HOST:$REDIS_PORT -timeout 30s
  echo "Redis is up."
fi

# Migrations and collectstatic only for web
if [ "$RUN_MIGRATIONS" = "true" ]; then
  echo "Making migrations..."
  python manage.py makemigrations --noinput

  echo "Running Django migrations..."
  python manage.py migrate --noinput
fi


if [ "$RUN_COLLECT_STATIC" = "true" ]; then
  echo "Collecting static files..."
  python manage.py collectstatic --noinput
fi

# Fix media folder permissions if it exists
if [ -d "/app/media" ]; then
  echo "Setting write permission for media directory..."
  chmod -R 775 /app/media
  chown -R django:django /app/media
fi


# Start the service
exec "$@"
