#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/freeze_taxonomy_release.sh [options]

Purpose:
  Promote reviewed auto dictionaries to release dictionaries:
  - taxonomy.auto.json  -> taxonomy.release.json
  - synonyms.auto.json  -> synonyms.release.json (optional if file exists)

Safety:
  - Fails when ALLOW_PROFILE_LEARNING=true or ALLOW_VACANCY_LEARNING=true,
    unless --force is provided.
  - Creates timestamped backups in logs/normalization/freeze/<UTC_TS>/.

Options:
  --taxonomy-auto PATH       Default: conf/normalization/taxonomy.auto.json
  --taxonomy-release PATH    Default: conf/normalization/taxonomy.release.json
  --synonyms-auto PATH       Default: conf/normalization/synonyms.auto.json
  --synonyms-release PATH    Default: conf/normalization/synonyms.release.json
  --no-synonyms              Skip synonyms promotion
  --force                    Allow freeze even if learning flags are true
  -h, --help                 Show help
EOF
}

TAXO_AUTO="conf/normalization/taxonomy.auto.json"
TAXO_RELEASE="conf/normalization/taxonomy.release.json"
SYN_AUTO="conf/normalization/synonyms.auto.json"
SYN_RELEASE="conf/normalization/synonyms.release.json"
FREEZE_SYNONYMS="true"
FORCE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --taxonomy-auto) TAXO_AUTO="$2"; shift 2 ;;
    --taxonomy-release) TAXO_RELEASE="$2"; shift 2 ;;
    --synonyms-auto) SYN_AUTO="$2"; shift 2 ;;
    --synonyms-release) SYN_RELEASE="$2"; shift 2 ;;
    --no-synonyms) FREEZE_SYNONYMS="false"; shift ;;
    --force) FORCE="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

allow_profile="${ALLOW_PROFILE_LEARNING:-false}"
allow_vacancy="${ALLOW_VACANCY_LEARNING:-false}"
if [[ "${FORCE}" != "true" ]]; then
  if [[ "${allow_profile,,}" == "true" || "${allow_vacancy,,}" == "true" || "${allow_profile}" == "1" || "${allow_vacancy}" == "1" ]]; then
    echo "Refusing freeze: learning flags must be disabled for release." >&2
    echo "Set ALLOW_PROFILE_LEARNING=false and ALLOW_VACANCY_LEARNING=false, or use --force." >&2
    exit 1
  fi
fi

if [[ ! -f "${TAXO_AUTO}" ]]; then
  echo "taxonomy auto file not found: ${TAXO_AUTO}" >&2
  exit 1
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
FREEZE_DIR="logs/normalization/freeze/${TS}"
mkdir -p "${FREEZE_DIR}"
LOG_FILE="${FREEZE_DIR}/freeze.log"

{
  echo "[START] $(date -u +%Y-%m-%dT%H:%M:%SZ) freeze taxonomy release"
  echo "[CFG] taxonomy_auto=${TAXO_AUTO} taxonomy_release=${TAXO_RELEASE} synonyms_auto=${SYN_AUTO} synonyms_release=${SYN_RELEASE} freeze_synonyms=${FREEZE_SYNONYMS}"
  echo "[CFG] ALLOW_PROFILE_LEARNING=${allow_profile} ALLOW_VACANCY_LEARNING=${allow_vacancy} FORCE=${FORCE}"

  mkdir -p "$(dirname "${TAXO_RELEASE}")"
  cp "${TAXO_AUTO}" "${TAXO_RELEASE}"
  echo "[STEP] taxonomy promoted: ${TAXO_AUTO} -> ${TAXO_RELEASE}"

  if [[ -f "${TAXO_RELEASE}" ]]; then
    cp "${TAXO_RELEASE}" "${FREEZE_DIR}/taxonomy.release.json"
  fi

  if [[ "${FREEZE_SYNONYMS}" == "true" ]]; then
    if [[ -f "${SYN_AUTO}" ]]; then
      mkdir -p "$(dirname "${SYN_RELEASE}")"
      cp "${SYN_AUTO}" "${SYN_RELEASE}"
      echo "[STEP] synonyms promoted: ${SYN_AUTO} -> ${SYN_RELEASE}"
      cp "${SYN_RELEASE}" "${FREEZE_DIR}/synonyms.release.json"
    else
      echo "[STEP] synonyms auto file not found, skipping: ${SYN_AUTO}"
    fi
  fi

  echo "[END] $(date -u +%Y-%m-%dT%H:%M:%SZ) freeze completed"
  echo "FREEZE_DIR=${FREEZE_DIR}"
  echo "LOG_FILE=${LOG_FILE}"
} 2>&1 | tee "${LOG_FILE}"

