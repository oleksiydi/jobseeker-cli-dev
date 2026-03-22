#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/export_project_state_bundle.sh [options]

Goal:
  Build one transfer archive with project runtime state:
  - .env (with secrets)
  - conf/ (runtime config files)
  - logs/normalization
  - Mongo dump archive (jobcenter by default)
  - Qdrant storage snapshot (data/qdrant)

Options:
  --env-file PATH          Env file path (default: .env)
  --compose-file PATH      Docker compose file (default: docker/docker-compose.yml)
  --mongo-service NAME     Mongo service name (default: mongo)
  --mongo-db DB            Mongo DB name (default: jobcenter)
  --out-dir PATH           Output directory for bundle (default: backups/transfer)
  --bundle-name NAME       Bundle file name without extension (default: project_state_<UTC_TS>)
  --skip-conf              Do not include conf directory
  --skip-logs              Do not include logs/normalization
  --skip-qdrant            Do not include Qdrant snapshot
  --help                   Show help

Example:
  bash tools/export_project_state_bundle.sh --mongo-db jobcenter
EOF
}

ENV_FILE=".env"
COMPOSE_FILE="docker/docker-compose.yml"
MONGO_SERVICE="mongo"
MONGO_DB="jobcenter"
OUT_DIR="backups/transfer"
BUNDLE_NAME=""
SKIP_LOGS="false"
SKIP_QDRANT="false"
SKIP_CONF="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --mongo-service) MONGO_SERVICE="$2"; shift 2 ;;
    --mongo-db) MONGO_DB="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --bundle-name) BUNDLE_NAME="$2"; shift 2 ;;
    --skip-conf) SKIP_CONF="true"; shift ;;
    --skip-logs) SKIP_LOGS="true"; shift ;;
    --skip-qdrant) SKIP_QDRANT="true"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Env file not found: ${ENV_FILE}" >&2
  exit 1
fi

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "Compose file not found: ${COMPOSE_FILE}" >&2
  exit 1
fi

ts="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z "${BUNDLE_NAME}" ]]; then
  BUNDLE_NAME="project_state_${ts}"
fi

work_dir="${OUT_DIR}/${BUNDLE_NAME}"
mkdir -p "${work_dir}"
mkdir -p "${work_dir}/payload"

echo "[1/5] Copy env file"
cp "${ENV_FILE}" "${work_dir}/payload/.env"

if [[ "${SKIP_CONF}" != "true" ]]; then
  echo "[2/6] Copy conf/"
  if [[ -d "conf" ]]; then
    cp -R "conf" "${work_dir}/payload/conf"
  else
    echo "WARN: conf directory not found, skip conf copy"
  fi
else
  echo "[2/6] Skip conf copy"
fi

echo "[3/6] Mongo backup"
mongo_archive="${work_dir}/payload/mongo_${MONGO_DB}_${ts}.archive.gz"
bash tools/mongo_backup.sh \
  --env-file "${ENV_FILE}" \
  --compose-file "${COMPOSE_FILE}" \
  --service "${MONGO_SERVICE}" \
  --db "${MONGO_DB}" \
  --out "${mongo_archive}"

if [[ "${SKIP_QDRANT}" != "true" ]]; then
  echo "[4/6] Qdrant snapshot from data/qdrant"
  if [[ -d "data/qdrant" ]]; then
    mkdir -p "${work_dir}/payload/qdrant"
    tar -C data -czf "${work_dir}/payload/qdrant/qdrant_storage_${ts}.tar.gz" qdrant
  else
    echo "WARN: data/qdrant not found, skip qdrant snapshot"
  fi
else
  echo "[4/6] Skip qdrant snapshot"
fi

if [[ "${SKIP_LOGS}" != "true" ]]; then
  echo "[5/6] Copy logs/normalization"
  if [[ -d "logs/normalization" ]]; then
    mkdir -p "${work_dir}/payload/logs"
    cp -R "logs/normalization" "${work_dir}/payload/logs/"
  else
    echo "WARN: logs/normalization not found, skip logs copy"
  fi
else
  echo "[5/6] Skip logs copy"
fi

echo "[6/6] Pack transfer archive"
cat > "${work_dir}/payload/manifest.txt" <<EOF
created_at_utc=${ts}
mongo_db=${MONGO_DB}
env_file_basename=$(basename "${ENV_FILE}")
includes_conf=$([[ "${SKIP_CONF}" == "true" ]] && echo "false" || echo "true")
includes_logs=$([[ "${SKIP_LOGS}" == "true" ]] && echo "false" || echo "true")
includes_qdrant=$([[ "${SKIP_QDRANT}" == "true" ]] && echo "false" || echo "true")
EOF

bundle_path="${OUT_DIR}/${BUNDLE_NAME}.tar.gz"
tar -C "${work_dir}" -czf "${bundle_path}" payload

echo ""
echo "Bundle created:"
echo "  ${bundle_path}"
echo ""
echo "Transfer this file to laptop, then run:"
echo "  bash tools/import_project_state_bundle.sh --bundle ${bundle_path}"
