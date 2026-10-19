#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/export_regensburg_full_join.sh [options]

Options:
  --out FILE          Output JSON file
                      Default: exports/regensburg_full_join.json
  --env-file FILE     Env file to load before export
                      Default: .env
  --help              Show help

Behavior:
  - loads variables from .env
  - requires MONGODB_URI
  - exports full merged records for Regensburg vacancies
    (latest stellenangebote + latest jobdetails by referenznummer)
EOF
}

OUT_FILE="exports/regensburg_full_join.json"
ENV_FILE=".env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --out) OUT_FILE="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ -z "${MONGODB_URI:-}" ]]; then
  echo "MONGODB_URI is not set. Provide it in ${ENV_FILE} or environment." >&2
  exit 1
fi

mkdir -p "$(dirname "${OUT_FILE}")"

mongosh "${MONGODB_URI}" --quiet --eval '
const jobcenterDb = db.getSiblingDB("jobcenter");
const result = jobcenterDb.getCollection("stellenangebote").aggregate([
  { $sort: { referenznummer: 1, updated_at: -1, _id: -1 } },
  {
    $group: {
      _id: "$referenznummer",
      vacancy: { $first: "$$ROOT" }
    }
  },
  { $replaceRoot: { newRoot: "$vacancy" } },
  {
    $set: {
      arbeitsort_city: {
        $ifNull: ["$arbeitsort.ort", "$arbeitsort.Ort"]
      }
    }
  },
  {
    $match: {
      arbeitsort_city: "Regensburg"
    }
  },
  {
    $set: {
      stellenangebote_doc: "$$ROOT"
    }
  },
  {
    $lookup: {
      from: "jobdetails",
      let: { ref: "$referenznummer" },
      pipeline: [
        {
          $match: {
            $expr: { $eq: ["$referenznummer", "$$ref"] }
          }
        },
        { $sort: { updated_at: -1, aenderungsdatum_dt: -1, _id: -1 } },
        { $limit: 1 }
      ],
      as: "jobdetails_doc"
    }
  },
  {
    $set: {
      jobdetails_doc: { $first: "$jobdetails_doc" }
    }
  },
  {
    $project: {
      _id: 0,
      referenznummer: 1,
      arbeitsort_city: 1,
      stellenangebote: "$stellenangebote_doc",
      jobdetails: "$jobdetails_doc"
    }
  },
  { $sort: { referenznummer: 1 } }
]).toArray();
print(EJSON.stringify(result, null, 2));
' > "${OUT_FILE}"

echo "export_ok out=${OUT_FILE}"
