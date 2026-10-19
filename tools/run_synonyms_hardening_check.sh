#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/run_synonyms_hardening_check.sh [options]

Options:
  --python-bin PATH         Python executable (default: .venv/bin/python)
  --out-dir DIR             Output root (default: logs/normalization/hardening)
  --status VALUE            Export status filter (default: accepted)
  --publish-include-status  Statuses for publish step (default: accepted,adjudicated_accepted)
  --allow-empty             Do not fail when snapshot has zero state rows
  --with-replace-import     Execute destructive replace import (default: off)
  --step-timeout-seconds N  Timeout per checklist step (default: 90)
  --help                    Show help

Checklist (PASS/FAIL):
  1) Export snapshot (validated)
  2) Import dry-run validation (validated)
  3) Publish dictionary from Mongo state
  4) Runtime consistency check against published dictionary
  5) Domain-aware runtime consistency (ambiguous alias behavior)
  6) Optional replace import (only with --with-replace-import)
EOF
}

PYTHON_BIN=".venv/bin/python"
OUT_DIR="logs/normalization/hardening"
STATUS_FILTER="accepted"
PUBLISH_INCLUDE_STATUS="accepted,adjudicated_accepted"
ALLOW_EMPTY="false"
WITH_REPLACE_IMPORT="false"
STEP_TIMEOUT_SECONDS="90"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --out-dir) OUT_DIR="$2"; shift 2 ;;
    --status) STATUS_FILTER="$2"; shift 2 ;;
    --publish-include-status) PUBLISH_INCLUDE_STATUS="$2"; shift 2 ;;
    --allow-empty) ALLOW_EMPTY="true"; shift ;;
    --with-replace-import) WITH_REPLACE_IMPORT="true"; shift ;;
    --step-timeout-seconds) STEP_TIMEOUT_SECONDS="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="${OUT_DIR}/${TS}"
STATE_JSONL="${RUN_DIR}/synonyms_state_snapshot.full.jsonl"
AUDIT_JSONL="${RUN_DIR}/synonyms_audit_snapshot.full.jsonl"
PUBLISH_OUT="${RUN_DIR}/synonyms.auto.hardening.json"
REPORT_MD="${RUN_DIR}/checklist.md"
REPORT_JSON="${RUN_DIR}/checklist.json"

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
  shift
  echo "[CHECK_START] ${name}"
  if timeout "${STEP_TIMEOUT_SECONDS}" "$@"; then
    record_result "${name}" "PASS" "ok"
    echo "[CHECK_PASS] ${name}"
    return 0
  fi
  record_result "${name}" "FAIL" "command_failed"
  echo "[CHECK_FAIL] ${name}"
  return 1
}

run_check "export_snapshot" \
  env PYTHONPATH=. "${PYTHON_BIN}" tools/export_synonyms_state_snapshot.py \
  --run-id "syn-hardening-${TS}" \
  --out-state-jsonl "${STATE_JSONL}" \
  --out-audit-jsonl "${AUDIT_JSONL}" \
  --status "${STATUS_FILTER}" \
  --validate || true

run_check "import_dry_run" \
  env PYTHONPATH=. "${PYTHON_BIN}" tools/import_synonyms_state_snapshot.py \
  --state-jsonl "${STATE_JSONL}" \
  --audit-jsonl "${AUDIT_JSONL}" \
  --validate \
  --dry-run || true

if [[ "${WITH_REPLACE_IMPORT}" == "true" ]]; then
  run_check "import_replace" \
    env PYTHONPATH=. "${PYTHON_BIN}" tools/import_synonyms_state_snapshot.py \
    --state-jsonl "${STATE_JSONL}" \
    --audit-jsonl "${AUDIT_JSONL}" \
    --validate \
    --replace-existing || true
else
  record_result "import_replace" "SKIP" "not_requested"
fi

run_check "publish_dictionary" \
  env PYTHONPATH=. "${PYTHON_BIN}" tools/publish_synonyms_auto.py \
  --out "${PUBLISH_OUT}" \
  --validate \
  --include-status "${PUBLISH_INCLUDE_STATUS}" || true

