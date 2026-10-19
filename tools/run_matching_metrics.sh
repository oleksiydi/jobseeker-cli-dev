#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/run_matching_metrics.sh [options]

Options:
  --python-bin PATH            Python executable (default: .venv/bin/python)
  --env-file PATH              Env file to snapshot relevant runtime keys (default: .env)
  --out-dir DIR                Output directory for logs/reports (default: logs/metrics)
  --reindex-version VALUE      Value for MATCHING_CHUNKING_VERSION in Run B
                               (default: smoke-reindex-<utc-timestamp>)
  --queries-file PATH          Text file with one query per line for search benchmark (optional)
  --profile-key KEY            Profile key for search benchmark (default: default)
  --search-limit N             Limit for search results (default: 20)
  --skip-search                Skip search benchmark block
  --help                       Show help

What it does:
  1) Run A: baseline build_job_index with current config.
  2) Run B: forced reindex via MATCHING_CHUNKING_VERSION override.
  3) Parse key metrics from both runs.
  4) (Optional) run search benchmark for queries from file.
  5) Save Markdown summary report to out-dir.

Notes:
  - Script does NOT delete MongoDB/Qdrant data.
  - It only runs application commands and collects logs/metrics.
EOF
}

PYTHON_BIN=".venv/bin/python"
ENV_FILE=".env"
OUT_DIR="logs/metrics"
REINDEX_VERSION=""
QUERIES_FILE=""
PROFILE_KEY="default"
SEARCH_LIMIT="20"
SKIP_SEARCH="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --reindex-version) REINDEX_VERSION="$2"; shift 2 ;;
    --queries-file) QUERIES_FILE="$2"; shift 2 ;;
    --profile-key) PROFILE_KEY="$2"; shift 2 ;;
    --search-limit) SEARCH_LIMIT="$2"; shift 2 ;;
    --skip-search) SKIP_SEARCH="true"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found or not executable: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Env file not found: ${ENV_FILE}" >&2
  exit 1
fi

if [[ -n "${QUERIES_FILE}" && ! -f "${QUERIES_FILE}" ]]; then
  echo "Queries file not found: ${QUERIES_FILE}" >&2
  exit 1
fi

if [[ -z "${REINDEX_VERSION}" ]]; then
  REINDEX_VERSION="smoke-reindex-$(date -u +%Y%m%dT%H%M%SZ)"
fi

RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${OUT_DIR}/${RUN_TS}"
mkdir -p "${RUN_DIR}"

RUN_A_LOG="${RUN_DIR}/build_run_a.log"
RUN_B_LOG="${RUN_DIR}/build_run_b.log"
SEARCH_LOG="${RUN_DIR}/search_benchmark.log"
REPORT_FILE="${RUN_DIR}/report.md"
MANIFEST_FILE="${RUN_DIR}/manifest.env"
ENV_SNAPSHOT_FILE="${RUN_DIR}/env_relevant.env"

cat > "${MANIFEST_FILE}" <<EOF
RUN_TS=${RUN_TS}
PYTHON_BIN=${PYTHON_BIN}
ENV_FILE=${ENV_FILE}
REINDEX_VERSION=${REINDEX_VERSION}
PROFILE_KEY=${PROFILE_KEY}
SEARCH_LIMIT=${SEARCH_LIMIT}
SKIP_SEARCH=${SKIP_SEARCH}
QUERIES_FILE=${QUERIES_FILE}
EOF

collect_env_snapshot() {
  local env_file="$1"
  local out_file="$2"
  awk -F= '
    BEGIN { OFS="=" }
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    {
      key=$1
      sub(/^[[:space:]]+/, "", key)
      sub(/[[:space:]]+$/, "", key)
      value=$0
      sub(/^[^=]*=/, "", value)
      sub(/^[[:space:]]+/, "", value)
      sub(/[[:space:]]+$/, "", value)
      # Safe allowlist for runtime behavior/speed (no credentials):
      if (key ~ /^MATCHING_/ || key == "MATCHING_BUILD_MAX_DOCS" || key == "MATCHING_SKIP_EMBEDDINGS" || key == "AUTO_FULL_REINDEX_ON_COLLECTION_CREATE" || key == "JOBDETAILS_PREFILTER_BATCH_SIZE") {
        print key, value
      }
    }
  ' "${env_file}" | sort > "${out_file}"
}

extract_metric() {
  local log_file="$1"
  local key="$2"
  local line
  line="$(grep -E "build_job_index finished:" "${log_file}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    echo ""
    return 0
  fi
  echo "${line}" | sed -n "s/.*${key}=\\([^ ]*\\).*/\\1/p"
}

extract_failed_qdrant() {
  local log_file="$1"
  local line
  line="$(grep -E "build_job_index qdrant metrics:" "${log_file}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    echo ""
    return 0
  fi
  echo "${line}" | sed -n "s/.*failed_qdrant=\\([^ ]*\\).*/\\1/p"
}

duration_seconds() {
  local start_epoch="$1"
  local end_epoch="$2"
  echo $((end_epoch - start_epoch))
}

echo "[1/4] Run A (baseline) ..."
collect_env_snapshot "${ENV_FILE}" "${ENV_SNAPSHOT_FILE}"
START_A="$(date +%s)"
"${PYTHON_BIN}" -m app.build_job_index > "${RUN_A_LOG}" 2>&1
END_A="$(date +%s)"
DUR_A="$(duration_seconds "${START_A}" "${END_A}")"

