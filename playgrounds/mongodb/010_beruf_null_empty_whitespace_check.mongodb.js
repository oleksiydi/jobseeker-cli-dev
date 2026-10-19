// MongoDB Playground
// Check how many stellenangebote documents have null, empty, whitespace-only,
// or usable non-empty values in beruf.

use("jobcenter");

db.getCollection("stellenangebote").aggregate([
  {
    $facet: {
      total: [{ $count: "count" }],

      null_or_missing: [
        { $match: { beruf: null } },
        { $count: "count" }
      ],

      empty_string: [
        { $match: { beruf: "" } },
        { $count: "count" }
      ],

      whitespace_only: [
        { $match: { beruf: { $type: "string", $regex: /^\s+$/ } } },
        { $count: "count" }
      ],

      non_empty_after_trim: [
        { $match: { beruf: { $type: "string", $regex: /\S/ } } },
        { $count: "count" }
      ]
    }
  }
]);
