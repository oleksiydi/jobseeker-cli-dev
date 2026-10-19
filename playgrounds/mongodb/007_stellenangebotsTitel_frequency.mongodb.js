// MongoDB Playground
// Count unique values of jobdetails.stellenangebotsTitel
// and show how many documents use each title.

use("jobcenter");

db.getCollection("jobdetails").aggregate([
  {
    $group: {
      _id: "$stellenangebotsTitel",
      count: { $sum: 1 }
    }
  },
  {
    $project: {
      _id: 0,
      stellenangebotsTitel: "$_id",
      count: 1
    }
  },
  { $sort: { count: -1, stellenangebotsTitel: 1 } }
]);
