#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/run_learning_flags_smoke.sh [options]

Purpose:
  Run final smoke checks for opt-in learning flags:
  1) vacancy learning OFF  -> must not write taxonomy artifacts
  2) vacancy learning ON   -> must write taxonomy artifacts
  3) profile learning OFF  -> must not mutate taxonomy file
  4) profile learning ON   -> must mutate taxonomy file

Options:
  --python-bin PATH         Python executable (default: .venv/bin/python)
  --provider NAME           LLM provider for vacancy smoke (default: ENV PROVIDER or openai)
  --model NAME              Model for vacancy smoke (default: ENV MODEL or gpt-4o-mini)
  --base-url URL            Optional LLM base URL override
  --api-key KEY             Optional LLM API key override
  --max-vacancies N         Vacancy sample size (default: 2)
  --max-terms N             Max terms per vacancy (base default: 5)
  --llm-max-tokens N        Max completion tokens for vacancy extraction (base default: 300)
  --retry-on-length N       Retries for finish_reason=length (base default: 0)
  --timeout-seconds N       Per-call timeout for vacancy extraction (default: 120)
  --out-root DIR            Output root (default: logs/normalization/smoke/final)
  --skip-vacancy            Skip vacancy OFF/ON checks
  --skip-profile            Skip profile OFF/ON checks
  -h, --help                Show help

Model-specific defaults:
  For gpt-5* models, if not overridden via CLI:
  - max-terms         => 3
  - llm-max-tokens    => 1200
  - retry-on-length   => 1

