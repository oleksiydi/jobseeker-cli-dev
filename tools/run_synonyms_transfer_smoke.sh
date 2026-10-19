#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/run_synonyms_transfer_smoke.sh [options]

Options:
  --python-bin PATH         Python executable (default: .venv/bin/python)
  --out-dir DIR             Snapshot/report directory (default: logs/normalization/smoke)
  --status VALUE            Optional export status filter (default: accepted)
  --with-replace-import     Run destructive replace import after dry-run validation
  --skip-publish            Skip publish step
  --help                    Show help

Flow:
  1) Export full snapshot from synonyms Mongo state (validated).
  2) Import snapshot in dry-run mode (validated).
  3) Optionally run replace-only import (destructive) when --with-replace-import is set.
  4) Publish synonyms.auto.json from Mongo state.
  5) Validate context/ambiguity transfer invariants.
  6) Write a short smoke report.

Safety:
  - By default import is dry-run only.
  - Replace import requires explicit --with-replace-import.
EOF
}

PYTHON_BIN=".venv/bin/python"
OUT_DIR="logs/normalization/smoke"
STATUS_FILTER="accepted"
WITH_REPLACE_IMPORT="false"
SKIP_PUBLISH="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --status) STATUS_FILTER="$2"; shift 2 ;;
    --with-replace-import) WITH_REPLACE_IMPORT="true"; shift ;;
    --skip-publish) SKIP_PUBLISH="true"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi

RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${OUT_DIR}/${RUN_TS}"
STATE_JSONL="${RUN_DIR}/synonyms_state_snapshot.full.jsonl"
AUDIT_JSONL="${RUN_DIR}/synonyms_audit_snapshot.full.jsonl"
PUBLISH_OUT="${RUN_DIR}/synonyms.auto.smoke.json"
REPORT_FILE="${RUN_DIR}/report.md"

mkdir -p "${RUN_DIR}"

echo "[1/4] export snapshot"
env PYTHONPATH=. "${PYTHON_BIN}" tools/export_synonyms_state_snapshot.py \
  --run-id "syn-smoke-${RUN_TS}" \
  --out-state-jsonl "${STATE_JSONL}" \
  --out-audit-jsonl "${AUDIT_JSONL}" \
  --status "${STATUS_FILTER}" \
  --validate

echo "[2/4] import snapshot dry-run"
env PYTHONPATH=. "${PYTHON_BIN}" tools/import_synonyms_state_snapshot.py \
  --state-jsonl "${STATE_JSONL}" \
  --audit-jsonl "${AUDIT_JSONL}" \
  --validate \
  --dry-run

if [[ "${WITH_REPLACE_IMPORT}" == "true" ]]; then
  echo "[3/4] import snapshot replace-existing"
  env PYTHONPATH=. "${PYTHON_BIN}" tools/import_synonyms_state_snapshot.py \
    --state-jsonl "${STATE_JSONL}" \
    --audit-jsonl "${AUDIT_JSONL}" \
    --validate \
    --replace-existing
else
  echo "[3/4] replace import skipped (use --with-replace-import)"
fi

if [[ "${SKIP_PUBLISH}" == "true" ]]; then
  echo "[4/4] publish skipped"
else
  echo "[4/4] publish synonyms.auto from Mongo state"
  env PYTHONPATH=. "${PYTHON_BIN}" tools/publish_synonyms_auto.py \
    --out "${PUBLISH_OUT}" \
    --validate \
    --include-status "accepted,adjudicated_accepted"
fi

echo "[5/5] validate transfer invariants"
env PYTHONPATH=. "${PYTHON_BIN}" -c '
import json
import sys
from pathlib import Path

state_jsonl = Path(sys.argv[1])
publish_out = Path(sys.argv[2])
skip_publish = sys.argv[3].lower() == "true"

state_rows = []
for line in state_jsonl.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line:
        continue
    state_rows.append(json.loads(line))

if not state_rows:
    print("note: empty_state_snapshot_for_selected_status")

for idx, row in enumerate(state_rows, start=1):
    if "domain" not in row:
        raise SystemExit(f"state_row_{idx}_missing_domain")
    if "facet" not in row:
        raise SystemExit(f"state_row_{idx}_missing_facet")

if skip_publish:
    raise SystemExit(0)

if not publish_out.exists():
    raise SystemExit("publish_output_missing")
payload = json.loads(publish_out.read_text(encoding="utf-8"))
entries = payload.get("entries", [])
aliases = payload.get("aliases", {})
ambiguous = set(payload.get("ambiguous_aliases", []))

if not isinstance(entries, list):
    raise SystemExit("entries_not_list")
if not isinstance(aliases, dict):
    raise SystemExit("aliases_not_object")
if not isinstance(payload.get("ambiguous_aliases", []), list):
    raise SystemExit("ambiguous_aliases_not_list")

for idx, entry in enumerate(entries, start=1):
    if "domain" not in entry:
        raise SystemExit(f"entry_{idx}_missing_domain")
    if "facet" not in entry:
        raise SystemExit(f"entry_{idx}_missing_facet")
    if "domain_scope" not in entry:
        raise SystemExit(f"entry_{idx}_missing_domain_scope")
    if "facet_scope" not in entry:
        raise SystemExit(f"entry_{idx}_missing_facet_scope")

for alias_key in ambiguous:
    if alias_key in aliases:
        raise SystemExit(f"unsafe_global_alias_present:{alias_key}")
' "${STATE_JSONL}" "${PUBLISH_OUT}" "${SKIP_PUBLISH}"

cat > "${REPORT_FILE}" <<EOF
# Synonyms Transfer Smoke (${RUN_TS})

- run_dir: \`${RUN_DIR}\`
- python_bin: \`${PYTHON_BIN}\`
- status_filter: \`${STATUS_FILTER}\`
- with_replace_import: \`${WITH_REPLACE_IMPORT}\`
- skip_publish: \`${SKIP_PUBLISH}\`

## Artifacts

- state_snapshot: \`${STATE_JSONL}\`
- audit_snapshot: \`${AUDIT_JSONL}\`
- publish_out: \`${PUBLISH_OUT}\`

## Notes

- Dry-run import validation is always executed.
- Replace import is executed only with \`--with-replace-import\`.
- Authoritative source remains MongoDB after replace import.
- Transfer invariants validate \`domain/facet\` presence in snapshots and \`domain/facet/scope\` presence in published entries.
- Ambiguous aliases must not appear in global \`aliases{}\`.
EOF

echo "smoke_ok report=${REPORT_FILE}"
