#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/mongo_restore.sh --archive FILE [options]

Options:
  --archive FILE        Input archive path (.archive.gz) (required)
  --compose-file PATH   Docker Compose file (default: docker/docker-compose.yml)
  --service NAME        Mongo service name in compose (default: mongo)
  --username USER       Mongo username (default: root)
  --password PASS       Mongo password (default: example)
  --auth-db DB          Mongo auth database (default: admin)
  --db DB               Target database for restore (default: jobcenter)
  --all-databases       Restore all namespaces from archive (ignores --db)
  --drop                Drop collections before restoring documents
  --help                Show help
EOF
}

ARCHIVE_FILE=""
COMPOSE_FILE="docker/docker-compose.yml"
SERVICE="mongo"
USERNAME="${MONGO_INITDB_ROOT_USERNAME:-root}"
PASSWORD="${MONGO_INITDB_ROOT_PASSWORD:-}"
AUTH_DB="${MONGO_AUTH_DB:-admin}"
DB_NAME="jobcenter"
ALL_DATABASES="false"
DROP_COLLECTIONS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive) ARCHIVE_FILE="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --username) USERNAME="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --auth-db) AUTH_DB="$2"; shift 2 ;;
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

if [[ -z "${PASSWORD}" ]]; then
  echo "Mongo password is empty. Set MONGO_INITDB_ROOT_PASSWORD or pass --password." >&2
  exit 2
fi

if [[ -z "$(docker compose -f "${COMPOSE_FILE}" ps -q "${SERVICE}")" ]]; then
  echo "Service '${SERVICE}' is not running. Start it first." >&2
  exit 1
fi

cmd=(
  docker compose -f "${COMPOSE_FILE}" exec -T "${SERVICE}" mongorestore
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