Artifacts:
  <run_dir>/smoke.log
  <run_dir>/summary.json
  <run_dir>/results.jsonl
  <run_dir>/cases/*
EOF
}

PYTHON_BIN=".venv/bin/python"
PROVIDER="${PROVIDER:-openai}"
MODEL="${MODEL:-gpt-4o-mini}"
BASE_URL=""
API_KEY=""
MAX_VACANCIES="2"
MAX_TERMS="5"
LLM_MAX_TOKENS="300"
RETRY_ON_LENGTH="0"
TIMEOUT_SECONDS="120"
OUT_ROOT="logs/normalization/smoke/final"
SKIP_VACANCY="false"
SKIP_PROFILE="false"
USER_SET_MAX_TERMS="false"
USER_SET_LLM_MAX_TOKENS="false"
USER_SET_RETRY_ON_LENGTH="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --provider) PROVIDER="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    --api-key) API_KEY="$2"; shift 2 ;;
    --max-vacancies) MAX_VACANCIES="$2"; shift 2 ;;
    --max-terms) MAX_TERMS="$2"; USER_SET_MAX_TERMS="true"; shift 2 ;;
    --llm-max-tokens) LLM_MAX_TOKENS="$2"; USER_SET_LLM_MAX_TOKENS="true"; shift 2 ;;
    --retry-on-length) RETRY_ON_LENGTH="$2"; USER_SET_RETRY_ON_LENGTH="true"; shift 2 ;;
    --timeout-seconds) TIMEOUT_SECONDS="$2"; shift 2 ;;
    --out-root) OUT_ROOT="$2"; shift 2 ;;
    --skip-vacancy) SKIP_VACANCY="true"; shift ;;
    --skip-profile) SKIP_PROFILE="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi

# Auto-stabilize defaults for gpt-5 family when caller did not override explicitly.
if [[ "${MODEL}" == gpt-5* ]]; then
  if [[ "${USER_SET_LLM_MAX_TOKENS}" != "true" ]]; then
    LLM_MAX_TOKENS="1200"
  fi
  if [[ "${USER_SET_MAX_TERMS}" != "true" ]]; then
    MAX_TERMS="3"
  fi
  if [[ "${USER_SET_RETRY_ON_LENGTH}" != "true" ]]; then
    RETRY_ON_LENGTH="1"
  fi
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${OUT_ROOT}/${TS}"
CASE_DIR="${RUN_DIR}/cases"
mkdir -p "${CASE_DIR}"
LOG_FILE="${RUN_DIR}/smoke.log"
RESULTS_JSONL="${RUN_DIR}/results.jsonl"
SUMMARY_JSON="${RUN_DIR}/summary.json"

touch "${RESULTS_JSONL}"

log() {
  local msg="$1"
  echo "${msg}" | tee -a "${LOG_FILE}" >/dev/null
}

sha256_file() {
  local p="$1"
  if [[ ! -f "${p}" ]]; then
    echo "missing"
    return 0
  fi
  sha256sum "${p}" | awk '{print $1}'
}

write_result() {
  local case_name="$1"
  local status="$2"
  local details="$3"
  printf '{"case":"%s","status":"%s","details":"%s","ts":"%s"}\n' \
    "${case_name}" "${status}" "${details//\"/\\\"}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${RESULTS_JSONL}"
}

run_vacancy_case() {
  local case_name="$1"
  local allow_flag="$2"       # true|false
  local expect_write="$3"     # true|false
  local case_log="${CASE_DIR}/${case_name}.log"
  local terms_out="${CASE_DIR}/${case_name}.terms.jsonl"
  local agg_out="${CASE_DIR}/${case_name}.aggregate.json"
  local calls_out="${CASE_DIR}/${case_name}.calls.jsonl"
  local proc_out="${CASE_DIR}/${case_name}.process.jsonl"

  rm -f "${terms_out}" "${agg_out}" "${calls_out}" "${proc_out}" "${case_log}"

  local -a cmd=(
    "${PYTHON_BIN}" tools/build_taxonomy_from_vacancies.py
    --provider "${PROVIDER}"
    --model "${MODEL}"
    --max-vacancies "${MAX_VACANCIES}"
    --max-terms-per-vacancy "${MAX_TERMS}"
    --llm-max-tokens "${LLM_MAX_TOKENS}"
    --retry-on-length "${RETRY_ON_LENGTH}"
    --timeout-seconds "${TIMEOUT_SECONDS}"
    --out-terms-jsonl "${terms_out}"
    --out-aggregate-json "${agg_out}"
    --llm-calls-log "${calls_out}"
    --process-log "${proc_out}"
    --overwrite-output
  )

  if [[ "${allow_flag}" == "true" ]]; then
    cmd+=(--allow-learning)
  fi
  if [[ -n "${BASE_URL}" ]]; then
    cmd+=(--base-url "${BASE_URL}")
  fi
  if [[ -n "${API_KEY}" ]]; then
    cmd+=(--api-key "${API_KEY}")
  fi

  log "[CASE_START] ${case_name}"
  if ! ALLOW_VACANCY_LEARNING="${allow_flag}" LLM_PROVIDER="${PROVIDER}" "${cmd[@]}" > "${case_log}" 2>&1; then
    write_result "${case_name}" "FAIL" "command_failed"
    log "[CASE_FAIL] ${case_name} command_failed (see ${case_log})"
    return 1
  fi

  local marker_present="false"
  if grep -q "learning_disabled_skip_write=true" "${case_log}"; then
    marker_present="true"
  fi

  local terms_nonempty="false"
  local agg_nonempty="false"
  local calls_nonempty="false"
  local process_nonempty="false"
  if [[ -s "${terms_out}" ]]; then
    terms_nonempty="true"
  fi
  if [[ -s "${agg_out}" ]]; then
    agg_nonempty="true"
  fi
  if [[ -s "${calls_out}" ]]; then
    calls_nonempty="true"
  fi
  if [[ -s "${proc_out}" ]]; then
    process_nonempty="true"
  fi

  if [[ "${expect_write}" == "false" ]]; then
    if [[ "${marker_present}" == "true" && "${terms_nonempty}" == "false" && "${agg_nonempty}" == "false" ]]; then
      write_result "${case_name}" "PASS" "learning_off_no_artifacts"
      log "[CASE_PASS] ${case_name}"
      return 0
    fi
    write_result "${case_name}" "FAIL" "expected_no_write_but_artifacts_or_marker_mismatch"
    log "[CASE_FAIL] ${case_name} expected no writes"
    return 1
  fi

  # learning ON means writes are allowed; case may still fail at LLM level.
  # For smoke we verify write path activation, not extraction quality.
  if [[ "${marker_present}" == "false" && ( "${terms_nonempty}" == "true" || "${agg_nonempty}" == "true" || "${calls_nonempty}" == "true" || "${process_nonempty}" == "true" ) ]]; then
    write_result "${case_name}" "PASS" "learning_on_write_path_active"
    log "[CASE_PASS] ${case_name}"
    return 0
  fi
  write_result "${case_name}" "FAIL" "expected_write_path_artifacts_missing"
  log "[CASE_FAIL] ${case_name} expected writes"
  return 1
}

run_profile_case() {
  local case_name="$1"
  local allow_flag="$2"       # true|false
  local expect_change="$3"    # true|false
  local case_log="${CASE_DIR}/${case_name}.log"
  local taxonomy_file="${CASE_DIR}/${case_name}.taxonomy.json"
  local processed_file="${CASE_DIR}/${case_name}.processed.json"
  local pending_file="${CASE_DIR}/${case_name}.pending.jsonl"
  local unknown_file="${CASE_DIR}/${case_name}.unknown.jsonl"

  cat > "${taxonomy_file}" <<'EOF'
{
  "version": 1,
  "aliases": {},
  "categories": {
    "skills": [],
    "tools": [],
    "stack": []
  }
}
EOF
  cat > "${unknown_file}" <<'EOF'
{"term":"k8s","source_field":"stack","context":"kubernetes cluster operations"}
EOF
  rm -f "${processed_file}" "${pending_file}" "${case_log}"

  local before_hash
  before_hash="$(sha256_file "${taxonomy_file}")"

  local -a cmd=(
    "${PYTHON_BIN}" tools/update_taxonomy_from_unknown_terms.py
    --input "${unknown_file}"
    --taxonomy "${taxonomy_file}"
    --processed-index "${processed_file}"
    --pending-out "${pending_file}"
  )
  if [[ "${allow_flag}" == "true" ]]; then
    cmd+=(--allow-learning)
  fi

  log "[CASE_START] ${case_name}"
  if ! ALLOW_PROFILE_LEARNING="${allow_flag}" "${cmd[@]}" > "${case_log}" 2>&1; then
    write_result "${case_name}" "FAIL" "command_failed"
    log "[CASE_FAIL] ${case_name} command_failed (see ${case_log})"
    return 1
  fi

  local after_hash
  after_hash="$(sha256_file "${taxonomy_file}")"
  local changed="false"
  if [[ "${before_hash}" != "${after_hash}" ]]; then
    changed="true"
  fi

  local marker_present="false"
  if grep -q "learning_disabled_skip_write=true" "${case_log}"; then
    marker_present="true"
  fi

  if [[ "${expect_change}" == "false" ]]; then
    if [[ "${changed}" == "false" && "${marker_present}" == "true" ]]; then
      write_result "${case_name}" "PASS" "learning_off_taxonomy_unchanged"
      log "[CASE_PASS] ${case_name}"
      return 0
    fi
    write_result "${case_name}" "FAIL" "expected_unchanged_or_missing_marker"
    log "[CASE_FAIL] ${case_name} expected unchanged taxonomy"
    return 1
  fi

  if [[ "${changed}" == "true" ]]; then
    write_result "${case_name}" "PASS" "learning_on_taxonomy_changed"
    log "[CASE_PASS] ${case_name}"
    return 0
  fi
  write_result "${case_name}" "FAIL" "expected_taxonomy_change"
  log "[CASE_FAIL] ${case_name} expected taxonomy mutation"
  return 1
}

log "[START] $(date -u +%Y-%m-%dT%H:%M:%SZ) run_learning_flags_smoke"
log "[CFG] provider=${PROVIDER} model=${MODEL} max_vacancies=${MAX_VACANCIES} max_terms=${MAX_TERMS} llm_max_tokens=${LLM_MAX_TOKENS} retry_on_length=${RETRY_ON_LENGTH} timeout_seconds=${TIMEOUT_SECONDS} skip_vacancy=${SKIP_VACANCY} skip_profile=${SKIP_PROFILE}"

failures=0

if [[ "${SKIP_VACANCY}" != "true" ]]; then
  run_vacancy_case "vacancy_learning_off" "false" "false" || failures=$((failures + 1))
  run_vacancy_case "vacancy_learning_on" "true" "true" || failures=$((failures + 1))
else
  write_result "vacancy_learning_off" "SKIP" "skipped_by_flag"
  write_result "vacancy_learning_on" "SKIP" "skipped_by_flag"
fi

if [[ "${SKIP_PROFILE}" != "true" ]]; then
  run_profile_case "profile_learning_off" "false" "false" || failures=$((failures + 1))
  run_profile_case "profile_learning_on" "true" "true" || failures=$((failures + 1))
else
  write_result "profile_learning_off" "SKIP" "skipped_by_flag"
  write_result "profile_learning_on" "SKIP" "skipped_by_flag"
fi

"${PYTHON_BIN}" - <<'PY' "${RESULTS_JSONL}" "${SUMMARY_JSON}"
import json
import sys
from pathlib import Path

results_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
rows = []
for line in results_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
        continue
    rows.append(json.loads(line))

status_counts = {}
for row in rows:
    status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

summary = {
    "total_cases": len(rows),
    "status_counts": status_counts,
    "cases": rows,
    "ok": status_counts.get("FAIL", 0) == 0,
}
summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(summary, ensure_ascii=False))
PY

log "[SUMMARY] $(cat "${SUMMARY_JSON}" | tr '\n' ' ')"
log "[END] $(date -u +%Y-%m-%dT%H:%M:%SZ) failures=${failures}"
log "RUN_DIR=${RUN_DIR}"
log "RESULTS_JSONL=${RESULTS_JSONL}"
log "SUMMARY_JSON=${SUMMARY_JSON}"

if [[ "${failures}" -gt 0 ]]; then
  exit 1
fi
