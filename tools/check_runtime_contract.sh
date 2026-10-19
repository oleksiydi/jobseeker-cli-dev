#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

echo "[contract] checking strict active filter..."
rg -n 'return \{"is_active": True\}' app/build_job_index.py >/dev/null
rg -n -U --pcre2 '\{\s*"\$match"\s*:\s*\{\s*"is_active"\s*:\s*True\s*\}\s*\}' app/analyze_jobs.py >/dev/null

echo "[contract] checking run-scoped details input..."
rg -n 'find_one\(\{"key": "job_list_collector_query_scope"\}\)' app/job_details_collector.py >/dev/null
rg -n 'usable_list_run' app/job_details_collector.py >/dev/null
rg -n '\{"event_type": "list_seen", "run_id": run_id\}' app/job_details_collector.py >/dev/null

echo "[contract] checking profile-scoped analysis uniqueness..."
rg -n -U --pcre2 '\("referenznummer",\s*ASCENDING\),\s*\("candidate_profile_key",\s*ASCENDING\),\s*\("input_hash",\s*ASCENDING\)' app/analyze_jobs.py >/dev/null
rg -n -U --pcre2 '\{\s*"referenznummer":\s*ref,\s*"candidate_profile_key":\s*key,\s*"input_hash":\s*input_hash\s*\}' app/analyze_jobs.py >/dev/null

echo "[contract] runtime contract checks passed"
