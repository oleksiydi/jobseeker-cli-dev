// MongoDB Playground
// Сценарий:
// 1) Берем уникальные последние версии вакансий из stellenangebote.
// 2) Подтягиваем последние версии details из jobdetails.
// 3) Считаем, у скольких уникальных записей поле заполнено/пусто.
//
// ВАЖНО:
// Ниже можно поменять поле для проверки. По умолчанию:
// details.stellenangebotsBeschreibung

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

  // Шаг 3. Явно оставляем только записи, где details существует.
  { $match: { details: { $ne: null } } },

  // Шаг 4. Укажи здесь целевое поле, заполненность которого нужно считать.
  // Пример альтернативы:
  // target_value: "$details.hauptberuf"
  {
    $set: {
      target_value: "$details.stellenangebotsBeschreibung"
    }
  },

  // Шаг 5. Нормализуем строку и отмечаем "заполнено / не заполнено".
  {
    $set: {
      target_value_str: {
        $trim: {
          input: { $toString: { $ifNull: ["$target_value", ""] } }
        }
      }
    }
  },
  {
    $set: {
      is_filled: { $gt: [{ $strLenCP: "$target_value_str" }, 0] }
    }
  },

  // Шаг 6. Финальная сводка по уникальным вакансиям.
  {
    $group: {
      _id: null,
      unique_records_total: { $sum: 1 },
      field_filled_count: { $sum: { $cond: ["$is_filled", 1, 0] } },
      field_empty_count: { $sum: { $cond: ["$is_filled", 0, 1] } }
    }
  },
  {
    $project: {
      _id: 0,
      unique_records_total: 1,
      field_filled_count: 1,
      field_empty_count: 1,
      field_filled_percent: {
        $cond: [
          { $gt: ["$unique_records_total", 0] },
          {
            $round: [
              {
                $multiply: [
                  { $divide: ["$field_filled_count", "$unique_records_total"] },
                  100
                ]
              },
              2
            ]
          },
          0
        ]
      }
    }
  }
]);
