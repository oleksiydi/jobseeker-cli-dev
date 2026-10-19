// MongoDB Playground
// Show full structured vacancy data for Regensburg only:
// - latest stellenangebote document
// - latest jobdetails document
// Join key: referenznummer
// City filter: stellenangebote.arbeitsort.ort / arbeitsort.Ort == "Regensburg"

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

  // Step 6. Convert lookup array into a single object.
  {
    $set: {
      jobdetails_doc: { $first: "$jobdetails_doc" }
    }
  },

  // Step 7. Structured output: one merged record per vacancy.
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
]);
