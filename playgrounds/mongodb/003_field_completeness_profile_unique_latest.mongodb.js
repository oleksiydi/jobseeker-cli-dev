// MongoDB Playground
// Цель:
// Построить профиль заполненности "по каждому полю" для уникальных актуальных записей
// (последняя версия в stellenangebote + последняя версия в jobdetails),
// и отделить "реально полезные значения" от системных и placeholder-значений.
//
// Что показывает результат по каждому полю:
// - docs_with_field: в скольких уникальных записях поле вообще присутствует
// - filled_count: в скольких записях поле не пустое
// - meaningful_count: в скольких записях значение похоже на полезное, а не на "мусор/placeholder"
// - coverage_present_percent: процент присутствия поля от общего числа уникальных записей
// - coverage_meaningful_percent: процент полезной заполненности от общего числа уникальных записей
// - meaningful_within_present_percent: доля полезных среди присутствующих
// - system_field: поле выглядит системным (1) или предметным (0)
//
// Примечание: это эвристика, а не абсолютная истина.

use("jobcenter");

db.getCollection("stellenangebote").aggregate([
  // 1) Последняя версия вакансии по referenznummer.
  { $sort: { referenznummer: 1, updated_at: -1, _id: -1 } },
  {
    $group: {
      _id: "$referenznummer",
      vacancy: { $first: "$$ROOT" }
    }
  },
  { $replaceRoot: { newRoot: "$vacancy" } },

  // 2) Последняя версия details по referenznummer.
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

  // 3) Нормализуем документ: объединяем meta + details в одно дерево с префиксами.
  // Это упрощает профиль "по всем полям".
  {
    $project: {
      source_doc: {
        meta: "$$ROOT",
        details: "$details"
      }
    }
  },

  // 4) Плоский список полей через $function:
  // - рекурсивно обходим объект
  // - сворачиваем в уникальные пути (field path)
  // - считаем filled/meaningful/system по эвристикам
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

            // Поля, которые обычно системные и не несут полезного бизнес-смысла для матчей.
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

            function valueType(v) {
              if (v === null || v === undefined) return "null";
              if (Array.isArray(v)) return "array";
              if (v instanceof Date) return "date";
              return typeof v;
            }

            // Агрегируем по пути поля, чтобы в пределах одной записи не дублировать path.
            const acc = {};

            function touch(path, v) {
              if (!path) return;
              if (!acc[path]) {
                acc[path] = {
                  field: path,
                  filled: false,
                  meaningful: false,
                  system: isSystemField(path),
                  value_type: valueType(v)
                };
              }
              acc[path].filled = acc[path].filled || isFilled(v);
              acc[path].meaningful = acc[path].meaningful || isMeaningful(v);
            }

            function walk(node, path) {
              if (node === null || node === undefined) {
                touch(path, node);
                return;
              }

              if (Array.isArray(node)) {
                touch(path, node);
                // Для массивов объектов добавляем агрегированный путь вида field[].key
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

  // 5) Делаем две ветки:
  // - stats: агрегаты по полям
  // - totals: общее число уникальных записей (деноминатор процентов)
  {
    $facet: {
      stats: [
        { $unwind: "$flat_fields" },
        {
          $group: {
            _id: "$flat_fields.field",
            docs_with_field: { $sum: 1 },
            filled_count: {
              $sum: { $cond: ["$flat_fields.filled", 1, 0] }
            },
            meaningful_count: {
              $sum: { $cond: ["$flat_fields.meaningful", 1, 0] }
            },
            system_field: {
              $max: { $cond: ["$flat_fields.system", 1, 0] }
            },
            value_types: { $addToSet: "$flat_fields.value_type" }
          }
        }
      ],
      totals: [{ $count: "total_unique_records" }]
    }
  },

  // 6) Склеиваем totals в каждую строку stats и считаем проценты.
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
        $mergeObjects: [
          "$stats",
          { total_unique_records: "$total_unique_records" }
        ]
      }
    }
  },
  {
    $project: {
      _id: 0,
      field: "$_id",
      system_field: 1,
      value_types: 1,
      total_unique_records: 1,
      docs_with_field: 1,
      filled_count: 1,
      meaningful_count: 1,
      coverage_present_percent: {
        $cond: [
          { $gt: ["$total_unique_records", 0] },
          {
            $round: [
              {
                $multiply: [
                  { $divide: ["$docs_with_field", "$total_unique_records"] },
                  100
                ]
              },
              2
            ]
          },
          0
        ]
      },
      coverage_meaningful_percent: {
        $cond: [
          { $gt: ["$total_unique_records", 0] },
          {
            $round: [
              {
                $multiply: [
                  { $divide: ["$meaningful_count", "$total_unique_records"] },
                  100
                ]
              },
              2
            ]
          },
          0
        ]
      },
      meaningful_within_present_percent: {
        $cond: [
          { $gt: ["$docs_with_field", 0] },
          {
            $round: [
              {
                $multiply: [
                  { $divide: ["$meaningful_count", "$docs_with_field"] },
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
  },

  // 7) По умолчанию исключаем системные поля.
  // Если хочешь увидеть вообще всё, закомментируй этот stage.
  { $match: { system_field: 0 } },

  // 8) Сортировка: сначала самые "полезно заполненные".
  { $sort: { coverage_meaningful_percent: -1, field: 1 } }
]);
