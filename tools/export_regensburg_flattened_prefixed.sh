#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash tools/export_regensburg_flattened_prefixed.sh [options]

Options:
  --out FILE          Output JSON file
                      Default: exports/regensburg_flattened_prefixed.json
  --env-file FILE     Env file to load before export
                      Default: .env
  --help              Show help

Behavior:
  - loads variables from .env
  - requires MONGODB_URI
  - exports fully flattened Regensburg vacancies
    (latest stellenangebote + latest jobdetails by referenznummer)
  - output fields are prefixed:
    - sa__... for stellenangebote
    - jd__... for jobdetails
EOF
}

OUT_FILE="exports/regensburg_flattened_prefixed.json"
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
      flat_row: {
        $function: {
          lang: "js",
          args: ["$referenznummer", "$arbeitsort_city", "$stellenangebote_doc", "$jobdetails_doc"],
          body: function(referenznummer, arbeitsortCity, stellenangeboteDoc, jobdetailsDoc) {
            function flattenInto(target, prefix, value, pathParts) {
              if (value === null || value === undefined) {
                target[prefix + pathParts.join("__")] = null;
                return;
              }

              if (Array.isArray(value)) {
                target[prefix + pathParts.join("__")] = JSON.stringify(value);
                return;
              }

              if (typeof value === "object") {
                var keys = Object.keys(value);
                if (keys.length === 0) {
                  target[prefix + pathParts.join("__")] = "{}";
                  return;
                }
                for (var i = 0; i < keys.length; i += 1) {
                  var key = keys[i];
                  flattenInto(target, prefix, value[key], pathParts.concat([key]));
                }
                return;
              }

              target[prefix + pathParts.join("__")] = value;
            }

            var row = {
              referenznummer: referenznummer,
              arbeitsort_city: arbeitsortCity
            };

            if (stellenangeboteDoc && typeof stellenangeboteDoc === "object") {
              var saKeys = Object.keys(stellenangeboteDoc);
              for (var i = 0; i < saKeys.length; i += 1) {
                var saKey = saKeys[i];
                flattenInto(row, "sa__", stellenangeboteDoc[saKey], [saKey]);
              }
            }

            if (jobdetailsDoc && typeof jobdetailsDoc === "object") {
              var jdKeys = Object.keys(jobdetailsDoc);
              for (var j = 0; j < jdKeys.length; j += 1) {
                var jdKey = jdKeys[j];
                flattenInto(row, "jd__", jobdetailsDoc[jdKey], [jdKey]);
              }
            }

            return row;
          }
        }
      }
    }
  },
  { $replaceRoot: { newRoot: "$flat_row" } },
  { $sort: { referenznummer: 1 } }
]).toArray();
print(EJSON.stringify(result, null, 2));
' > "${OUT_FILE}"

echo "export_ok out=${OUT_FILE}"
