#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/reset_taxonomy_state.sh [options]

Options:
  --taxonomy PATH           Taxonomy file (default: conf/normalization/taxonomy.auto.json)
  --unknown-terms PATH      Unknown terms log (default: logs/normalization/unknown_terms.jsonl)
  --pending PATH            Pending suggestions log (default: logs/normalization/taxonomy_pending.jsonl)
  --processed-index PATH    Processed signatures index (default: logs/normalization/taxonomy_processed.json)
  --runs-dir PATH           Normalization runs dir (default: logs/profile_normalization)
  --clear-runs              Also delete all files under runs dir.
  -h, --help                Show help.

What it resets:
  1) Recreates empty taxonomy JSON with categories skills/tools/stack.
  2) Truncates unknown_terms and pending logs.
  3) Resets processed index to valid empty JSON object {}.
  4) Optionally clears normalization run artifacts.
EOF
}

TAXONOMY_PATH="conf/normalization/taxonomy.auto.json"
UNKNOWN_TERMS_PATH="logs/normalization/unknown_terms.jsonl"
PENDING_PATH="logs/normalization/taxonomy_pending.jsonl"
PROCESSED_INDEX_PATH="logs/normalization/taxonomy_processed.json"
RUNS_DIR="logs/profile_normalization"
CLEAR_RUNS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --taxonomy) TAXONOMY_PATH="$2"; shift 2 ;;
    --unknown-terms) UNKNOWN_TERMS_PATH="$2"; shift 2 ;;
    --pending) PENDING_PATH="$2"; shift 2 ;;
    --processed-index) PROCESSED_INDEX_PATH="$2"; shift 2 ;;
    --runs-dir) RUNS_DIR="$2"; shift 2 ;;
    --clear-runs) CLEAR_RUNS="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

mkdir -p "$(dirname "${TAXONOMY_PATH}")"
mkdir -p "$(dirname "${UNKNOWN_TERMS_PATH}")"
mkdir -p "$(dirname "${PENDING_PATH}")"
mkdir -p "$(dirname "${PROCESSED_INDEX_PATH}")"
mkdir -p "${RUNS_DIR}"

cat > "${TAXONOMY_PATH}" <<'EOF'
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

: > "${UNKNOWN_TERMS_PATH}"
: > "${PENDING_PATH}"
printf "{}\n" > "${PROCESSED_INDEX_PATH}"

if [[ "${CLEAR_RUNS}" == "true" ]]; then
  rm -rf "${RUNS_DIR:?}/"*
fi

echo "Reset done."
echo "taxonomy=${TAXONOMY_PATH}"
echo "unknown_terms=${UNKNOWN_TERMS_PATH}"
echo "pending=${PENDING_PATH}"
echo "processed_index=${PROCESSED_INDEX_PATH}"
if [[ "${CLEAR_RUNS}" == "true" ]]; then
  echo "runs_cleared=${RUNS_DIR}"
else
  echo "runs_cleared=no (use --clear-runs to wipe ${RUNS_DIR})"
fi
