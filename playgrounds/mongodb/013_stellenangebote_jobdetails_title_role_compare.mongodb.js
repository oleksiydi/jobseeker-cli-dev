// MongoDB Playground
// Show comparable role/title fields side by side for each vacancy:
// - stellenangebote.beruf
// - stellenangebote.titel
// - jobdetails.hauptberuf
// - jobdetails.stellenangebotsTitel
//
// Join key: referenznummer
// Base collection: stellenangebote

use("jobcenter");

db.getCollection("stellenangebote").aggregate([
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
        {
          $project: {
            _id: 0,
            hauptberuf: 1,
            stellenangebotsTitel: 1
          }
        },
        { $limit: 1 }
      ],
      as: "jobdetails_match"
    }
  },
  {
    $unwind: {
      path: "$jobdetails_match",
      preserveNullAndEmptyArrays: true
    }
  },
  {
    $project: {
      _id: 0,
      referenznummer: 1,
      stellenangebote_beruf: "$beruf",
      stellenangebote_titel: "$titel",
      jobdetails_hauptberuf: "$jobdetails_match.hauptberuf",
      jobdetails_stellenangebotsTitel: "$jobdetails_match.stellenangebotsTitel"
    }
  },
  { $sort: { referenznummer: 1 } }
]);