# Runtime consistency check:
# - published dictionary must be valid JSON object with entries list
# - global_safe entries must resolve through flat fallback
# - other entries must resolve only with their domain/facet context
if env PYTHONPATH=. "${PYTHON_BIN}" -c '
import json
import sys
from pathlib import Path

from app.normalization.loader import load_runtime_taxonomy
from app.normalization.runtime import NormalizationContext, resolve_canonical_term

publish_out = Path(sys.argv[1])
allow_empty = sys.argv[2].lower() == "true"
state_jsonl = Path(sys.argv[3])

if not publish_out.exists():
    raise SystemExit("publish_output_missing")
payload = json.loads(publish_out.read_text(encoding="utf-8"))
entries = payload.get("entries", [])
if not isinstance(entries, list):
    raise SystemExit("entries_not_list")

state_rows = 0
if state_jsonl.exists():
    state_rows = sum(1 for line in state_jsonl.read_text(encoding="utf-8").splitlines() if line.strip())
if state_rows == 0 and not allow_empty:
    raise SystemExit("empty_state_snapshot")

if entries:
    first = entries[0]
    alias = str(first.get("alias", "")).strip()
    canonical = str(first.get("canonical", "")).strip()
    runtime_taxonomy = load_runtime_taxonomy(
        taxonomy_release_path=Path("conf/normalization/taxonomy.release.json"),
        taxonomy_auto_path=Path("conf/normalization/taxonomy.auto.json"),
        synonyms_release_path=Path("conf/normalization/synonyms.release.json"),
        synonyms_auto_path=publish_out,
    )
    alias_key = alias.lower().replace("_", " ").replace("-", " ")
    alias_key = " ".join(alias_key.split())
    if first.get("domain_scope") == "global_safe":
        resolved = runtime_taxonomy.get("aliases", {}).get(alias_key, "")
        if not resolved:
            raise SystemExit("runtime_global_alias_not_resolved")
        if canonical and resolved != canonical:
            raise SystemExit("runtime_global_alias_mismatch")
    else:
        result = resolve_canonical_term(
            term=alias,
            source_category=str(first.get("category") or "skills"),
            taxonomy=runtime_taxonomy,
            context=NormalizationContext(
                domain=str(first.get("domain") or "unknown"),
                domain_confidence=1.0,
                facet_hint=str(first.get("facet") or "") or None,
                context_source="hardening_check",
            ),
        )
        if result.canonical != canonical:
            raise SystemExit("runtime_context_alias_mismatch")
        if result.resolution_status != "resolved":
            raise SystemExit("runtime_context_alias_not_resolved")
' "${PUBLISH_OUT}" "${ALLOW_EMPTY}" "${STATE_JSONL}"; then
  record_result "runtime_consistency" "PASS" "ok"
  echo "[CHECK_PASS] runtime_consistency"
else
  record_result "runtime_consistency" "FAIL" "command_failed"
  echo "[CHECK_FAIL] runtime_consistency"
fi

# Domain-aware runtime consistency check:
# - without context ambiguous alias must remain unresolved
# - with domain context ambiguous alias must resolve to the correct canonical
if env PYTHONPATH=. "${PYTHON_BIN}" -c '
import json
import sys
import tempfile
from pathlib import Path

from app.normalization.loader import load_runtime_taxonomy
from app.normalization.runtime import (
    NormalizationContext,
    resolve_canonical_term,
)

publish_out = Path(sys.argv[1])
if not publish_out.exists():
    raise SystemExit("publish_output_missing")

payload = json.loads(publish_out.read_text(encoding="utf-8"))
if not isinstance(payload, dict):
    raise SystemExit("publish_payload_not_object")

