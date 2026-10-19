// MongoDB Playground
// Сценарий:
// 1) Берем последние версии вакансий из stellenangebote по referenznummer.
// 2) Подтягиваем последние версии деталей из jobdetails по referenznummer.
// 3) Собираем "полный текст вакансии" из всех верхнеуровневых строковых полей
//    обеих коллекций.
// 4) Считаем среднее число символов по meta, details и combined.
//
// Важно:
// - это именно оценка по строковым полям верхнего уровня;
// - вложенные объекты и нестроковые поля не считаются;
// - для быстрой оценки объема текста этого обычно достаточно.

use("jobcenter");

db.getCollection("stellenangebote").aggregate([
  // Шаг 1. Последняя версия вакансии по referenznummer.
  { $sort: { referenznummer: 1, updated_at: -1, _id: -1 } },
  {
    $group: {
      _id: "$referenznummer",
      vacancy: { $first: "$$ROOT" }
    }
  },
  { $replaceRoot: { newRoot: "$vacancy" } },

  // Шаг 2. Последняя версия details для той же referenznummer.
  {
    $lookup: {
      from: "jobdetails",
      let: { ref: "$referenznummer" },
      pipeline: [
        { $match: { $expr: { $eq: ["$referenznummer", "$$ref"] } } },
        { $sort: { updated_at: -1, aenderungsdatum_dt: -1, _id: -1 } },
        { $limit: 1 }
      ],
      as: "details"
    }
  },
  { $set: { details: { $first: "$details" } } },

  // Шаг 3. Оставляем только актуальные пары, где details найден.
  { $match: { details: { $ne: null } } },

  // Шаг 4. Берем только строковые поля верхнего уровня из vacancy и details.
  {
    $set: {
      vacancy_string_fields: {
        $filter: {
          input: { $objectToArray: "$$ROOT" },
          as: "item",
          cond: { $eq: [{ $type: "$$item.v" }, "string"] }
        }
      },
      details_string_fields: {
        $filter: {
          input: { $objectToArray: "$details" },
          as: "item",
          cond: { $eq: [{ $type: "$$item.v" }, "string"] }
        }
      }
    }
  },

  // Шаг 5. Склеиваем строковые поля в единый текст и считаем длину.
  {
    $set: {
      vacancy_text: {
        $reduce: {
          input: {
            $map: {
              input: "$vacancy_string_fields",
              as: "item",
              in: "$$item.v"
            }
          },
          initialValue: "",
          in: {
            $cond: [
              { $eq: ["$$value", ""] },
              "$$this",
              { $concat: ["$$value", "\n", "$$this"] }
            ]
          }
        }
      },
      details_text: {
        $reduce: {
          input: {
            $map: {
              input: "$details_string_fields",
              as: "item",
              in: "$$item.v"
            }
          },
          initialValue: "",
          in: {
            $cond: [
              { $eq: ["$$value", ""] },
              "$$this",
              { $concat: ["$$value", "\n", "$$this"] }
            ]
          }
        }
      }
    }
  },
  {
    $set: {
      combined_text: {
        $cond: [
          { $eq: ["$vacancy_text", ""] },
          "$details_text",
          {
            $cond: [
              { $eq: ["$details_text", ""] },
              "$vacancy_text",
              { $concat: ["$vacancy_text", "\n", "$details_text"] }
            ]
          }
        ]
      }
    }
  },
  {
    $set: {
      vacancy_chars: { $strLenCP: "$vacancy_text" },
      details_chars: { $strLenCP: "$details_text" },
      combined_chars: { $strLenCP: "$combined_text" }
    }
  },

  // Шаг 6. Общая статистика по длине полного текста вакансии.
  {
    $group: {
      _id: null,
      vacancies_count: { $sum: 1 },
      avg_vacancy_chars: { $avg: "$vacancy_chars" },
      avg_details_chars: { $avg: "$details_chars" },
      avg_combined_chars: { $avg: "$combined_chars" },
      min_combined_chars: { $min: "$combined_chars" },
      max_combined_chars: { $max: "$combined_chars" }
    }
  },
  {
    $project: {
      _id: 0,
      vacancies_count: 1,
      avg_vacancy_chars: { $round: ["$avg_vacancy_chars", 2] },
      avg_details_chars: { $round: ["$avg_details_chars", 2] },
      avg_combined_chars: { $round: ["$avg_combined_chars", 2] },
      min_combined_chars: 1,
      max_combined_chars: 1
    }
  }
]);
