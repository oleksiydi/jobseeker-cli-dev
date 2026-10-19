// MongoDB Playground
// Check how many jobdetails documents have null, empty, whitespace-only,
// or usable non-empty values in stellenangebotsTitel.

use("jobcenter");

db.getCollection("jobdetails").aggregate([
  {
    $facet: {
      total: [{ $count: "count" }],

      null_or_missing: [
        { $match: { stellenangebotsTitel: null } },
        { $count: "count" }
      ],

      empty_string: [
        { $match: { stellenangebotsTitel: "" } },
        { $count: "count" }
      ],

      whitespace_only: [
        { $match: { stellenangebotsTitel: { $type: "string", $regex: /^\s+$/ } } },
        { $count: "count" }
      ],

      non_empty_after_trim: [
        { $match: { stellenangebotsTitel: { $type: "string", $regex: /\S/ } } },
        { $count: "count" }
      ]
    }
  }
]);
