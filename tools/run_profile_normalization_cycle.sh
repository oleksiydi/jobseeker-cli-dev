#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/run_profile_normalization_cycle.sh [options]

Options:
  --python-bin PATH           Python executable (default: .venv/bin/python)
  --input PATH                Input resume text file (default: conf/profiles/sample.resume.de.txt)
  --profile-key KEY           Profile key (default: default)
  --out-dir DIR               Output directory for pass results (default: logs/profile_normalization)
  --timeout-seconds N         Extraction timeout per run (default: 1200)
  --max-tokens N              Max completion tokens (default: 350)
  --taxonomy PATH             Taxonomy file (default: conf/normalization/taxonomy.auto.json)
  --unknown-terms PATH        Unknown terms log (default: logs/normalization/unknown_terms.jsonl)
  --processed-index PATH      Processed signatures index (default: logs/normalization/taxonomy_processed.json)
  --pending-out PATH          Pending suggestions log (default: logs/normalization/taxonomy_pending.jsonl)
  --allow-empty-content       Forward flag to extractor (debug mode)
  -h, --help                  Show help

What it does:
  1) Pass 1: extract profile from the same input text.
  2) Update taxonomy from auto-collected unknown terms.
  3) Pass 2: extract profile again from the same input text.
  4) Print short before/after comparison.
EOF
}

PYTHON_BIN=".venv/bin/python"
INPUT="conf/profiles/sample.resume.de.txt"
PROFILE_KEY="default"
OUT_DIR="logs/profile_normalization"
TIMEOUT_SECONDS="1200"
MAX_TOKENS="350"
TAXONOMY_PATH="conf/normalization/taxonomy.auto.json"
UNKNOWN_TERMS_PATH="logs/normalization/unknown_terms.jsonl"
PROCESSED_INDEX_PATH="logs/normalization/taxonomy_processed.json"
PENDING_OUT_PATH="logs/normalization/taxonomy_pending.jsonl"
ALLOW_EMPTY_CONTENT="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --input) INPUT="$2"; shift 2 ;;
    --profile-key) PROFILE_KEY="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --timeout-seconds) TIMEOUT_SECONDS="$2"; shift 2 ;;
    --max-tokens) MAX_TOKENS="$2"; shift 2 ;;
    --taxonomy) TAXONOMY_PATH="$2"; shift 2 ;;
    --unknown-terms) UNKNOWN_TERMS_PATH="$2"; shift 2 ;;
    --processed-index) PROCESSED_INDEX_PATH="$2"; shift 2 ;;
    --pending-out) PENDING_OUT_PATH="$2"; shift 2 ;;
    --allow-empty-content) ALLOW_EMPTY_CONTENT="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi
if [[ ! -f "${INPUT}" ]]; then
  echo "Input file not found: ${INPUT}" >&2
  exit 1
fi

RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${OUT_DIR}/${RUN_TS}"
mkdir -p "${RUN_DIR}"
mkdir -p "$(dirname "${UNKNOWN_TERMS_PATH}")"
mkdir -p "$(dirname "${PROCESSED_INDEX_PATH}")"
mkdir -p "$(dirname "${PENDING_OUT_PATH}")"
# Ensure file exists even when pass1 logs no unknown terms.
touch "${UNKNOWN_TERMS_PATH}"

PASS1_OUT="${RUN_DIR}/profile_pass1.json"
PASS2_OUT="${RUN_DIR}/profile_pass2.json"
PASS1_LOG="${RUN_DIR}/pass1.log"
PASS2_LOG="${RUN_DIR}/pass2.log"
UPDATE_LOG="${RUN_DIR}/taxonomy_update.log"
LLM_CALLS_LOG="${RUN_DIR}/llm_calls.jsonl"
MANIFEST="${RUN_DIR}/manifest.env"

