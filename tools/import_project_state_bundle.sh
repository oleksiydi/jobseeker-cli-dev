#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/import_project_state_bundle.sh --bundle FILE [options]

Goal:
  Restore project runtime state from transfer archive:
  - put .env in project root (optionally overwrite)
  - restore conf/
  - restore logs/normalization
  - restore Mongo dump into local Mongo container
  - restore Qdrant storage into data/qdrant

Options:
  --bundle FILE            Transfer archive (.tar.gz) created by export script
  --env-file PATH          Target env file path (default: .env)
  --compose-file PATH      Docker compose file (default: docker/docker-compose.yml)
  --mongo-service NAME     Mongo service name (default: mongo)
  --mongo-db DB            Target mongo DB (default: jobcenter)
  --overwrite-env          Overwrite existing env file
  --replace-conf           Replace existing conf/ before restore
  --reset-mongo-data       Stop mongo, remove local mongo data dir, start fresh mongo
  --restore-mongo          Explicitly restore Mongo from bundle archive
  --restore-mongo-replace  Reset mongo data + restore Mongo (explicit replace semantics)
  --drop-mongo             Drop collections before mongo restore
  --replace-logs           Replace existing logs/normalization
  --replace-qdrant         Replace existing data/qdrant content
  --skip-conf              Skip conf restore
  --skip-mongo             Skip mongo restore
  --skip-qdrant            Skip qdrant restore
  --skip-logs              Skip logs restore
  --help                   Show help

Example:
  bash tools/import_project_state_bundle.sh \
    --bundle backups/transfer/project_state_20260321T110000Z.tar.gz \
    --overwrite-env \
    --replace-conf \
    --restore-mongo-replace \
    --replace-logs \
    --replace-qdrant
EOF
}

BUNDLE_FILE=""
ENV_FILE=".env"
COMPOSE_FILE="docker/docker-compose.yml"
MONGO_SERVICE="mongo"
MONGO_DB="jobcenter"
OVERWRITE_ENV="false"
REPLACE_CONF="false"
RESET_MONGO_DATA="false"
DROP_MONGO="false"
RESTORE_MONGO="false"
REPLACE_LOGS="false"
REPLACE_QDRANT="false"
SKIP_CONF="false"
SKIP_MONGO="false"
SKIP_QDRANT="false"
SKIP_LOGS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle) BUNDLE_FILE="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --mongo-service) MONGO_SERVICE="$2"; shift 2 ;;
    --mongo-db) MONGO_DB="$2"; shift 2 ;;
    --overwrite-env) OVERWRITE_ENV="true"; shift ;;
    --replace-conf) REPLACE_CONF="true"; shift ;;
    --reset-mongo-data) RESET_MONGO_DATA="true"; shift ;;
    --restore-mongo) RESTORE_MONGO="true"; shift ;;
    --restore-mongo-replace)
      RESTORE_MONGO="true"
      RESET_MONGO_DATA="true"
      DROP_MONGO="true"
      shift
      ;;
    --drop-mongo) DROP_MONGO="true"; shift ;;
    --replace-logs) REPLACE_LOGS="true"; shift ;;
    --replace-qdrant) REPLACE_QDRANT="true"; shift ;;
    --skip-conf) SKIP_CONF="true"; shift ;;
    --skip-mongo)
      SKIP_MONGO="true"
      RESTORE_MONGO="false"
      shift
      ;;
    --skip-qdrant) SKIP_QDRANT="true"; shift ;;
    --skip-logs) SKIP_LOGS="true"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${BUNDLE_FILE}" ]]; then
  echo "--bundle is required" >&2
  usage
  exit 2
fi

if [[ ! -f "${BUNDLE_FILE}" ]]; then
  echo "Bundle file not found: ${BUNDLE_FILE}" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "Compose file not found: ${COMPOSE_FILE}" >&2
  exit 1
fi

extract_root="$(mktemp -d)"
trap 'rm -rf "${extract_root}"' EXIT

echo "[1/6] Extract bundle"
tar -C "${extract_root}" -xzf "${BUNDLE_FILE}"
payload_dir="${extract_root}/payload"
if [[ ! -d "${payload_dir}" ]]; then
  echo "Invalid bundle: payload directory not found" >&2
  exit 1
fi

echo "[2/6] Restore env"
if [[ -f "${payload_dir}/.env" ]]; then
  if [[ -f "${ENV_FILE}" && "${OVERWRITE_ENV}" != "true" ]]; then
    echo "Env file ${ENV_FILE} exists. Use --overwrite-env to replace it." >&2
    exit 2
  fi
  cp "${payload_dir}/.env" "${ENV_FILE}"
else
  echo "WARN: .env not found in bundle"
fi

