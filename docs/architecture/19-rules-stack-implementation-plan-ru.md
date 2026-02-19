# План внедрения: Pydantic + Pandera + JSONLogic (RU)

## Короткий ответ заранее: это production-подход?

Да, это нормальный production-подход для data/retrieval систем.

Почему:
1. Чёткое разделение ответственности:
- `Pydantic` — валидная структура объектов;
- `Pandera` — качество табличных/батч-данных;
- `JSONLogic` — управляемые бизнес-правила.
2. Правила можно менять без переписывания всего пайплайна.
3. Есть трассировка: почему запись прошла/не прошла и какие правила сработали.

Ограничение:
- нужен порядок и дисциплина версий правил, иначе будет “зоопарк”.

---

## 1) Что это за стек простыми словами

## 1.1 Pydantic

Что это:
- библиотека для строгих моделей данных в Python.

Что решает:
- гарантирует, что объект вакансии после merge имеет ожидаемые поля и типы;
- ранний фейл на кривом входе.

Пример задачи:
- `referenznummer` обязателен;
- `title`/`role` приводятся к строкам;
- `alternativBeruf*` могут быть `None`, но не “ломают” пайплайн.

## 1.2 Pandera

Что это:
- библиотека для проверки качества табличных данных (DataFrame).

Что решает:
- массовая проверка батча (`5000` вакансий): полнота, длины, доли пустых полей, выбросы.

Пример задачи:
- “доля `description_missing` > 40%” => предупреждение;
- “слишком длинные описания > N символов” => отдельный флаг.

## 1.3 JSONLogic

Что это:
- формат правил в JSON, который интерпретирует rule-engine.

Что решает:
- вынести if/else-политику из кода в конфиг (skip/reindex/fallback/labels/penalties).

Пример задачи:
- `"alternativBeruf1" == "false"` -> исключить из `ALT_ROLES`;
- если `description` пустое -> `description_missing=true` + synthetic chunk.

---

## 2) Архитектурная роль каждого слоя

Порядок в пайплайне:
1. `Merge` (`meta + details`).
2. `Pydantic` модель merged-вакансии.
3. `Normalization` (чистка значений).
4. `JSONLogic` rules (решения/флаги/маркеры).
5. `Pandera` quality-check батча.
6. Chunking -> Embeddings -> Qdrant upsert.
7. Mongo metadata + audit trail.

Важно:
- `Pydantic` и `Pandera` не конкурируют, они на разных уровнях.
- `JSONLogic` принимает решение, но опирается на уже нормализованные поля.

---

## 3) Что конкретно прописываем

## 3.1 Модели (Pydantic)

Создать модели:
1. `MergedVacancyRaw` — сырой merge до нормализации.
2. `MergedVacancyNormalized` — после нормализации, готов к chunking.
3. `ChunkRecord` — единица chunk-пайплайна.
4. `RuleEvaluationResult` — что вернул rule-engine.

Ключевые поля:
1. `referenznummer` (обязательное).
2. `title`, `role`, `description`, `location`, `employment`.
3. `alt_roles` (список строк).
4. флаги:
- `description_missing`
- `section_label`
- `section_confidence`

## 3.2 Схема качества (Pandera)

Для батча задаём:
1. обязательность `referenznummer`.
2. допустимый диапазон длины `description`.
3. долю пустых/псевдо-полей.
4. правило уникальности `referenznummer` в текущем срезе.

Типы проверок:
1. hard fail (критичные ошибки).
2. soft warning (не останавливаем пайплайн, но логируем).

## 3.3 Rule-set (JSONLogic)

Разнести по файлам:
1. `normalize_rules.v1.json`
2. `fallback_rules.v1.json`
3. `label_rules.v1.json`
4. `reindex_rules.v1.json`

Примеры правил:
1. `ALT_ROLES` cleanup (`"", "false", "null", "none", "-", "n/a"`).
2. `description_missing` detection.
3. synthetic chunk generation trigger.
4. `section_label=unknown`, если confidence ниже порога.
5. skip/reindex по `embedding_input_hash/model/chunking/preprocess`.

---

## 4) Кто “парсит и исполняет” правила

Нужен отдельный модуль-исполнитель:
- `app/matching/rule_engine.py`

Его задача:
1. загрузить JSONLogic-правила из `conf/rules/...`.
2. принять `MergedVacancyNormalized`.
3. вернуть:
- transformed fields;
- flags;
- reasons (какие правила сработали);
- итоговые решения (`skip/reindex/fallback`).

Важный production-момент:
- каждый rule-result должен сохранять:
  - `rule_set_version`
  - `applied_rules[]`
  - `decision_reasons[]`

---

## 5) Как это выполняется на практике (runtime)

