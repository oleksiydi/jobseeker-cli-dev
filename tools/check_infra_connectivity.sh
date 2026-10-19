#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/check_infra_connectivity.sh [options]

Options:
  --compose-file PATH         Docker compose file (default: docker/docker-compose.yml)
  --mongo-service NAME        Mongo service name in compose (default: mongo)
  --qdrant-url URL            Qdrant health URL (default: http://127.0.0.1:6333/health)
  --out-dir DIR               Output directory (default: logs/infra)
  --help                      Show help

Checks:
  1) Docker compose service "mongo" is running.
  2) Docker compose service "qdrant" is running.
  3) Mongo responds to ping inside container.
  4) Qdrant /health endpoint responds.
EOF
}

COMPOSE_FILE="docker/docker-compose.yml"
MONGO_SERVICE="mongo"
QDRANT_URL="http://127.0.0.1:6333/health"
OUT_DIR="logs/infra"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --mongo-service) MONGO_SERVICE="$2"; shift 2 ;;
    --qdrant-url) QDRANT_URL="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "Compose file not found: ${COMPOSE_FILE}" >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${OUT_DIR}/${TS}"
REPORT_MD="${RUN_DIR}/connectivity.md"
REPORT_JSON="${RUN_DIR}/connectivity.json"
mkdir -p "${RUN_DIR}"

CHECK_NAMES=()
CHECK_STATUS=()
CHECK_DETAILS=()

record_result() {
  local name="$1"
  local status="$2"
  local details="$3"
  CHECK_NAMES+=("${name}")
  CHECK_STATUS+=("${status}")
  CHECK_DETAILS+=("${details}")
}

run_check() {
  local name="$1"
  local details="$2"
  shift 2
  echo "[CHECK_START] ${name}"
  if "$@"; then
    echo "[CHECK_PASS] ${name}"
    record_result "${name}" "PASS" "${details}"
    return 0
  fi
  echo "[CHECK_FAIL] ${name}"
  record_result "${name}" "FAIL" "${details}"
  return 1
}

compose_service_running() {
  local service="$1"
  local status
  status="$(docker compose -f "${COMPOSE_FILE}" ps --status running --services | tr -d '\r' || true)"
  grep -qx "${service}" <<<"${status}"
}

mongo_ping() {
  docker compose -f "${COMPOSE_FILE}" exec -T "${MONGO_SERVICE}" \
    mongosh --quiet --eval 'db.runCommand({ ping: 1 }).ok' | grep -q '^1$'
}

qdrant_health() {
  local urls=("${QDRANT_URL}")
  local base="${QDRANT_URL%/*}"
  if [[ "${QDRANT_URL}" == */health ]]; then
    urls+=("${base}/healthz" "${base}/readyz" "${base}/livez")
  fi

  if command -v curl >/dev/null 2>&1; then
    for url in "${urls[@]}"; do
      if curl -fsS "${url}" >/dev/null 2>/dev/null; then
        return 0
      fi
    done
    return 1
  fi
  if command -v wget >/dev/null 2>&1; then
    for url in "${urls[@]}"; do
      if wget -qO- "${url}" >/dev/null; then
        return 0
      fi
    done
    return 1
  fi
  return 1
}

run_check "mongo_container_running" "docker compose ps running mongo" compose_service_running "mongo" || true
run_check "qdrant_container_running" "docker compose ps running qdrant" compose_service_running "qdrant" || true
run_check "mongo_ping" "mongosh ping inside container" mongo_ping || true
run_check "qdrant_health" "HTTP health endpoint ${QDRANT_URL}" qdrant_health || true

{
  echo "# Infra Connectivity Check (${TS})"
  echo
  echo "- compose_file: \`${COMPOSE_FILE}\`"
  echo "- mongo_service: \`${MONGO_SERVICE}\`"
  echo "- qdrant_url: \`${QDRANT_URL}\`"
  echo "- run_dir: \`${RUN_DIR}\`"
  echo
  echo "## Checklist"
  for i in "${!CHECK_NAMES[@]}"; do
    echo "$((i + 1)). ${CHECK_NAMES[$i]}: ${CHECK_STATUS[$i]} (${CHECK_DETAILS[$i]})"
  done
} > "${REPORT_MD}"

{
  echo "["
  for i in "${!CHECK_NAMES[@]}"; do
    sep=","
    if [[ $i -eq $((${#CHECK_NAMES[@]} - 1)) ]]; then
      sep=""
    fi
    printf '  {"name":"%s","status":"%s","details":"%s"}%s\n' \
      "${CHECK_NAMES[$i]}" "${CHECK_STATUS[$i]}" "${CHECK_DETAILS[$i]}" "${sep}"
  done
  echo "]"
} > "${REPORT_JSON}"

for i in "${!CHECK_STATUS[@]}"; do
  if [[ "${CHECK_STATUS[$i]}" == "FAIL" ]]; then
    echo "infra_connectivity_failed report=${REPORT_MD}"
    exit 1
  fi
done

echo "infra_connectivity_ok report=${REPORT_MD}"
