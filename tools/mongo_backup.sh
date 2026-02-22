#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/mongo_backup.sh [options]

Options:
  --compose-file PATH   Docker Compose file (default: docker/docker-compose.yml)
  --service NAME        Mongo service name in compose (default: mongo)
  --username USER       Mongo username (default: root)
  --password PASS       Mongo password (default: example)
  --auth-db DB          Mongo auth database (default: admin)
  --db DB               Database name to dump (default: jobcenter)
  --all-databases       Dump all databases (ignores --db)
  --out FILE            Output archive path (default: backups/mongo_<db>_<utc>.archive.gz)
  --help                Show help
EOF
}

COMPOSE_FILE="docker/docker-compose.yml"
SERVICE="mongo"
USERNAME="root"
PASSWORD="example"
AUTH_DB="admin"
DB_NAME="jobcenter"
ALL_DATABASES="false"
OUT_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --service) SERVICE="$2"; shift 2 ;;
    --username) USERNAME="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --auth-db) AUTH_DB="$2"; shift 2 ;;
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

if [[ -z "$(docker compose -f "${COMPOSE_FILE}" ps -q "${SERVICE}")" ]]; then
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
  docker compose -f "${COMPOSE_FILE}" exec -T "${SERVICE}" mongodump
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
echo "  compose_file=${COMPOSE_FILE}"
echo "  service=${SERVICE}"
echo "  all_databases=${ALL_DATABASES}"
if [[ "${ALL_DATABASES}" != "true" ]]; then
  echo "  db=${DB_NAME}"
fi
echo "  output=${OUT_FILE}"

"${cmd[@]}" > "${OUT_FILE}"

echo "Backup finished: ${OUT_FILE}"

