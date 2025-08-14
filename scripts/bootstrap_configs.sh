#!/usr/bin/env bash

INTERACTIVE=true

while getopts "y" opt; do
  case $opt in
  y)
    INTERACTIVE=false
    ;;
  \?)
    echo "Invalid option: -$OPTARG" >&2
    exit 1
    ;;
  esac
done

shift $((OPTIND - 1))

if [ "$#" -lt 4 ]; then
  echo "❌ Missing required arguments"
  echo "Usage: $0 [-y] <api_url> <email> <password> <config_dir>"
  echo "Options:"
  echo "  -y    Non-interactive mode (skip confirmation)"
  exit 1
fi

API_URL="$1"
EMAIL="$2"
PASSWORD="$3"
CONFIG_DIR="$4"
LOGS_URL="$5"
PROJECT_NAME="ts-factory"
PROJECT_ID=""

if [ ! -d "$CONFIG_DIR" ]; then
  echo "❌ Config directory not found: $CONFIG_DIR"
  exit 1
fi

echo "📝 Using config directory: $CONFIG_DIR"
echo "📝 Using login $EMAIL"

if $INTERACTIVE; then
  echo "📝 Do you want to bootstrap configs? [y/N]"
  read -p "Continue? [y/N] " answer
  if [[ ! $answer =~ ^[Yy]$ ]]; then
    echo "⏭️ Configs bootstrap skipped"
    exit 0
  fi
fi

process_json_file() {
  local input_file="$1"
  local processed_content

  if [ ! -f "$input_file" ]; then
    echo "⚠️ Input file not found: $input_file"
    return 1
  fi

  processed_content=$(cat "$input_file" | sed "s|@@LOGS_URL@@|${LOGS_URL}|g")

  echo "$processed_content"
}

config_exists() {
  local name=$1
  local type=$2
  local project_id=$3
  local response

  response=$(curl -s "$API_URL/api/v2/config/" -b ./tmp/cookies.txt)

  if command -v jq >/dev/null 2>&1; then
    echo "$response" | jq -e ".[] | select(.name == \"$name\" and .type == \"$type\" and .project == $project_id)" >/dev/null
  else
    # Fallback if jq is not available - more complex grep pattern
    echo "$response" | grep -A5 -B5 "\"name\": \"$name\"" | grep -A3 -B3 "\"type\": \"$type\"" | grep -q "\"project\": $project_id"
  fi
  return $?
}

get_project_id() {
  local project_name=$1
  local response
  response=$(curl -s "$API_URL/api/v2/projects/" -b ./tmp/cookies.txt)
  if command -v jq >/dev/null 2>&1; then
    echo "$response" | jq -r ".[] | select(.name == \"$project_name\") | .id"
  else
    # Fallback if jq is not available - extract ID using grep and sed
    echo "$response" | grep -A1 -B1 "\"name\": \"$project_name\"" | grep "\"id\":" | sed 's/.*"id": *\([0-9]*\).*/\1/'
  fi
}

project_exists() {
  local project_name=$1
  local project_id
  project_id=$(get_project_id "$project_name")
  if [ -n "$project_id" ] && [ "$project_id" != "null" ]; then
    PROJECT_ID="$project_id"
    return 0
  else
    return 1
  fi
}

create_project() {
  if project_exists "$PROJECT_NAME"; then
    echo "⏭️ Project '$PROJECT_NAME' already exists (ID: $PROJECT_ID), skipping creation..."
    return 0
  fi

  echo "📝 Creating project '$PROJECT_NAME'..."

  response=$(curl -s "$API_URL/api/v2/projects/" \
    -H 'content-type: application/json' \
    -b ./tmp/cookies.txt \
    --data-raw "{\"name\":\"$PROJECT_NAME\"}")

  if echo "$response" | grep -q "id"; then
    if command -v jq >/dev/null 2>&1; then
      PROJECT_ID=$(echo "$response" | jq -r '.id')
    else
      PROJECT_ID=$(echo "$response" | grep -o '"id": *[0-9]*' | sed 's/"id": *//')
    fi

    echo "✅ Successfully created project '$PROJECT_NAME' (ID: $PROJECT_ID)"

  else
    echo "❌ Failed to create project '$PROJECT_NAME'"
    echo "Response: $response"

    exit 1
  fi
}

create_config() {
  local type=$1
  local name=$2
  local file=$3

  if [ ! -f "$file" ]; then
    echo "⚠️ Config file not found: $file, skipping..."
    return 0
  fi

  if config_exists "$name" "$type" "$PROJECT_ID"; then
    echo "⏭️ Config '$name' ($type) already exists for project $PROJECT_ID, skipping..."
    return 0
  fi

  content=$(process_json_file "$file")

  if [ $? -ne 0 ]; then
    echo "❌ Failed to process $file"
    exit 1
  fi

  echo "📝 Creating $type config '$name' from $file for project ID $PROJECT_ID..."

  response=$(curl -s "$API_URL/api/v2/config/" \
    -H 'Content-Type: application/json' \
    -b ./tmp/cookies.txt \
    --data-raw "{
      \"type\": \"$type\",
      \"name\": \"$name\",
      \"description\": \"$name Configuration\",
      \"is_active\": true,
      \"content\": $content,
      \"project\": $PROJECT_ID
    }")

  if echo "$response" | grep -q "id"; then
    echo "✅ Successfully created $name config"
  else
    echo "❌ Failed to create $name config"
    echo "Response: $response"
    exit 1
  fi
}

config_names=("report" "meta" "references" "per_conf")
config_files=("report.json" "meta.json" "references.json" "per_conf.json")

create_project

if [ -z "$PROJECT_ID" ]; then
  echo "❌ Failed to get project ID"
  exit 1
fi

for i in "${!config_names[@]}"; do
  name="${config_names[$i]}"
  file="$CONFIG_DIR/${config_files[$i]}"
  type="global"
  if [ "$name" = "report" ]; then
    type="report"
  fi
  create_config "$type" "$name" "$file"
done

echo "✅ All configs processed successfully!"