Для каждой вакансии:
1. merge `meta + details`.
2. pydantic validate/coerce.
3. базовая нормализация строк.
4. запуск JSONLogic:
- чистка `alt_roles`;
- выставление `description_missing`;
- выбор fallback/synthetic chunk;
- предварительный `section_label`/`unknown`.
5. chunking + embeddings.
6. skip/reindex decision.
7. write Qdrant + Mongo metadata + audit.

Для батча:
1. после шага 3/4 формируем DataFrame.
2. гоняем Pandera checks.
3. пишем quality-report.

---

## 6) Что из ваших примеров будет обрабатываться

## 6.1 `alternativBeruf1 = "false"`

Что произойдёт:
1. JSONLogic rule пометит значение как псевдо-пустое.
2. поле удалится из `ALT_ROLES`.
3. в canonical text будет `ALT_ROLES:` (пусто).

## 6.2 Нет `description`

Что произойдёт:
1. `description_missing=true`.
2. создаётся synthetic chunk из role/title/location/employment.
3. в payload и Mongo попадёт этот флаг.

## 6.3 Неструктурированный длинный текст

Что произойдёт:
1. chunking даст блоки по смысловой связности.
2. `section_label` ставится эвристикой.
3. если уверенности нет -> `unknown` (это нормальный исход).

## 6.4 Слишком “грязные” данные

Что произойдёт:
1. Pydantic отловит критичные типовые проблемы.
2. Pandera покажет деградацию качества батча.
3. pipeline продолжится для валидных записей, а проблемные будут помечены reason-кодами.

---

## 7) Плюсы и минусы этого стека

Плюсы:
1. прозрачность и объяснимость решений;
2. легче сопровождать и безопасно менять;
3. правила можно версионировать независимо от алгоритмов embedding.

Минусы:
1. больше начальной инженерной работы;
2. нужно поддерживать документацию правил;
3. нужен audit-слой, иначе ценность rule-engine падает.

---

## 8) Пошаговый план внедрения (implementation backlog)

## Этап A (минимальный рабочий)
1. Добавить Pydantic-модели merged/normalized.
2. Добавить JSONLogic rule-engine для:
- `ALT_ROLES` cleanup;
- `description_missing`;
- synthetic chunk trigger;
- unknown label fallback.
3. Логировать `applied_rules`.

Критерий готовности:
- ваши текущие кейсы из `docs/architecture/18-...` воспроизводятся кодом без ручных if/else.

## Этап B (качество батча)
1. Добавить Pandera schema/checks.
2. Сформировать quality report (`json + log`).

Критерий готовности:
- есть отчёт по качеству каждой ночной загрузки.

## Этап C (прод-уровень)
1. Версионирование правил (`rule_set_version`).
2. Канареечный rollout правил (флагом).
3. Метрики по rule-hit/unknown-rate/fallback-rate.

Критерий готовности:
- можно безопасно менять правила и видеть эффект.

---

## 9) Рекомендуемая структура файлов

1. `conf/rules/normalize_rules.v1.json`
2. `conf/rules/fallback_rules.v1.json`
3. `conf/rules/label_rules.v1.json`
4. `conf/rules/reindex_rules.v1.json`
5. `app/matching/models.py` (Pydantic)
6. `app/matching/rule_engine.py` (JSONLogic executor)
7. `app/matching/quality_schema.py` (Pandera)
8. `app/matching/audit.py` (rule audit trail)

---

## 10) Что важно помнить команде

1. Не пытаться “всё решить LLM”.
2. Сначала детерминированная нормализация и правила.
3. `unknown` — это нормальный и честный результат, а не ошибка.
4. Без audit trail правила в проде быстро становятся непрозрачными.

---

## 11) Статус реализации (текущее состояние)

Выполнено:
1. Шаг 0 (планирование в `PLANS.md`).
2. Шаг 1 (базовые Pydantic-модели + нормализация + unit-тесты):
- `app/matching/models.py`
- `tests/test_matching_models.py`
3. Шаг 2 (JSONLogic rule engine + rule files + unit-тесты):
- `app/matching/rule_engine.py`
- `conf/rules/normalize_rules.v1.json`
- `conf/rules/fallback_rules.v1.json`
- `conf/rules/label_rules.v1.json`
- `conf/rules/reindex_rules.v1.json`
- `tests/test_matching_rule_engine.py`

Реализовано в коде Шага 1:
1. `MergedVacancyRaw.from_sources(...)` с fallback-приоритетом `details -> meta`.
2. `normalize_alt_roles(...)` с удалением псевдо-пустых значений:
- `""`, `"false"`, `"none"`, `"null"`, `"n/a"`, `"-"`, `"nan"`.
3. `MergedVacancyNormalized.from_raw(...)` и флаг `description_missing`.
4. `canonical_text()` по фиксированному шаблону.
5. Контрактные модели `ChunkRecord` и `RuleEvaluationResult` как база для следующих шагов.

Следующий шаг:
1. Шаг 3: `Pandera` quality checks (`app/matching/quality_schema.py`).
