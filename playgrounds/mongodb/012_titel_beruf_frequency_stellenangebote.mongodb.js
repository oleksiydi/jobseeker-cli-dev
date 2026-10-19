// MongoDB Playground
// Count unique values of `titel` and `beruf` in stellenangebote.
//
// The result has two arrays:
// - titel_frequency
// - beruf_frequency
//
// Null, missing, empty and whitespace-only values are excluded.

use("jobcenter");

db.getCollection("stellenangebote").aggregate([
  {
    $facet: {
      titel_frequency: [
        {
          $match: {
            titel: { $type: "string", $regex: /\S/ }
          }
        },
        {
          $group: {
            _id: "$titel",
            count: { $sum: 1 }
          }
        },
        {
          $project: {
            _id: 0,
            titel: "$_id",
            count: 1
          }
        },
        { $sort: { count: -1, titel: 1 } }
      ],

      beruf_frequency: [
        {
          $match: {
            beruf: { $type: "string", $regex: /\S/ }
          }
        },
        {
          $group: {
            _id: "$beruf",
            count: { $sum: 1 }
          }
        },
        {
          $project: {
            _id: 0,
            beruf: "$_id",
            count: 1
          }
        },
        { $sort: { count: -1, beruf: 1 } }
      ]
    }
  }
]);
