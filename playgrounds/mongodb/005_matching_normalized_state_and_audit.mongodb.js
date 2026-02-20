/**
 * 005_matching_normalized_state_and_audit.mongodb.js
 *
 * Быстрые выборочные запросы по результатам normalisation/index-run.
 */

use("jobcenter");

// 1) Сводка статусов
db.getCollection("matching_index_state").aggregate([
  {
    $group: {
      _id: "$index_status",
      count: { $sum: 1 }
    }
  },
  { $sort: { count: -1 } }
]);

// 2) Последние записи состояния
db.getCollection("matching_index_state").find(
  {},
  {
    _id: 0,
    referenznummer: 1,
    index_status: 1,
    embedding_input_hash: 1,
    embedding_model: 1,
    chunking_version: 1,
    preprocess_version: 1,
    chunks_count: 1,
    quality_hard_passed: 1,
    quality_soft_passed: 1,
    quality_soft_warnings: 1,
    skip_reason: 1,
    indexed_at: 1,
    last_seen_at: 1
  }
).sort({ indexed_at: -1, last_seen_at: -1 }).limit(30);

// 3) Проблемные записи (если есть)
db.getCollection("matching_index_state").find(
  {
    index_status: { $in: ["quality_failed", "qdrant_failed"] }
  },
  {
    _id: 0,
    referenznummer: 1,
    index_status: 1,
    quality_hard_errors: 1,
    quality_soft_warnings: 1,
    qdrant_error: 1,
    updated_at: 1
  }
).sort({ updated_at: -1 }).limit(50);

// 4) Последние audit-события
db.getCollection("matching_audit").find(
  {},
  {
    _id: 0,
    run_id: 1,
    created_at: 1,
    referenznummer: 1,
    needs_reindex: 1,
    skip_reason: 1,
    applied_rules: 1,
    decision_reasons: 1,
    flags: 1
  }
).sort({ created_at: -1 }).limit(30);

// 5) Агрегации по последнему run_id
const latestRun = db.getCollection("matching_audit")
  .find({}, { run_id: 1, _id: 0 })
  .sort({ created_at: -1 })
  .limit(1)
  .toArray();

if (latestRun.length > 0) {
  const runId = latestRun[0].run_id;

  // 5.1) распределение skip/reindex
  db.getCollection("matching_audit").aggregate([
    { $match: { run_id: runId } },
    {
      $group: {
        _id: {
          needs_reindex: "$needs_reindex",
          skip_reason: "$skip_reason"
        },
        count: { $sum: 1 }
      }
    },
    { $sort: { count: -1 } }
  ]);

  // 5.2) топ причин decision_reasons
  db.getCollection("matching_audit").aggregate([
    { $match: { run_id: runId } },
    { $unwind: "$decision_reasons" },
    { $group: { _id: "$decision_reasons", count: { $sum: 1 } } },
    { $sort: { count: -1 } }
  ]);
}