if [[ "${SKIP_CONF}" != "true" ]]; then
  echo "[3/7] Restore conf/"
  bundle_conf_dir="${payload_dir}/conf"
  if [[ -d "${bundle_conf_dir}" ]]; then
    if [[ -d "conf" && "${REPLACE_CONF}" == "true" ]]; then
      rm -rf conf
    fi
    if [[ ! -d "conf" ]]; then
      cp -R "${bundle_conf_dir}" conf
    else
      cp -R "${bundle_conf_dir}/." conf/
    fi
  else
    echo "WARN: conf/ not found in bundle"
  fi
else
  echo "[3/7] Skip conf restore"
fi

if [[ "${RESET_MONGO_DATA}" == "true" ]]; then
  echo "[4/8] Reset local Mongo data"
  # Current compose uses shared host dir:
  #   mongo:  ./data      -> /data/db
  #   qdrant: ./data/qdrant -> /qdrant/storage
  # To let Mongo init root user on empty /data/db, we must clear whole ./data.
  # Therefore require qdrant replace in the same run to avoid silent data loss.
  if grep -Eq '^[[:space:]]*-[[:space:]]*\./data:/data/db' "${COMPOSE_FILE}"; then
    if [[ "${REPLACE_QDRANT}" != "true" ]]; then
      echo "ERROR: --reset-mongo-data with shared ./data volume requires --replace-qdrant." >&2
      echo "       Add --replace-qdrant (recommended) or adjust docker compose volumes." >&2
      exit 2
    fi
  fi

  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" stop "${MONGO_SERVICE}" qdrant >/dev/null 2>&1 || true
  mkdir -p data
  find data -mindepth 1 -maxdepth 1 -exec rm -rf {} +
else
  echo "[4/8] Skip mongo data reset"
fi

echo "[5/8] Start infra (mongo + qdrant)"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d "${MONGO_SERVICE}" qdrant

if [[ "${SKIP_MONGO}" != "true" ]]; then
  echo "[6/8] Restore Mongo"
  mongo_archive="$(find "${payload_dir}" -maxdepth 1 -type f -name 'mongo_*.archive.gz' | head -n 1 || true)"
  if [[ "${RESTORE_MONGO}" == "true" && -n "${mongo_archive}" ]]; then
    restore_cmd=(bash tools/mongo_restore.sh
      --archive "${mongo_archive}"
      --env-file "${ENV_FILE}"
      --compose-file "${COMPOSE_FILE}"
      --service "${MONGO_SERVICE}"
      --db "${MONGO_DB}"
    )
    if [[ "${DROP_MONGO}" == "true" ]]; then
      restore_cmd+=(--drop)
    fi
    "${restore_cmd[@]}"
  elif [[ "${RESTORE_MONGO}" == "true" ]]; then
    echo "WARN: --restore-mongo requested, but mongo archive not found in bundle"
  else
    echo "Mongo restore skipped (explicit mode: add --restore-mongo or --restore-mongo-replace)"
  fi
else
  echo "[6/8] Skip mongo restore"
fi

if [[ "${SKIP_QDRANT}" != "true" ]]; then
  echo "[7/8] Restore Qdrant"
  qdrant_archive="$(find "${payload_dir}/qdrant" -maxdepth 1 -type f -name 'qdrant_storage_*.tar.gz' | head -n 1 || true)"
  if [[ -n "${qdrant_archive}" ]]; then
    mkdir -p data
    if [[ "${REPLACE_QDRANT}" == "true" ]]; then
      docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" stop qdrant
      rm -rf data/qdrant
      mkdir -p data/qdrant
      tar -C data -xzf "${qdrant_archive}"
      docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d qdrant
    else
      echo "Qdrant snapshot found but not applied. Use --replace-qdrant to apply it."
    fi
  else
    echo "WARN: qdrant snapshot not found, skip qdrant restore"
  fi
else
  echo "[7/8] Skip qdrant restore"
fi

if [[ "${SKIP_LOGS}" != "true" ]]; then
  echo "[8/8] Restore logs"
  bundle_logs_dir="${payload_dir}/logs/normalization"
  if [[ -d "${bundle_logs_dir}" ]]; then
    if [[ -d "logs/normalization" && "${REPLACE_LOGS}" == "true" ]]; then
      rm -rf "logs/normalization"
    fi
    mkdir -p logs
    if [[ ! -d "logs/normalization" ]]; then
      cp -R "${bundle_logs_dir}" "logs/normalization"
    else
      cp -R "${bundle_logs_dir}/." "logs/normalization/"
    fi
  else
    echo "WARN: logs/normalization not found in bundle"
  fi
else
  echo "[8/8] Skip logs restore"
fi

echo ""
echo "Import completed."
echo "Checks:"
echo "  docker compose --env-file ${ENV_FILE} -f ${COMPOSE_FILE} ps"
echo "  ls -lah logs/normalization | head"
