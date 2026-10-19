#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

if [[ -x ".venv/bin/pytest" ]]; then
  PYTEST=".venv/bin/pytest"
else
  PYTEST="pytest"
fi

run() {
  local title="$1"
  shift
  echo ""
  echo "[smoke] ${title}"
  echo "[smoke] cmd: $*"
  "$@"
}

run "01 list collector retry + scope" \
  "${PYTEST}" -q tests/test_job_list_collector_inactive.py -k "retry or scope_changed"

run "02 details collector prefilter + backoff + write isolation" \
  "${PYTEST}" -q tests/test_job_details_collector_prefilter.py tests/test_job_details_collector_active_filter.py

run "03 build index active filter" \
  "${PYTEST}" -q tests/test_build_job_index.py -k "active_vacancy_filter"

run "04 backfill is_active legacy" \
  "${PYTEST}" -q tests/test_import_tools.py -k "backfill_is_active"

run "05 migrate analysen profile index" \
  "${PYTEST}" -q tests/test_import_tools.py -k "migrate_analysen"

run "06 analyze jobs profile-scoped" \
  "${PYTEST}" -q tests/test_analyze_jobs.py

echo ""
echo "[smoke] all smoke checks passed"

