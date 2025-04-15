#!/bin/bash

get_container_name() {
  local service="$1"
  docker ps --format '{{.Names}}' | grep "${COMPOSE_PROJECT_NAME}-${service}"
}

# Check required environment variables
if [ -z "$DB_USER" ] || [ -z "$DB_NAME" ]; then
  echo "❌ Required environment variables not set"
  echo "Required: DB_USER, DB_NAME"
  exit 1
fi

COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-bublik}
POSTGRES_CONTAINER=$(get_container_name "db")

create_backup() {
  local backup_dir="${1:-backups}"

  if [ -z "$POSTGRES_CONTAINER" ]; then
    echo "❌ Database container not found"
    echo "Make sure the database is running"
    exit 1
  fi

  # Ensure backup directory exists
  mkdir -p "$backup_dir"

  # Generate backup filename with timestamp
  TIMESTAMP=$(date +%Y%m%d_%H%M%S)
  DB_BACKUP_FILE="$backup_dir/db_backup_${TIMESTAMP}.sql"

  # Backup database
  echo "📝 Creating database backup..."
  if docker exec $POSTGRES_CONTAINER pg_dump \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --clean \
    --if-exists \
    --no-owner \
    --no-privileges \
    > "$DB_BACKUP_FILE"; then
    echo "✅ Database backup created at: $DB_BACKUP_FILE"
    echo "📊 Backup size: $(du -h "$DB_BACKUP_FILE" | cut -f1)"
  else
    echo "❌ Failed to create database backup"
    rm -f "$DB_BACKUP_FILE"
    exit 1
  fi

  # Optionally create compressed version
  read -p "Compress the backup file? [y/N] " compress_answer
  if [[ $compress_answer =~ ^[Yy]$ ]]; then
    COMPRESSED_FILE="${DB_BACKUP_FILE}.gz"
    echo "📝 Compressing backup..."
    if gzip -c "$DB_BACKUP_FILE" > "$COMPRESSED_FILE"; then
      echo "✅ Compressed backup created at: $COMPRESSED_FILE"
      echo "📊 Compressed size: $(du -h "$COMPRESSED_FILE" | cut -f1)"
      read -p "Remove original uncompressed file? [y/N] " remove_answer
      if [[ $remove_answer =~ ^[Yy]$ ]]; then
        rm -f "$DB_BACKUP_FILE"
        echo "✅ Original file removed"
      fi
    else
      echo "❌ Failed to compress backup"
    fi
  fi
}

restore_backup() {
  local backup_file="$1"

  if [ -z "$POSTGRES_CONTAINER" ]; then
    echo "❌ Database container not found"
    echo "Make sure the database is running"
    exit 1
  fi

  if [ -z "$backup_file" ]; then
    echo "❌ No backup file specified"
    echo "Usage: $0 restore /path/to/backup.sql[.gz]"
    exit 1
  fi

  if [ ! -f "$backup_file" ]; then
    echo "❌ Backup file not found: $backup_file"
    exit 1
  fi

  echo "⚠️ This will overwrite the current database!"
  echo "📝 Restore from: $backup_file"
  read -p "Continue? [y/N] " answer
  if [[ ! $answer =~ ^[Yy]$ ]]; then
    echo "⏭️ Restore cancelled"
    exit 0
  fi

  # Check if file is compressed
  if [[ "$backup_file" == *.gz ]]; then
    echo "🔄 Restoring from compressed backup..."
    if gunzip -c "$backup_file" | docker exec -i $POSTGRES_CONTAINER psql \
      -U "$DB_USER" \
      -d "$DB_NAME"; then
      echo "✅ Database restored successfully"
    else
      echo "❌ Failed to restore database"
      exit 1
    fi
  else
    echo "🔄 Restoring database..."
    if cat "$backup_file" | docker exec -i $POSTGRES_CONTAINER psql \
      -U "$DB_USER" \
      -d "$DB_NAME"; then
      echo "✅ Database restored successfully"
    else
      echo "❌ Failed to restore database"
      exit 1
    fi
  fi
}

list_backups() {
  local backup_dir="${1:-backups}"

  if [ ! -d "$backup_dir" ]; then
    echo "❌ Backup directory not found: $backup_dir"
    exit 1
  fi

  echo "📝 Available database backups in $backup_dir:"
  echo "----------------------------------------"
  if ls -lh "$backup_dir"/db_backup_*.sql* 2>/dev/null; then
    echo "----------------------------------------"
  else
    echo "No database backups found"
  fi
}

# Command line interface
case "$1" in
  "create")
    create_backup "${2:-backups}"
    ;;
  "restore")
    restore_backup "$2"
    ;;
  "list")
    list_backups "${2:-backups}"
    ;;
  *)
    echo "Usage: $0 {create|restore|list} [path]"
    echo "  create [dir]     Create database backup (default dir: backups)"
    echo "  restore <file>   Restore database from backup file"
    echo "  list [dir]       List available backups (default dir: backups)"
    exit 1
    ;;
esac
