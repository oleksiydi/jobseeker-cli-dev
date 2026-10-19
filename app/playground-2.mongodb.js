// MongoDB Playground
// Use Ctrl+Space inside a snippet or a string literal to trigger completions.

// The current database to use.
use("jobcenter");

// db.jobdetails.distinct("aenderungsdatum", { /* Filter */ })

// Schnelle Prüfung der Abdeckung von jobdetails durch aktive Stellen.
// 1) Wie viele jobdetails haben ein passendes aktives stellenangebot (is_active != false)
db.jobdetails.aggregate([
  {
    $lookup: {
      from: "stellenangebote",
      let: { ref: "$referenznummer" },
      pipeline: [
        {
          $match: {
            $expr: {
              $and: [
                { $eq: ["$referenznummer", "$$ref"] },
                { $ne: ["$is_active", false] }
              ]
            }
          }
        },
        { $limit: 1 }
      ],
      as: "sa_active"
    }
  },
  { $match: { "sa_active.0": { $exists: true } } },
  { $count: "details_matching_active" }
]);

// 2) Wie viele jobdetails haben KEIN passendes aktives stellenangebot
db.jobdetails.aggregate([
  {
    $lookup: {
      from: "stellenangebote",
      let: { ref: "$referenznummer" },
      pipeline: [
        {
          $match: {
            $expr: {
              $and: [
                { $eq: ["$referenznummer", "$$ref"] },
                { $ne: ["$is_active", false] }
              ]
            }
          }
        },
        { $limit: 1 }
      ],
      as: "sa_active"
    }
  },
  { $match: { "sa_active.0": { $exists: false } } },
  { $count: "details_without_active" }
]);

// 3) Kontroll-Totalwerte
db.getCollection("stellenangebote").countDocuments({ is_active: { $ne: false } });
db.getCollection("jobdetails").countDocuments({});




// === Gemeinsame Basis für Join + Datumsnormalisierung ===
const baseJoin = [
  {
    $lookup: {
      from: "stellenangebote",
      localField: "referenznummer",
      foreignField: "referenznummer",
      as: "sa"
    }
  },
  { $unwind: "$sa" },
  {
    $set: {
      jd_dt: {
        $switch: {
          branches: [
            { case: { $eq: [{ $type: "$aenderungsdatum" }, "date"] }, then: "$aenderungsdatum" },
            {
              case: { $eq: [{ $type: "$aenderungsdatum" }, "string"] },
              then: {
                $dateFromString: {
                  dateString: "$aenderungsdatum",
                  timezone: "UTC",
                  onError: null,
                  onNull: null
                }
              }
            }
          ],
          default: null
        }
      },
      sa_dt: {
        $switch: {
          branches: [
            { case: { $eq: [{ $type: "$sa.modifikationsTimestamp" }, "date"] }, then: "$sa.modifikationsTimestamp" },
            {
              case: { $eq: [{ $type: "$sa.modifikationsTimestamp" }, "string"] },
              then: {
                $dateFromString: {
                  dateString: "$sa.modifikationsTimestamp",
                  timezone: "UTC",
                  onError: null,
                  onNull: null
                }
              }
            }
          ],
          default: null
        }
      }
    }
  },
  {
    $set: {
      diff_ms: {
        $cond: [
          { $and: [{ $ne: ["$sa_dt", null] }, { $ne: ["$jd_dt", null] }] },
          { $subtract: ["$sa_dt", "$jd_dt"] },
          null
        ]
      }
    }
  }
];


// 1) Differenz pro Stelle anzeigen
db.jobdetails.aggregate([
  ...baseJoin,
  { $match: { diff_ms: { $ne: null } } },
  {
    $project: {
      _id: 0,
      referenznummer: 1,
      aenderungsdatum: 1,
      modifikationsTimestamp: "$sa.modifikationsTimestamp",
      jd_dt: 1,
      sa_dt: 1,
      diff_ms: 1,
      diff_sec: { $divide: ["$diff_ms", 1000] },
      diff_min: { $divide: ["$diff_ms", 60000] },
      sign: {
        $switch: {
          branches: [
            { case: { $gt: ["$diff_ms", 0] }, then: "positive" },
            { case: { $lt: ["$diff_ms", 0] }, then: "negative" }
          ],
          default: "zero"
        }
      }
    }
  },
  { $sort: { diff_ms: -1 } }
]);


// 2) Anzahl positive / negative / zero / null
db.jobdetails.aggregate([
  ...baseJoin,
  {
    $group: {
      _id: null,
      total_joined: { $sum: 1 },
      diff_null: { $sum: { $cond: [{ $eq: ["$diff_ms", null] }, 1, 0] } },
      diff_negative: { $sum: { $cond: [{ $lt: ["$diff_ms", 0] }, 1, 0] } },
      diff_zero: { $sum: { $cond: [{ $eq: ["$diff_ms", 0] }, 1, 0] } },
      diff_positive: { $sum: { $cond: [{ $gt: ["$diff_ms", 0] }, 1, 0] } }
    }
  },
  { $project: { _id: 0 } }
]);

/*

// 3) Nur negative Werte
db.jobdetails.aggregate([
  ...baseJoin,
  { $match: { diff_ms: { $lt: 0 } } },
  { $count: "negative_count" }
]);

// 4) Nur positive Werte
db.jobdetails.aggregate([
  ...baseJoin,
  { $match: { diff_ms: { $gt: 0 } } },
  { $count: "positive_count" }
]);



// 5) Gruppen mit gleicher Differenz + Anzahl Einträge pro Gruppe
db.jobdetails.aggregate([
  ...baseJoin,
  { $match: { diff_ms: { $ne: null } } },
  {
    $group: {
      _id: "$diff_ms",
      count: { $sum: 1 },
      example_refs: { $push: "$referenznummer" }
    }
  },
  {
    $project: {
      _id: 0,
      diff_ms: "$_id",
      count: 1,
      // Damit die Ausgabe klein bleibt, nur die ersten 5 referenznummer je Gruppe
      example_refs: { $slice: ["$example_refs", 5] }
    }
  },
  { $sort: { count: -1, diff_ms: 1 } }
]);

*/


db.stellenangebote.aggregate([
  {
    $lookup: {
      from: "stellenangebote_history",
      localField: "referenznummer",
      foreignField: "referenznummer",
      as: "history"
    }
  }
]);
