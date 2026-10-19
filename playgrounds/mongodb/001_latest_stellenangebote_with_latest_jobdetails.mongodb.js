// MongoDB Playground
// Сценарий:
// 1) Берем только последнюю версию вакансии из stellenangebote по referenznummer.
// 2) Для каждой такой вакансии подтягиваем только последнюю версию деталей из jobdetails.
// 3) На выходе получаем "актуальную пару": vacancy + details.

use("jobcenter");

db.getCollection("stellenangebote").aggregate([
  // Шаг 1. Сортируем так, чтобы самая свежая запись по referenznummer была первой.
  // Логика свежести:
  // - сначала по updated_at (новее выше),
  // - затем по _id как стабильный tie-breaker.
  { $sort: { referenznummer: 1, updated_at: -1, _id: -1 } },

  // Шаг 2. Группируем по referenznummer и берем первую запись из отсортированного набора.
  // Это и есть "последняя версия" вакансии в stellenangebote.
  {
    $group: {
      _id: "$referenznummer",
      vacancy: { $first: "$$ROOT" }
    }
  },

  // Шаг 3. Возвращаем структуру документа обратно как обычный vacancy-документ.
  { $replaceRoot: { newRoot: "$vacancy" } },

  // Шаг 4. Делаем lookup в jobdetails по той же referenznummer.
  // Внутри lookup также сортируем и ограничиваем до 1 документа,
  // чтобы взять только последнюю версию details.
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

  // Шаг 5. Превращаем массив details (0..1 элемент) в один объект.
  { $set: { details: { $first: "$details" } } },

  // Шаг 6 (опционально). Оставляем только вакансии, где детали найдены.
  // Убери этот stage, если хочешь видеть также вакансии без деталей.
  { $match: { details: { $ne: null } } },

  // Шаг 7. Убираем служебные поля, чтобы выдача была чище.
  {
    $project: {
      old: 0,
      "details.old": 0
    }
  }
]);
