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
  - Qdrant storage snapshot (resolved path: override -> docker inspect -> fallback)

Options:
  --env-file PATH          Env file path (default: .env)
  --compose-file PATH      Docker compose file (default: docker/docker-compose.yml)
  --mongo-service NAME     Mongo service name (default: mongo)
  --mongo-db DB            Mongo DB name (default: jobcenter)
  --qdrant-service NAME    Qdrant service name (default: qdrant)
  --qdrant-data-path PATH  Explicit Qdrant data path on host (optional)
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
QDRANT_SERVICE="qdrant"
QDRANT_DATA_PATH=""
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
    --qdrant-service) QDRANT_SERVICE="$2"; shift 2 ;;
    --qdrant-data-path) QDRANT_DATA_PATH="$2"; shift 2 ;;
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
source tools/lib/state_bundle_paths.sh

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
  qdrant_included="false"
  qdrant_reason=""
  resolve_qdrant_data_path "${QDRANT_DATA_PATH}" "${ENV_FILE}" "${COMPOSE_FILE}" "${QDRANT_SERVICE}" >/dev/null
  resolved_qdrant_path="${STATE_BUNDLE_RESOLVED_PATH}"
  qdrant_path_source="${STATE_BUNDLE_PATH_SOURCE}"
  qdrant_path_reason="${STATE_BUNDLE_PATH_REASON}"
  echo "[4/6] Qdrant snapshot from ${resolved_qdrant_path} (source=${qdrant_path_source})"

  if [[ -d "${resolved_qdrant_path}" ]]; then
    mkdir -p "${work_dir}/payload/qdrant"
    qdrant_parent="$(dirname "${resolved_qdrant_path}")"
    qdrant_base="$(basename "${resolved_qdrant_path}")"
    tar -C "${qdrant_parent}" -czf "${work_dir}/payload/qdrant/qdrant_storage_${ts}.tar.gz" "${qdrant_base}"
    qdrant_included="true"
    qdrant_reason="snapshot_created"
  else
    echo "WARN: Qdrant data path not found: ${resolved_qdrant_path}, skip qdrant snapshot"
    qdrant_reason="resolved_path_missing"
  fi
else
  echo "[4/6] Skip qdrant snapshot"
  qdrant_included="false"
  qdrant_reason="skipped_by_flag"
  resolved_qdrant_path=""
  qdrant_path_source=""
  qdrant_path_reason=""
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
includes_qdrant=${qdrant_included}
qdrant_requested=$([[ "${SKIP_QDRANT}" == "true" ]] && echo "false" || echo "true")
qdrant_path_resolved=${resolved_qdrant_path}
qdrant_path_source=${qdrant_path_source}
qdrant_path_reason=${qdrant_path_reason}
qdrant_include_reason=${qdrant_reason}
EOF

bundle_path="${OUT_DIR}/${BUNDLE_NAME}.tar.gz"
tar -C "${work_dir}" -czf "${bundle_path}" payload

echo ""
echo "Bundle created:"
echo "  ${bundle_path}"
echo ""
echo "Transfer this file to laptop, then run:"
echo "  bash tools/import_project_state_bundle.sh --bundle ${bundle_path}"
