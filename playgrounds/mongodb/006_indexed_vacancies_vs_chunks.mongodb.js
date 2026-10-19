/**
 * 006_indexed_vacancies_vs_chunks.mongodb.js
 *
 * Диагностика расхождения:
 * "вакансий просмотрено N" vs "points в Qdrant = M"
 *
 * Важно:
 * - points в Qdrant = число чанков (а не число вакансий);
 * - текущий прогон может быть полностью skipped и не менять points;
 * - этот файл анализирует состояние в Mongo (matching_index_state).
 */

use("jobcenter");

// 1) Сколько уникальных вакансий вообще есть в состоянии индекса.
db.getCollection("matching_index_state").aggregate([
  { $group: { _id: "$referenznummer" } },
  { $count: "unique_vacancies_in_state" }
]);

// 2) Распределение по статусам индексации.
db.getCollection("matching_index_state").aggregate([
  {
    $group: {
      _id: "$index_status",
      vacancies: { $sum: 1 }
    }
  },
  { $sort: { vacancies: -1 } }
]);

// 3) Сколько вакансий реально "indexed" и сколько у них суммарно чанков.
// Это главный ориентир, который обычно должен быть близок к points в Qdrant.
db.getCollection("matching_index_state").aggregate([
  { $match: { index_status: "indexed" } },
  {
    $group: {
      _id: null,
      indexed_vacancies: { $sum: 1 },
      total_chunks_in_indexed_vacancies: { $sum: { $ifNull: ["$chunks_count", 0] } },
      avg_chunks_per_indexed_vacancy: { $avg: { $ifNull: ["$chunks_count", 0] } }
    }
  },
  { $project: { _id: 0 } }
]);

// 4) Для контроля: какие вакансии имеют indexed, но 0 чанков (нештатно).
db.getCollection("matching_index_state").find(
  {
    index_status: "indexed",
    $or: [
      { chunks_count: { $exists: false } },
      { chunks_count: 0 }
    ]
  },
  {
    _id: 0,
    referenznummer: 1,
    index_status: 1,
    chunks_count: 1,
    updated_at: 1,
    indexed_at: 1
  }
).sort({ updated_at: -1 }).limit(50);

// 5) Последние 30 записей, чтобы увидеть свежие skip/indexed причины.
db.getCollection("matching_index_state").find(
  {},
  {
    _id: 0,
    referenznummer: 1,
    index_status: 1,
    skip_reason: 1,
    chunks_count: 1,
    indexed_at: 1,
    updated_at: 1,
    last_seen_at: 1
  }
).sort({ updated_at: -1, indexed_at: -1 }).limit(30);

