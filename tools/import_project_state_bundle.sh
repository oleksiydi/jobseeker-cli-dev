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
  - restore Qdrant storage into resolved host path

Options:
  --bundle FILE            Transfer archive (.tar.gz) created by export script
  --env-file PATH          Target env file path (default: .env)
  --compose-file PATH      Docker compose file (default: docker/docker-compose.yml)
  --mongo-service NAME     Mongo service name (default: mongo)
  --mongo-db DB            Target mongo DB (default: jobcenter)
  --mongo-data-path PATH   Explicit Mongo data path on host for reset (optional)
  --qdrant-service NAME    Qdrant service name (default: qdrant)
  --qdrant-data-path PATH  Explicit Qdrant data path on host for restore (optional)
  --overwrite-env          Overwrite existing env file
  --replace-conf           Replace existing conf/ before restore
  --reset-mongo-data       Stop mongo, remove local mongo data dir, start fresh mongo
  --restore-mongo          Explicitly restore Mongo from bundle archive
  --restore-mongo-replace  Reset mongo data + restore Mongo (explicit replace semantics)
  --drop-mongo             Drop collections before mongo restore
  --replace-logs           Replace existing logs/normalization
  --replace-qdrant         Replace existing Qdrant storage at resolved target path
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
MONGO_DATA_PATH=""
QDRANT_SERVICE="qdrant"
QDRANT_DATA_PATH=""
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
    --mongo-data-path) MONGO_DATA_PATH="$2"; shift 2 ;;
    --qdrant-service) QDRANT_SERVICE="$2"; shift 2 ;;
    --qdrant-data-path) QDRANT_DATA_PATH="$2"; shift 2 ;;
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
source tools/lib/state_bundle_paths.sh

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

clear_dir_contents() {
  local target_dir="$1"
  local helper_image="${2:-mongo:8.0}"
  mkdir -p "${target_dir}"

  # Fast path: try removing as current user first.
  (
    shopt -s dotglob nullglob
    local entries=("${target_dir}"/*)
    if [[ ${#entries[@]} -gt 0 ]]; then
      rm -rf "${entries[@]}" 2>/dev/null || true
    fi
  )

  # Slow path: if directory still has entries (often root-owned), clean via container root.
  if find "${target_dir}" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    docker run --rm -v "${target_dir}:/target" "${helper_image}" \
      sh -lc 'rm -rf /target/* /target/.[!.]* /target/..?* 2>/dev/null || true'
  fi
}

wait_for_mongo_ready() {
  local env_file="$1"
  local compose_file="$2"
  local service="$3"
  local timeout_seconds="${4:-90}"

  local username password auth_db
  username="$(read_env_value "MONGO_INITDB_ROOT_USERNAME" "${env_file}")"
  password="$(read_env_value "MONGO_INITDB_ROOT_PASSWORD" "${env_file}")"
  auth_db="$(read_env_value "MONGO_AUTH_DB" "${env_file}")"
  username="${username:-root}"
  auth_db="${auth_db:-admin}"

  if [[ -z "${password}" ]]; then
    echo "WARN: MONGO_INITDB_ROOT_PASSWORD is empty; skip explicit readiness wait"
    return 0
  fi

  local started_at now
  started_at="$(date +%s)"
  while true; do
    if docker compose --env-file "${env_file}" -f "${compose_file}" exec -T "${service}" \
      mongosh --quiet \
      --username "${username}" \
      --password "${password}" \
      --authenticationDatabase "${auth_db}" \
      --eval 'db.adminCommand({ ping: 1 }).ok' 2>/dev/null | grep -q '^1$'; then
      return 0
    fi

    now="$(date +%s)"
    if (( now - started_at >= timeout_seconds )); then
      echo "ERROR: Mongo is not ready after ${timeout_seconds}s" >&2
      return 1
    fi
    sleep 2
  done
}

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
  resolve_mongo_data_path "${MONGO_DATA_PATH}" "${ENV_FILE}" "${COMPOSE_FILE}" "${MONGO_SERVICE}" >/dev/null
  resolved_mongo_data_path="${STATE_BUNDLE_RESOLVED_PATH}"
  mongo_data_source="${STATE_BUNDLE_PATH_SOURCE}"
  resolve_qdrant_data_path "${QDRANT_DATA_PATH}" "${ENV_FILE}" "${COMPOSE_FILE}" "${QDRANT_SERVICE}" >/dev/null
  resolved_qdrant_data_path="${STATE_BUNDLE_RESOLVED_PATH}"
  qdrant_data_source="${STATE_BUNDLE_PATH_SOURCE}"

  echo "[4/8] Reset local Mongo data"
  echo "       mongo path=${resolved_mongo_data_path} (source=${mongo_data_source})"
  echo "       qdrant path=${resolved_qdrant_data_path} (source=${qdrant_data_source})"

  # If Qdrant storage is nested under Mongo data path, Mongo reset will remove it.
  # Require explicit qdrant replacement to avoid silent data loss.
  if path_is_within "${resolved_qdrant_data_path}" "${resolved_mongo_data_path}"; then
    if [[ "${REPLACE_QDRANT}" != "true" ]]; then
      echo "ERROR: Qdrant path is within Mongo data path; reset will remove Qdrant data." >&2
      echo "       Add --replace-qdrant (recommended) or provide separate data paths." >&2
      exit 2
    fi
  fi

  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" stop "${MONGO_SERVICE}" "${QDRANT_SERVICE}" >/dev/null 2>&1 || true
  clear_dir_contents "${resolved_mongo_data_path}" "mongo:8.0"
else
  echo "[4/8] Skip mongo data reset"
fi

echo "[5/8] Start infra (mongo + qdrant)"
docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d "${MONGO_SERVICE}" "${QDRANT_SERVICE}"

if [[ "${SKIP_MONGO}" != "true" ]]; then
  echo "[6/8] Restore Mongo"
  mongo_archive="$(find "${payload_dir}" -maxdepth 1 -type f -name 'mongo_*.archive.gz' | head -n 1 || true)"
  if [[ "${RESTORE_MONGO}" == "true" && -n "${mongo_archive}" ]]; then
    wait_for_mongo_ready "${ENV_FILE}" "${COMPOSE_FILE}" "${MONGO_SERVICE}" 90
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
  resolve_qdrant_data_path "${QDRANT_DATA_PATH}" "${ENV_FILE}" "${COMPOSE_FILE}" "${QDRANT_SERVICE}" >/dev/null
  resolved_qdrant_data_path="${STATE_BUNDLE_RESOLVED_PATH}"
  qdrant_data_source="${STATE_BUNDLE_PATH_SOURCE}"
  echo "       target path=${resolved_qdrant_data_path} (source=${qdrant_data_source})"

  qdrant_archive="$(find "${payload_dir}/qdrant" -maxdepth 1 -type f -name 'qdrant_storage_*.tar.gz' | head -n 1 || true)"
  if [[ -n "${qdrant_archive}" ]]; then
    if [[ "${REPLACE_QDRANT}" == "true" ]]; then
      docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" stop "${QDRANT_SERVICE}"
      clear_dir_contents "${resolved_qdrant_data_path}" "qdrant/qdrant:v1.17.0"
      mkdir -p "${resolved_qdrant_data_path}"
      docker run --rm \
        -v "${resolved_qdrant_data_path}:/target" \
        -v "${qdrant_archive}:/snapshot.tar.gz:ro" \
        "qdrant/qdrant:v1.17.0" \
        sh -lc 'tar -xzf /snapshot.tar.gz -C /target --strip-components=1'
      docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d "${QDRANT_SERVICE}"
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
