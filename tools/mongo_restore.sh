#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/mongo_restore.sh --archive FILE [options]

Options:
  --archive FILE        Input archive path (.archive.gz) (required)
  --env-file PATH       Env file for docker compose (default: .env)
  --compose-file PATH   Docker Compose file (default: docker/docker-compose.yml)
  --service NAME        Mongo service name in compose (default: mongo)
  --username USER       Mongo username (default: .env -> shell env -> root)
  --password PASS       Mongo password (default: .env -> shell env)
  --auth-db DB          Mongo auth database (default: .env -> shell env -> admin)
  --db DB               Target database for restore (default: jobcenter)
  --all-databases       Restore all namespaces from archive (ignores --db)
  --drop                Drop collections before restoring documents
  --help                Show help
EOF
}

ARCHIVE_FILE=""
COMPOSE_FILE="docker/docker-compose.yml"
ENV_FILE=".env"
SERVICE="mongo"
USERNAME=""
PASSWORD=""
AUTH_DB=""
DB_NAME="jobcenter"
ALL_DATABASES="false"
DROP_COLLECTIONS="false"
USERNAME_FROM_ARG="false"
PASSWORD_FROM_ARG="false"
AUTH_DB_FROM_ARG="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive) ARCHIVE_FILE="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --username) USERNAME="$2"; USERNAME_FROM_ARG="true"; shift 2 ;;
    --password) PASSWORD="$2"; PASSWORD_FROM_ARG="true"; shift 2 ;;
    --auth-db) AUTH_DB="$2"; AUTH_DB_FROM_ARG="true"; shift 2 ;;
    --db) DB_NAME="$2"; shift 2 ;;
    --all-databases) ALL_DATABASES="true"; shift ;;
    --drop) DROP_COLLECTIONS="true"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${ARCHIVE_FILE}" ]]; then
  echo "--archive is required" >&2
  usage
  exit 2
fi

if [[ ! -f "${ARCHIVE_FILE}" ]]; then
  echo "Archive file not found: ${ARCHIVE_FILE}" >&2
  exit 1
fi

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "Compose file not found: ${COMPOSE_FILE}" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Env file not found: ${ENV_FILE}" >&2
  echo "Pass --env-file PATH or create .env in repository root." >&2
  exit 1
fi

read_env_value() {
  local key="$1"
  local env_file="$2"
  awk -F= -v key="${key}" '
    function trim(s) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", s)
      return s
    }
    function unquote(s) {
      if (s ~ /^".*"$/ || s ~ /^'\''.*'\''$/) {
        return substr(s, 2, length(s) - 2)
      }
      return s
    }

    # Skip comments and empty lines.
    /^[[:space:]]*($|#)/ { next }

    {
      current_key = trim($1)
      if (current_key != key) {
        next
      }

      # Keep everything after first "=" to preserve values containing "=".
      value = $0
      sub(/^[^=]*=/, "", value)
      value = trim(value)
      print unquote(value)
      exit
    }
  ' "${env_file}"
}

if [[ "${USERNAME_FROM_ARG}" != "true" ]]; then
  USERNAME="$(read_env_value "MONGO_INITDB_ROOT_USERNAME" "${ENV_FILE}")"
  if [[ -z "${USERNAME}" ]]; then
    USERNAME="${MONGO_INITDB_ROOT_USERNAME:-}"
  fi
  USERNAME="${USERNAME:-root}"
fi

if [[ "${PASSWORD_FROM_ARG}" != "true" ]]; then
  PASSWORD="$(read_env_value "MONGO_INITDB_ROOT_PASSWORD" "${ENV_FILE}")"
  if [[ -z "${PASSWORD}" ]]; then
    PASSWORD="${MONGO_INITDB_ROOT_PASSWORD:-}"
  fi
fi

if [[ "${AUTH_DB_FROM_ARG}" != "true" ]]; then
  AUTH_DB="$(read_env_value "MONGO_AUTH_DB" "${ENV_FILE}")"
  if [[ -z "${AUTH_DB}" ]]; then
    AUTH_DB="${MONGO_AUTH_DB:-}"
  fi
  AUTH_DB="${AUTH_DB:-admin}"
fi

compose_cmd=(docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}")

if [[ -z "${PASSWORD}" ]]; then
  echo "Mongo password is empty. Set MONGO_INITDB_ROOT_PASSWORD or pass --password." >&2
  exit 2
fi

if [[ -z "$("${compose_cmd[@]}" ps -q "${SERVICE}")" ]]; then
  echo "Service '${SERVICE}' is not running. Start it first." >&2
  exit 1
fi

cmd=(
  "${compose_cmd[@]}" exec -T "${SERVICE}" mongorestore
  --username "${USERNAME}"
  --password "${PASSWORD}"
  --authenticationDatabase "${AUTH_DB}"
  --archive
  --gzip
)

if [[ "${ALL_DATABASES}" != "true" ]]; then
  cmd+=(--db "${DB_NAME}" --nsInclude "${DB_NAME}.*")
fi

if [[ "${DROP_COLLECTIONS}" == "true" ]]; then
  cmd+=(--drop)
fi

echo "Restore started..."
echo "  env_file=${ENV_FILE}"
echo "  compose_file=${COMPOSE_FILE}"
echo "  service=${SERVICE}"
echo "  archive=${ARCHIVE_FILE}"
echo "  all_databases=${ALL_DATABASES}"
if [[ "${ALL_DATABASES}" != "true" ]]; then
  echo "  db=${DB_NAME}"
fi
echo "  drop=${DROP_COLLECTIONS}"

"${cmd[@]}" < "${ARCHIVE_FILE}"

echo "Restore finished from: ${ARCHIVE_FILE}"
