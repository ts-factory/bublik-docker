#!/usr/bin/env bash

get_container_name() {
  local service="$1"
  docker ps --format '{{.Names}}' | grep "${COMPOSE_PROJECT_NAME}-${service}"
}

if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ]; then
  echo "❌ Required environment variables not set"
  echo "Required: DB_USER, DB_NAME"
  exit 1
fi

COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-bublik}
POSTGRES_CONTAINER=$(get_container_name "db")
DJANGO_CONTAINER=$(get_container_name "django")

echo "Backing up migrations folder..."
docker cp $DJANGO_CONTAINER:/app/bublik/bublik/data/migrations ./backups/migrations_backup

echo "Truncating django_migrations table..."
docker exec $POSTGRES_CONTAINER psql -U "$DB_USER" -d "$DB_NAME" -c 'TRUNCATE "django_migrations";'

echo "Running manage.py migrate --fake..."
docker exec $DJANGO_CONTAINER python manage.py migrate --fake

echo "✅ All done!"