with tempfile.TemporaryDirectory() as tmpdir:
    tmpdir_path = Path(tmpdir)
    taxonomy_release = tmpdir_path / "taxonomy.release.json"
    taxonomy_auto = tmpdir_path / "taxonomy.auto.json"
    synonyms_release = tmpdir_path / "synonyms.release.json"
    synonyms_test = tmpdir_path / "synonyms.domain-aware.test.json"

    taxonomy_stub = {
        "version": 1,
        "aliases": {},
        "categories": {"skills": [], "tools": [], "stack": []},
    }
    synonyms_stub = {"version": 1, "entries": [], "aliases": {}}
    taxonomy_release.write_text(json.dumps(taxonomy_stub), encoding="utf-8")
    taxonomy_auto.write_text(json.dumps(taxonomy_stub), encoding="utf-8")
    synonyms_release.write_text(json.dumps(synonyms_stub), encoding="utf-8")

    payload.setdefault("entries", [])
    payload["entries"].extend([
        {
            "alias": "ROM",
            "canonical": "Read-Only Memory",
            "domain": "it_software_daten",
            "facet": "technology_stack",
            "category": "skills",
            "skill_class": "professional",
            "status": "accepted",
        },
        {
            "alias": "ROM",
            "canonical": "Range of Motion",
            "domain": "gesundheitswesen_pflege_medizin",
            "facet": "skill_requirement",
            "category": "skills",
            "skill_class": "professional",
            "status": "accepted",
        },
    ])
    synonyms_test.write_text(json.dumps(payload), encoding="utf-8")

    runtime_taxonomy = load_runtime_taxonomy(
        taxonomy_release_path=taxonomy_release,
        taxonomy_auto_path=taxonomy_auto,
        synonyms_release_path=synonyms_release,
        synonyms_auto_path=synonyms_test,
    )

    no_context = resolve_canonical_term(
        term="ROM",
        source_category="skills",
        taxonomy=runtime_taxonomy,
        context=NormalizationContext(),
    )
    if no_context.resolution_status != "unresolved":
        raise SystemExit("ambiguous_without_context_must_be_unresolved")
    if no_context.resolution_source != "ambiguous_without_context":
        raise SystemExit("ambiguous_without_context_wrong_source")

    it_context = NormalizationContext(
        domain="it_software_daten",
        domain_confidence=1.0,
        facet_hint="technology_stack",
        context_source="hardening_check",
    )
    it_resolved = resolve_canonical_term(
        term="ROM",
        source_category="skills",
        taxonomy=runtime_taxonomy,
        context=it_context,
    )
    if it_resolved.canonical != "Read-Only Memory":
        raise SystemExit("domain_context_not_resolved_to_it")
    if it_resolved.resolution_status != "resolved":
        raise SystemExit("domain_context_expected_resolved_status")

    med_context = NormalizationContext(
        domain="gesundheitswesen_pflege_medizin",
        domain_confidence=1.0,
        facet_hint="skill_requirement",
        context_source="hardening_check",
    )
    med_resolved = resolve_canonical_term(
        term="ROM",
        source_category="skills",
        taxonomy=runtime_taxonomy,
        context=med_context,
    )
    if med_resolved.canonical != "Range of Motion":
        raise SystemExit("domain_context_not_resolved_to_medical")
    if med_resolved.resolution_status != "resolved":
        raise SystemExit("domain_context_expected_resolved_status")
' "${PUBLISH_OUT}"; then
  record_result "domain_aware_runtime_consistency" "PASS" "ok"
  echo "[CHECK_PASS] domain_aware_runtime_consistency"
else
  record_result "domain_aware_runtime_consistency" "FAIL" "command_failed"
  echo "[CHECK_FAIL] domain_aware_runtime_consistency"
fi

{
  echo "# Synonyms Hardening Check (${TS})"
  echo
  echo "- run_dir: \`${RUN_DIR}\`"
  echo "- status_filter: \`${STATUS_FILTER}\`"
  echo "- publish_include_status: \`${PUBLISH_INCLUDE_STATUS}\`"
  echo "- allow_empty: \`${ALLOW_EMPTY}\`"
  echo "- with_replace_import: \`${WITH_REPLACE_IMPORT}\`"
  echo
  echo "## Checklist"
  for i in "${!CHECK_NAMES[@]}"; do
    echo "$((i+1)). ${CHECK_NAMES[$i]}: ${CHECK_STATUS[$i]} (${CHECK_DETAILS[$i]})"
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
    echo "hardening_check_failed report=${REPORT_MD}"
    exit 1
  fi
done

echo "hardening_check_ok report=${REPORT_MD}"
