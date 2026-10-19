// MongoDB Playground
// Fully flattened Regensburg vacancy export:
// - latest stellenangebote document
// - latest jobdetails document
// - one flat row per referenznummer
// - field prefixes:
//   - sa__... for stellenangebote
//   - jd__... for jobdetails
// Join key: referenznummer
// City filter: stellenangebote.arbeitsort.ort / arbeitsort.Ort == "Regensburg"
//
// Notes:
// - nested object keys are flattened with "__"
// - arrays are serialized as JSON strings for export-friendliness

use("jobcenter");

db.getCollection("stellenangebote").aggregate([
  // Step 1. Keep only the latest stellenangebote version per referenznummer.
  { $sort: { referenznummer: 1, updated_at: -1, _id: -1 } },
  {
    $group: {
      _id: "$referenznummer",
      vacancy: { $first: "$$ROOT" }
    }
  },
  { $replaceRoot: { newRoot: "$vacancy" } },

  // Step 2. Normalize city access because source data may contain ort or Ort.
  {
    $set: {
      arbeitsort_city: {
        $ifNull: ["$arbeitsort.ort", "$arbeitsort.Ort"]
      }
    }
  },

  // Step 3. Filter only Regensburg vacancies.
  {
    $match: {
      arbeitsort_city: "Regensburg"
    }
  },

  // Step 4. Preserve the full stellenangebote document before lookup.
  {
    $set: {
      stellenangebote_doc: "$$ROOT"
    }
  },

  // Step 5. Pull only the latest jobdetails version for the same referenznummer.
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

  // Step 6. Flatten both source documents into one export row.
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
]);
