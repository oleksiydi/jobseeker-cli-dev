#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/bootstrap_laptop_state.sh [options]

Goal:
  Prepare a fresh laptop to run the project in a state close to the server setup:
  - create .env from .env.example (if missing)
  - create virtualenv and install dependencies
  - start local infra (mongo + qdrant) via docker compose
  - optionally restore Mongo from backup archive
  - import default profile + analysis prompt
  - run qdrant bootstrap

Options:
  --env-file PATH           Env file path (default: .env)
  --venv-path PATH          Virtualenv path (default: .venv)
  --python-bin BIN          Python interpreter for venv creation (default: python3)
  --compose-file PATH       Docker compose file (default: docker/docker-compose.yml)
  --restore-archive FILE    Optional mongo archive (.archive.gz) to restore
  --drop-on-restore         Drop collections before restore
  --skip-deps               Skip pip install -r requirements.txt
  --skip-infra              Skip docker compose up for mongo/qdrant
  --skip-seed-import        Skip import of default profile + prompt
  --skip-qdrant-bootstrap   Skip python -m app.bootstrap_qdrant
  --help                    Show help

Examples:
  # Basic setup (fresh laptop)
  bash tools/bootstrap_laptop_state.sh

  # Setup + restore db from archive
  bash tools/bootstrap_laptop_state.sh \
    --restore-archive backups/mongo_jobcenter_20260321T120000Z.archive.gz \
    --drop-on-restore

Runbook:
  docs/operations/project-state-transfer.md
EOF
}

ENV_FILE=".env"
VENV_PATH=".venv"
PYTHON_BIN="python3"
COMPOSE_FILE="docker/docker-compose.yml"
RESTORE_ARCHIVE=""
DROP_ON_RESTORE="false"
SKIP_DEPS="false"
SKIP_INFRA="false"
SKIP_SEED_IMPORT="false"
SKIP_QDRANT_BOOTSTRAP="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --venv-path) VENV_PATH="$2"; shift 2 ;;
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --compose-file) COMPOSE_FILE="$2"; shift 2 ;;
    --restore-archive) RESTORE_ARCHIVE="$2"; shift 2 ;;
    --drop-on-restore) DROP_ON_RESTORE="true"; shift ;;
    --skip-deps) SKIP_DEPS="true"; shift ;;
    --skip-infra) SKIP_INFRA="true"; shift ;;
    --skip-seed-import) SKIP_SEED_IMPORT="true"; shift ;;
    --skip-qdrant-bootstrap) SKIP_QDRANT_BOOTSTRAP="true"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

echo "[1/7] Ensure env file"
if [[ ! -f "${ENV_FILE}" ]]; then
  if [[ ! -f ".env.example" ]]; then
    echo "ERROR: .env.example not found in repository root" >&2
    exit 1
  fi
  cp .env.example "${ENV_FILE}"
  echo "Created ${ENV_FILE} from .env.example"
else
  echo "Using existing ${ENV_FILE}"
fi

echo "[2/7] Ensure virtualenv"
if [[ ! -d "${VENV_PATH}" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_PATH}"
  echo "Created virtualenv at ${VENV_PATH}"
else
  echo "Using existing virtualenv at ${VENV_PATH}"
fi

PY="${VENV_PATH}/bin/python"
PIP="${VENV_PATH}/bin/pip"

if [[ ! -x "${PY}" ]]; then
  echo "ERROR: python not found in ${VENV_PATH}" >&2
  exit 1
fi

if [[ "${SKIP_DEPS}" != "true" ]]; then
  echo "[3/7] Install dependencies"
  "${PIP}" install -r requirements.txt
else
  echo "[3/7] Skip dependencies installation"
fi

if [[ "${SKIP_INFRA}" != "true" ]]; then
  echo "[4/7] Start infra (mongo + qdrant)"
  docker compose --env-file "${ENV_FILE}" -f "${COMPOSE_FILE}" up -d mongo qdrant
else
  echo "[4/7] Skip infra startup"
fi

if [[ -n "${RESTORE_ARCHIVE}" ]]; then
  echo "[5/7] Restore Mongo archive"
  restore_cmd=(bash tools/mongo_restore.sh
    --archive "${RESTORE_ARCHIVE}"
    --env-file "${ENV_FILE}"
    --compose-file "${COMPOSE_FILE}"
    --service mongo
    --db jobcenter
  )
  if [[ "${DROP_ON_RESTORE}" == "true" ]]; then
    restore_cmd+=(--drop)
  fi
  "${restore_cmd[@]}"
else
  echo "[5/7] No restore archive provided, skip DB restore"
fi

if [[ "${SKIP_SEED_IMPORT}" != "true" ]]; then
  echo "[6/7] Import default profile and prompt"
  "${PY}" tools/import_profiles.py --file conf/profiles/default.profile.json
  "${PY}" tools/import_analysis_prompts.py --file conf/prompts/default.prompt_task.json
else
  echo "[6/7] Skip default seeds import"
fi

if [[ "${SKIP_QDRANT_BOOTSTRAP}" != "true" ]]; then
  echo "[7/7] Bootstrap qdrant collection"
  "${PY}" -m app.bootstrap_qdrant
else
  echo "[7/7] Skip qdrant bootstrap"
fi

echo ""
echo "Done."
echo "Next checks:"
echo "  docker compose --env-file ${ENV_FILE} -f ${COMPOSE_FILE} ps"
echo "  ${PY} -m app.search_jobs --query \"python\" --profile-key default --limit 5"
echo "  docs/operations/project-state-transfer.md"
echo ""
echo "Note:"
echo "  Fill secrets in ${ENV_FILE} (OPENAI_API_KEY / OPENROUTER_API_KEY / passwords) before external LLM runs."
