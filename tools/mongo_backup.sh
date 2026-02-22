#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/mongo_backup.sh [options]

Options:
  --env-file PATH      Env file for docker compose (default: .env)
  --compose-file PATH   Docker Compose file (default: docker/docker-compose.yml)
  --service NAME        Mongo service name in compose (default: mongo)
  --username USER       Mongo username (default: MONGO_INITDB_ROOT_USERNAME from env/shell or root)
  --password PASS       Mongo password (default: MONGO_INITDB_ROOT_PASSWORD from env/shell)
  --auth-db DB          Mongo auth database (default: MONGO_AUTH_DB from env/shell or admin)
  --db DB               Database name to dump (default: jobcenter)
  --all-databases       Dump all databases (ignores --db)
  --out FILE            Output archive path (default: backups/mongo_<db>_<utc>.archive.gz)
  --help                Show help
EOF
}

COMPOSE_FILE="docker/docker-compose.yml"
ENV_FILE=".env"
SERVICE="mongo"
USERNAME=""
PASSWORD=""
AUTH_DB=""
DB_NAME="jobcenter"
ALL_DATABASES="false"
OUT_FILE=""
USERNAME_FROM_ARG="false"
PASSWORD_FROM_ARG="false"
AUTH_DB_FROM_ARG="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --username) USERNAME="$2"; USERNAME_FROM_ARG="true"; shift 2 ;;
    --password) PASSWORD="$2"; PASSWORD_FROM_ARG="true"; shift 2 ;;
    --auth-db) AUTH_DB="$2"; AUTH_DB_FROM_ARG="true"; shift 2 ;;
    --db) DB_NAME="$2"; shift 2 ;;
    --all-databases) ALL_DATABASES="true"; shift ;;
    --out) OUT_FILE="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

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
  awk -F= -v k="${key}" '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    $1 == k {
      sub(/^[^=]*=/, "", $0)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $0)
      if (($0 ~ /^".*"$/) || ($0 ~ /^'\''.*'\''$/)) {
        $0 = substr($0, 2, length($0)-2)
      }
      print $0
      exit
    }
  ' "${env_file}"
}

if [[ "${USERNAME_FROM_ARG}" != "true" ]]; then
  USERNAME="${MONGO_INITDB_ROOT_USERNAME:-}"
  if [[ -z "${USERNAME}" ]]; then
    USERNAME="$(read_env_value "MONGO_INITDB_ROOT_USERNAME" "${ENV_FILE}")"
  fi
  USERNAME="${USERNAME:-root}"
fi

if [[ "${PASSWORD_FROM_ARG}" != "true" ]]; then
  PASSWORD="${MONGO_INITDB_ROOT_PASSWORD:-}"
  if [[ -z "${PASSWORD}" ]]; then
    PASSWORD="$(read_env_value "MONGO_INITDB_ROOT_PASSWORD" "${ENV_FILE}")"
  fi
fi

if [[ "${AUTH_DB_FROM_ARG}" != "true" ]]; then
  AUTH_DB="${MONGO_AUTH_DB:-}"
  if [[ -z "${AUTH_DB}" ]]; then
    AUTH_DB="$(read_env_value "MONGO_AUTH_DB" "${ENV_FILE}")"
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

if [[ -z "${OUT_FILE}" ]]; then
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  db_label="${DB_NAME}"
  if [[ "${ALL_DATABASES}" == "true" ]]; then
    db_label="all"
  fi
  OUT_FILE="backups/mongo_${db_label}_${ts}.archive.gz"
fi

mkdir -p "$(dirname "${OUT_FILE}")"

cmd=(
  "${compose_cmd[@]}" exec -T "${SERVICE}" mongodump
  --username "${USERNAME}"
  --password "${PASSWORD}"
  --authenticationDatabase "${AUTH_DB}"
  --archive
  --gzip
)

if [[ "${ALL_DATABASES}" != "true" ]]; then
  cmd+=(--db "${DB_NAME}")
fi

echo "Backup started..."
echo "  env_file=${ENV_FILE}"
echo "  compose_file=${COMPOSE_FILE}"
echo "  service=${SERVICE}"
echo "  all_databases=${ALL_DATABASES}"
if [[ "${ALL_DATABASES}" != "true" ]]; then
  echo "  db=${DB_NAME}"
fi
echo "  output=${OUT_FILE}"

"${cmd[@]}" > "${OUT_FILE}"

echo "Backup finished: ${OUT_FILE}"