cat > "${MANIFEST}" <<EOF
RUN_TS=${RUN_TS}
INPUT=${INPUT}
PROFILE_KEY=${PROFILE_KEY}
TIMEOUT_SECONDS=${TIMEOUT_SECONDS}
MAX_TOKENS=${MAX_TOKENS}
TAXONOMY_PATH=${TAXONOMY_PATH}
UNKNOWN_TERMS_PATH=${UNKNOWN_TERMS_PATH}
PROCESSED_INDEX_PATH=${PROCESSED_INDEX_PATH}
PENDING_OUT_PATH=${PENDING_OUT_PATH}
ALLOW_EMPTY_CONTENT=${ALLOW_EMPTY_CONTENT}
LLM_CALLS_LOG=${LLM_CALLS_LOG}
EOF

EXTRA_FLAGS=()
if [[ "${ALLOW_EMPTY_CONTENT}" == "true" ]]; then
  EXTRA_FLAGS+=(--allow-empty-content)
fi

echo "[1/4] pass1 extraction..."
if ! "${PYTHON_BIN}" tools/extract_profile_from_text.py \
  --input "${INPUT}" \
  --output "${PASS1_OUT}" \
  --profile-key "${PROFILE_KEY}" \
  --timeout-seconds "${TIMEOUT_SECONDS}" \
  --max-tokens "${MAX_TOKENS}" \
  --taxonomy-path "${TAXONOMY_PATH}" \
  --unknown-terms-path "${UNKNOWN_TERMS_PATH}" \
  --llm-calls-log "${LLM_CALLS_LOG}" \
  "${EXTRA_FLAGS[@]}" > "${PASS1_LOG}" 2>&1; then
  echo "ERROR: pass1 extraction failed. Log: ${PASS1_LOG}" >&2
  cat "${PASS1_LOG}" >&2
  exit 1
fi

echo "[2/4] taxonomy update..."
if [[ -s "${UNKNOWN_TERMS_PATH}" ]]; then
  "${PYTHON_BIN}" tools/update_taxonomy_from_unknown_terms.py \
  --input "${UNKNOWN_TERMS_PATH}" \
  --taxonomy "${TAXONOMY_PATH}" \
  --processed-index "${PROCESSED_INDEX_PATH}" \
  --pending-out "${PENDING_OUT_PATH}" > "${UPDATE_LOG}" 2>&1
else
  echo "SKIP: unknown terms file is empty (${UNKNOWN_TERMS_PATH})" > "${UPDATE_LOG}"
fi

echo "[3/4] pass2 extraction..."
if ! "${PYTHON_BIN}" tools/extract_profile_from_text.py \
  --input "${INPUT}" \
  --output "${PASS2_OUT}" \
  --profile-key "${PROFILE_KEY}" \
  --timeout-seconds "${TIMEOUT_SECONDS}" \
  --max-tokens "${MAX_TOKENS}" \
  --taxonomy-path "${TAXONOMY_PATH}" \
  --unknown-terms-path "${UNKNOWN_TERMS_PATH}" \
  --llm-calls-log "${LLM_CALLS_LOG}" \
  "${EXTRA_FLAGS[@]}" > "${PASS2_LOG}" 2>&1; then
  echo "ERROR: pass2 extraction failed. Log: ${PASS2_LOG}" >&2
  cat "${PASS2_LOG}" >&2
  exit 1
fi

echo "[4/4] comparison..."
"${PYTHON_BIN}" - <<'PY' "${PASS1_OUT}" "${PASS2_OUT}"
import json
import sys
from pathlib import Path

pass1 = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
pass2 = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))

def n(field, obj):
    value = obj.get(field, [])
    return len(value) if isinstance(value, list) else 0

print("pass1.target_role =", pass1.get("target_role", ""))
print("pass2.target_role =", pass2.get("target_role", ""))
print("pass1.skills/tools/stack =", n("skills", pass1), n("tools", pass1), n("stack", pass1))
print("pass2.skills/tools/stack =", n("skills", pass2), n("tools", pass2), n("stack", pass2))
print("pass1.summary_len =", len(pass1.get("summary", "")))
print("pass2.summary_len =", len(pass2.get("summary", "")))
PY

echo ""
echo "Done."
echo "Run dir: ${RUN_DIR}"
echo "Pass1: ${PASS1_OUT}"
echo "Pass2: ${PASS2_OUT}"
echo "Taxonomy update log: ${UPDATE_LOG}"
echo "LLM calls log: ${LLM_CALLS_LOG}"