echo "[2/4] Run B (forced reindex via MATCHING_CHUNKING_VERSION=${REINDEX_VERSION}) ..."
START_B="$(date +%s)"
MATCHING_CHUNKING_VERSION="${REINDEX_VERSION}" "${PYTHON_BIN}" -m app.build_job_index > "${RUN_B_LOG}" 2>&1
END_B="$(date +%s)"
DUR_B="$(duration_seconds "${START_B}" "${END_B}")"

RUN_A_RUN_ID="$(extract_metric "${RUN_A_LOG}" "run_id")"
RUN_A_SCANNED="$(extract_metric "${RUN_A_LOG}" "scanned")"
RUN_A_REINDEXED="$(extract_metric "${RUN_A_LOG}" "reindexed")"
RUN_A_SKIPPED="$(extract_metric "${RUN_A_LOG}" "skipped")"
RUN_A_FAILED_QUALITY="$(extract_metric "${RUN_A_LOG}" "failed_quality")"
RUN_A_NORMALIZED_ONLY="$(extract_metric "${RUN_A_LOG}" "normalized_only")"
RUN_A_FAILED_QDRANT="$(extract_failed_qdrant "${RUN_A_LOG}")"

RUN_B_RUN_ID="$(extract_metric "${RUN_B_LOG}" "run_id")"
RUN_B_SCANNED="$(extract_metric "${RUN_B_LOG}" "scanned")"
RUN_B_REINDEXED="$(extract_metric "${RUN_B_LOG}" "reindexed")"
RUN_B_SKIPPED="$(extract_metric "${RUN_B_LOG}" "skipped")"
RUN_B_FAILED_QUALITY="$(extract_metric "${RUN_B_LOG}" "failed_quality")"
RUN_B_NORMALIZED_ONLY="$(extract_metric "${RUN_B_LOG}" "normalized_only")"
RUN_B_FAILED_QDRANT="$(extract_failed_qdrant "${RUN_B_LOG}")"

SEARCH_RUNS="0"
if [[ "${SKIP_SEARCH}" != "true" && -n "${QUERIES_FILE}" ]]; then
  echo "[3/4] Search benchmark ..."
  : > "${SEARCH_LOG}"
  while IFS= read -r query; do
    if [[ -z "${query}" ]]; then
      continue
    fi
    if [[ "${query}" =~ ^# ]]; then
      continue
    fi
    SEARCH_RUNS=$((SEARCH_RUNS + 1))
    Q_START="$(date +%s)"
    "${PYTHON_BIN}" -m app.search_jobs --query "${query}" --profile-key "${PROFILE_KEY}" --limit "${SEARCH_LIMIT}" > "${RUN_DIR}/search_${SEARCH_RUNS}.json" 2>> "${SEARCH_LOG}"
    Q_END="$(date +%s)"
    Q_DUR="$(duration_seconds "${Q_START}" "${Q_END}")"
    echo "query_${SEARCH_RUNS}_seconds=${Q_DUR}" >> "${SEARCH_LOG}"
    echo "query_${SEARCH_RUNS}_text=${query}" >> "${SEARCH_LOG}"
  done < "${QUERIES_FILE}"
fi

echo "[4/4] Writing report ..."
cat > "${REPORT_FILE}" <<EOF
# Matching Metrics Run (${RUN_TS})

## Environment
- python_bin: \`${PYTHON_BIN}\`
- env_file: \`${ENV_FILE}\`
- reindex_version_for_run_b: \`${REINDEX_VERSION}\`
- profile_key: \`${PROFILE_KEY}\`
- search_limit: \`${SEARCH_LIMIT}\`
- queries_file: \`${QUERIES_FILE}\`
- skip_search: \`${SKIP_SEARCH}\`
- relevant_env_snapshot: \`${ENV_SNAPSHOT_FILE}\`

## Build Job Index

### Run A (baseline)
- run_id: \`${RUN_A_RUN_ID}\`
- duration_seconds: \`${DUR_A}\`
- scanned: \`${RUN_A_SCANNED}\`
- reindexed: \`${RUN_A_REINDEXED}\`
- skipped: \`${RUN_A_SKIPPED}\`
- failed_quality: \`${RUN_A_FAILED_QUALITY}\`
- normalized_only: \`${RUN_A_NORMALIZED_ONLY}\`
- failed_qdrant: \`${RUN_A_FAILED_QDRANT}\`
- log: \`${RUN_A_LOG}\`

### Run B (forced reindex)
- run_id: \`${RUN_B_RUN_ID}\`
- duration_seconds: \`${DUR_B}\`
- scanned: \`${RUN_B_SCANNED}\`
- reindexed: \`${RUN_B_REINDEXED}\`
- skipped: \`${RUN_B_SKIPPED}\`
- failed_quality: \`${RUN_B_FAILED_QUALITY}\`
- normalized_only: \`${RUN_B_NORMALIZED_ONLY}\`
- failed_qdrant: \`${RUN_B_FAILED_QDRANT}\`
- log: \`${RUN_B_LOG}\`

## Search Benchmark
- runs: \`${SEARCH_RUNS}\`
- log: \`${SEARCH_LOG}\`

## Artifacts
- manifest: \`${MANIFEST_FILE}\`
- relevant_env_snapshot: \`${ENV_SNAPSHOT_FILE}\`
- report: \`${REPORT_FILE}\`
EOF

echo "Done."
echo "Report: ${REPORT_FILE}"
