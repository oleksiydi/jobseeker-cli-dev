#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/run_local_smoke_check.sh [options]

Purpose:
  Реальный smoke-check локальной LLM на живых данных:
  1) 2 вакансии -> taxonomy extraction (dry-run, без записи словаря)
  2) 1 профиль -> profile normalization
  3) единый лог START/STEP/END и остановка при первой ошибке

Defaults tuned for CPU-only local model:
  --max-vacancies 2
  --max-terms-per-vacancy 10
  --llm-max-tokens 1100
  --max-input-chars-vacancy 3000
  --max-input-chars-profile 4000
  --timeout-seconds 0   (0 = без таймаута)

Options:
  --python-bin PATH             Python executable (default: .venv/bin/python)
  --provider NAME               LLM provider (default: llama_cpp)
  --base-url URL                Local LLM base URL (default: http://127.0.0.1:8081)
  --model NAME                  Model id (default: Qwen3-8B-Q4_K_M.gguf)
  --max-vacancies N             Vacancies for smoke taxonomy run (default: 2)
  --max-terms-per-vacancy N     Max terms per vacancy (default: 10)
  --llm-max-tokens N            Max completion tokens per call (default: 1100)
  --max-input-chars-vacancy N   Vacancy prompt cap (default: 3000)
  --max-input-chars-profile N   Profile input cap (default: 4000)
  --timeout-seconds N           Per-call timeout, 0 disables timeout (default: 0)
  --profile-input PATH          Resume text file (default: conf/profiles/sample.resume.de.txt)
  --taxonomy-release PATH       Release taxonomy path (default: conf/normalization/taxonomy.release.json)
  --taxonomy-auto PATH          Auto taxonomy path (default: conf/normalization/taxonomy.auto.json)
  --out-dir DIR                 Smoke output root (default: logs/normalization/smoke)
  -h, --help                    Show help
EOF
}

PYTHON_BIN=".venv/bin/python"
PROVIDER="llama_cpp"
BASE_URL="http://127.0.0.1:8081"
MODEL="Qwen3-8B-Q4_K_M.gguf"
MAX_VACANCIES="2"
MAX_TERMS_PER_VACANCY="10"
LLM_MAX_TOKENS="1100"
MAX_INPUT_CHARS_VACANCY="3000"
MAX_INPUT_CHARS_PROFILE="4000"
TIMEOUT_SECONDS="0"
PROFILE_INPUT="conf/profiles/sample.resume.de.txt"
TAXONOMY_RELEASE_PATH="conf/normalization/taxonomy.release.json"
TAXONOMY_AUTO_PATH="conf/normalization/taxonomy.auto.json"
OUT_DIR="logs/normalization/smoke"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --provider) PROVIDER="$2"; shift 2 ;;
    --base-url) BASE_URL="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --max-vacancies) MAX_VACANCIES="$2"; shift 2 ;;
    --max-terms-per-vacancy) MAX_TERMS_PER_VACANCY="$2"; shift 2 ;;
    --llm-max-tokens) LLM_MAX_TOKENS="$2"; shift 2 ;;
    --max-input-chars-vacancy) MAX_INPUT_CHARS_VACANCY="$2"; shift 2 ;;
    --max-input-chars-profile) MAX_INPUT_CHARS_PROFILE="$2"; shift 2 ;;
    --timeout-seconds) TIMEOUT_SECONDS="$2"; shift 2 ;;
    --profile-input) PROFILE_INPUT="$2"; shift 2 ;;
    --taxonomy-release) TAXONOMY_RELEASE_PATH="$2"; shift 2 ;;
    --taxonomy-auto) TAXONOMY_AUTO_PATH="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi

if [[ ! -f "${PROFILE_INPUT}" ]]; then
  echo "Profile input not found: ${PROFILE_INPUT}" >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
SMOKE_DIR="${OUT_DIR}/${TS}"
mkdir -p "${SMOKE_DIR}"

LOG_FILE="${SMOKE_DIR}/smoke_run.log"
VAC_PROCESS_LOG="${SMOKE_DIR}/vacancy_process.jsonl"
PROFILE_OUT="${SMOKE_DIR}/profile.json"
PROFILE_CALLS="${SMOKE_DIR}/profile_llm_calls.jsonl"

{
  echo "[START] $(date -u +%Y-%m-%dT%H:%M:%SZ) smoke run"
  echo "[CFG] provider=${PROVIDER} model=${MODEL} max_vacancies=${MAX_VACANCIES} max_terms=${MAX_TERMS_PER_VACANCY} llm_max_tokens=${LLM_MAX_TOKENS} max_input_vacancy=${MAX_INPUT_CHARS_VACANCY} max_input_profile=${MAX_INPUT_CHARS_PROFILE} timeout_seconds=${TIMEOUT_SECONDS}"

  echo "[STEP1_START] $(date -u +%Y-%m-%dT%H:%M:%SZ) taxonomy from vacancies"
  LLM_PROVIDER="${PROVIDER}" "${PYTHON_BIN}" tools/build_taxonomy_from_vacancies.py \
    --provider "${PROVIDER}" \
    --base-url "${BASE_URL}" \
    --model "${MODEL}" \
    --max-vacancies "${MAX_VACANCIES}" \
    --max-terms-per-vacancy "${MAX_TERMS_PER_VACANCY}" \
    --llm-max-tokens "${LLM_MAX_TOKENS}" \
    --max-input-chars "${MAX_INPUT_CHARS_VACANCY}" \
    --timeout-seconds "${TIMEOUT_SECONDS}" \
    --process-log "${VAC_PROCESS_LOG}" \
    --dry-run
  echo "[STEP1_DONE] $(date -u +%Y-%m-%dT%H:%M:%SZ) taxonomy check completed"

  echo "[STEP2_START] $(date -u +%Y-%m-%dT%H:%M:%SZ) profile normalization"
  LLM_PROVIDER="${PROVIDER}" "${PYTHON_BIN}" tools/extract_profile_from_text.py \
    --input "${PROFILE_INPUT}" \
    --output "${PROFILE_OUT}" \
    --provider "${PROVIDER}" \
    --base-url "${BASE_URL}" \
    --model "${MODEL}" \
    --timeout-seconds "${TIMEOUT_SECONDS}" \
    --max-tokens "${LLM_MAX_TOKENS}" \
    --max-input-chars "${MAX_INPUT_CHARS_PROFILE}" \
    --llm-calls-log "${PROFILE_CALLS}" \
    --taxonomy-release-path "${TAXONOMY_RELEASE_PATH}" \
    --taxonomy-auto-path "${TAXONOMY_AUTO_PATH}"
  echo "[STEP2_DONE] $(date -u +%Y-%m-%dT%H:%M:%SZ) profile normalization completed"

  echo "[END] $(date -u +%Y-%m-%dT%H:%M:%SZ) smoke run finished successfully"
  echo "SMOKE_DIR=${SMOKE_DIR}"
  echo "LOG_FILE=${LOG_FILE}"
  echo "VAC_PROCESS_LOG=${VAC_PROCESS_LOG}"
  echo "PROFILE_OUT=${PROFILE_OUT}"
  echo "PROFILE_CALLS=${PROFILE_CALLS}"
} 2>&1 | tee "${LOG_FILE}"

