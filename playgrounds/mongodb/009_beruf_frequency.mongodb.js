// MongoDB Playground
// Count unique values of stellenangebote.beruf
// and show how many documents use each value.

use("jobcenter");

db.getCollection("stellenangebote").aggregate([
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
]);
