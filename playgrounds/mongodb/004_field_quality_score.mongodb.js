// MongoDB Playground
// Цель:
// Дать итоговую оценку качества полей (quality_score), чтобы понять,
// какие поля реально полезны для поиска/ранжирования.
//
// Источник:
// - уникальные последние версии stellenangebote
// - + последние версии jobdetails по referenznummer
//
// Метрики:
// - coverage_meaningful_percent: доля уникальных записей, где поле осмысленно заполнено
// - distinct_ratio_percent: насколько поле вариативно (не константное)
// - avg_text_len_meaningful: средняя длина осмысленных строковых значений
// - text_informativeness_percent: нормализованный показатель "насколько поле содержательное" (для строк)
//
// Итог:
// quality_score = 0.60 * coverage + 0.25 * variability + 0.15 * text_informativeness
//
// Примечание:
// Это эвристический скор, не абсолютная истина.

use("jobcenter");

db.getCollection("stellenangebote").aggregate([
  // 1) Уникальные последние вакансии.
  { $sort: { referenznummer: 1, updated_at: -1, _id: -1 } },
  {
    $group: {
      _id: "$referenznummer",
      vacancy: { $first: "$$ROOT" }
    }
  },
  { $replaceRoot: { newRoot: "$vacancy" } },

  // 2) Последние details.
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
  { $match: { details: { $ne: null } } },

  // 3) Объединяем в единый документ для профилирования.
  {
    $project: {
      source_doc: {
        meta: "$$ROOT",
        details: "$details"
      }
    }
  },

  // 4) Флэттим поля и сразу собираем признаки для качества.
  {
    $set: {
      flat_fields: {
        $function: {
          lang: "js",
          args: ["$source_doc"],
          body: function (doc) {
            const PLACEHOLDERS = new Set([
              "",
              "null",
              "none",
              "n/a",
              "na",
              "k.a.",
              "keine_angaben",
              "keine_angabe",
              "nicht_relevant",
              "unbekannt",
              "unknown",
              "keine angaben",
              "keine angabe"
            ]);

            const SYSTEM_PATTERNS = [
              /(^|\.)(_id)$/i,
              /(^|\.)(created_at|updated_at|last_checked_at|missing_since)$/i,
              /(^|\.)(meta_hash|details_hash|input_hash)$/i,
              /(^|\.)(run_id|seen_at|old)$/i,
              /(^|\.)(modifikationstimestamp_dt|aenderungsdatum_dt|last_seen_remote_updated_at_dt)$/i,
              /(^|\.)(last_seen_remote_updated_at)$/i
            ];

            function isSystemField(path) {
              return SYSTEM_PATTERNS.some((re) => re.test(path));
            }

            function valueType(v) {
              if (v === null || v === undefined) return "null";
              if (Array.isArray(v)) return "array";
              if (v instanceof Date) return "date";
              return typeof v;
            }

            function isFilled(v) {
              if (v === null || v === undefined) return false;
              if (typeof v === "string") return v.trim().length > 0;
              if (Array.isArray(v)) return v.length > 0;
              if (v instanceof Date) return true;
              if (typeof v === "object") return Object.keys(v).length > 0;
              return true;
            }

            function isMeaningful(v) {
              if (!isFilled(v)) return false;

              if (typeof v === "string") {
                const s = v.trim().toLowerCase();
                if (PLACEHOLDERS.has(s)) return false;
                return true;
              }
              if (typeof v === "number") return Number.isFinite(v);
              if (typeof v === "boolean") return true;
              if (v instanceof Date) return true;
              if (Array.isArray(v)) return v.some((x) => isMeaningful(x));
              if (typeof v === "object") return Object.keys(v).length > 0;
              return false;
            }

            function normalizeForDistinct(v) {
              if (v === null || v === undefined) return "__NULL__";
              if (typeof v === "string") return v.trim().toLowerCase();
              if (typeof v === "number" || typeof v === "boolean") return String(v);
              if (v instanceof Date) return v.toISOString();
              if (Array.isArray(v)) {
                return JSON.stringify(v.map((x) => normalizeForDistinct(x)));
              }
              if (typeof v === "object") {
                // Для объектов берём ключи как грубую сигнатуру.
                return "__OBJ_KEYS__:" + Object.keys(v).sort().join(",");
              }
              return String(v);
            }

            function textLen(v) {
              if (typeof v !== "string") return 0;
              return v.trim().length;
            }

            const acc = {};

            function touch(path, v) {
              if (!path) return;
              if (!acc[path]) {
                acc[path] = {
                  field: path,
                  system: isSystemField(path),
                  value_type: valueType(v),
                  filled: false,
                  meaningful: false,
                  normalized_value: null,
                  text_len: 0
                };
              }
              const cur = acc[path];
              const filled = isFilled(v);
              const meaningful = isMeaningful(v);

              cur.filled = cur.filled || filled;
              cur.meaningful = cur.meaningful || meaningful;
              if (meaningful) {
                cur.normalized_value = normalizeForDistinct(v);
                cur.text_len = Math.max(cur.text_len, textLen(v));
              }
            }

            function walk(node, path) {
              if (node === null || node === undefined) {
                touch(path, node);
                return;
              }

              if (Array.isArray(node)) {
                touch(path, node);
                for (const el of node) {
                  if (el && typeof el === "object" && !Array.isArray(el)) {
                    for (const k of Object.keys(el)) {
                      walk(el[k], path ? path + "[]." + k : "[]." + k);
                    }
                  }
                }
                return;
              }

              if (node instanceof Date || typeof node !== "object") {
                touch(path, node);
                return;
              }

              const keys = Object.keys(node);
              if (keys.length === 0) {
                touch(path, node);
                return;
              }
              for (const k of keys) {
                const p = path ? path + "." + k : k;
                walk(node[k], p);
              }
            }

            walk(doc, "");
            return Object.values(acc);
          }
        }
      }
    }
  },

  // 5) stats + totals.
  {
    $facet: {
      stats: [
        { $unwind: "$flat_fields" },
        {
          $group: {
            _id: "$flat_fields.field",
            docs_with_field: { $sum: 1 },
            filled_count: { $sum: { $cond: ["$flat_fields.filled", 1, 0] } },
            meaningful_count: { $sum: { $cond: ["$flat_fields.meaningful", 1, 0] } },
            system_field: { $max: { $cond: ["$flat_fields.system", 1, 0] } },
            value_types: { $addToSet: "$flat_fields.value_type" },
            normalized_values: {
              $addToSet: {
                $cond: ["$flat_fields.meaningful", "$flat_fields.normalized_value", "$$REMOVE"]
              }
            },
            text_len_sum: {
              $sum: {
                $cond: ["$flat_fields.meaningful", "$flat_fields.text_len", 0]
              }
            }
          }
        }
      ],
      totals: [{ $count: "total_unique_records" }]
    }
  },
  {
    $project: {
      stats: 1,
      total_unique_records: {
        $ifNull: [{ $arrayElemAt: ["$totals.total_unique_records", 0] }, 0]
      }
    }
  },
  { $unwind: "$stats" },
  {
    $replaceRoot: {
      newRoot: {
        $mergeObjects: ["$stats", { total_unique_records: "$total_unique_records" }]
      }
    }
  },

  // 6) Производные метрики.
  {
    $set: {
      distinct_count: { $size: "$normalized_values" },
      avg_text_len_meaningful: {
        $cond: [
          { $gt: ["$meaningful_count", 0] },
          { $round: [{ $divide: ["$text_len_sum", "$meaningful_count"] }, 2] },
          0
        ]
      },
      coverage_meaningful_percent: {
        $cond: [
          { $gt: ["$total_unique_records", 0] },
          {
            $round: [
              { $multiply: [{ $divide: ["$meaningful_count", "$total_unique_records"] }, 100] },
              2
            ]
          },
          0
        ]
      }
    }
  },
  {
    $set: {
      distinct_ratio_percent: {
        $cond: [
          { $gt: ["$meaningful_count", 0] },
          {
            $round: [
              { $multiply: [{ $divide: ["$distinct_count", "$meaningful_count"] }, 100] },
              2
            ]
          },
          0
        ]
      },
      // Нормализуем текстовую информативность в диапазон [0..100] по простой функции насыщения.
      text_informativeness_percent: {
        $cond: [
          {
            $or: [
              { $in: ["string", "$value_types"] },
              { $in: ["array", "$value_types"] }
            ]
          },
          {
            $round: [
              {
                $min: [
                  100,
                  { $multiply: [{ $divide: ["$avg_text_len_meaningful", 120] }, 100] }
                ]
              },
              2
            ]
          },
          0
        ]
      }
    }
  },

  // 7) Итоговый quality_score.
  {
    $set: {
      quality_score: {
        $round: [
          {
            $add: [
              { $multiply: ["$coverage_meaningful_percent", 0.60] },
              { $multiply: ["$distinct_ratio_percent", 0.25] },
              { $multiply: ["$text_informativeness_percent", 0.15] }
            ]
          },
          2
        ]
      }
    }
  },

  // 8) Чистый output + отсев системных.
  { $match: { system_field: 0 } },
  {
    $project: {
      _id: 0,
      field: "$_id",
      quality_score: 1,
      coverage_meaningful_percent: 1,
      distinct_ratio_percent: 1,
      text_informativeness_percent: 1,
      total_unique_records: 1,
      docs_with_field: 1,
      meaningful_count: 1,
      distinct_count: 1,
      avg_text_len_meaningful: 1,
      value_types: 1
    }
  },
  { $sort: { quality_score: -1, field: 1 } }
]);
