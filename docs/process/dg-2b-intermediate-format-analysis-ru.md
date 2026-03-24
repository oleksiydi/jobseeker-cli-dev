# DG-2B: Разбор Decision Gate по Формату Intermediate Synonyms Artifacts

Audience: `Human + AI`  
Status: `decision-support`  
Last updated: `2026-03-24`

## Зачем нужен этот документ

Этот документ фиксирует предметную разборку `DG-2B` из
`docs/process/taxonomy-synonyms-codex-implementation-plan-ru.md`.

Decision gate:
- нужно определить, в каком формате хранить intermediate artifacts для stages
  `synonyms.candidates` и `synonyms.adjudicated`;
- решение влияет на append/resume behavior, batch processing, validator CLI,
  отладку, стоимость ошибок в длинных прогонах и удобство интеграции с LLM stages;
- это нельзя выбирать молча в коде, потому что от этого зависит весь рабочий ритм
  pipeline между generation, gating, adjudication и publish.

## 1. В чём состоит задача или проблема

В synonyms pipeline промежуточные артефакты не являются "финальным словарем".
Это рабочие слои между стадиями:

- candidate generation;
- auto-gate classification;
- LLM adjudication;
- publisher.

У этих слоев другие требования, чем у release dictionary:

- запись должна быть удобна для append;
- нужны resume/checkpoint-friendly форматы;
- удобно валидировать и читать частично;
- при ошибке не хочется терять весь batch;
- нужно уметь быстро смотреть хвост файла и отдельные записи.

Сейчас в проекте уже есть сильный operational precedent:

- `vacancy_terms*.jsonl`
- `vacancy_taxonomy_process*.jsonl`
- `vacancy_taxonomy_llm_calls*.jsonl`
- `vacancy_taxonomy_checkpoint*.jsonl`
- `synonyms.pending.jsonl`

Но это ещё не означает, что `DG-2B` закрыт автоматически.
Нужно проверить, действительно ли тот же формат остается лучшим именно для
`synonyms.candidates` и `synonyms.adjudicated`.

## 2. Почему здесь вообще нужен decision gate

Это не только вопрос "что удобнее открыть в редакторе".

Решение по `DG-2B` влияет на:

- возможность append-only pipeline;
- стоимость сбоя при длинном прогоне;
- простоту LLM adjudication batching;
- формат validator CLI;
- читаемость и диффируемость артефактов;
- сложность dedup/checkpoint logic;
- способность pipeline обрабатывать большие корпуса без полной перезаписи файла.

Если формат выбрать неудачно, то дальше:

- либо каждая стадия будет переписывать весь файл целиком;
- либо придётся городить лишние конвертеры;
- либо debugging станет дорогим;
- либо adjudication batching будет неудобным.

## 3. Какие именно неоднозначности нужно разрешить

Нужно заранее ответить на следующие вопросы:

- intermediate artifacts должны быть record-oriented или batch-oriented?
- нормально ли, если одна битая запись ломает весь артефакт?
- нужна ли возможность append/resume без полной пересборки файла?
- должны ли intermediate файлы быть удобны для `tail`, `jq`, простых grep-like проверок?
- насколько важен удобный batch handoff в LLM adjudication worker?
- где мы хотим держать atomic unit работы:
  - одна запись;
  - один batch/массив;
  - пачка файлов?

## 4. Реалистичные примеры

### Базовый простой пример

Candidate builder нашёл три новых пары:

- `k8s -> kubernetes`
- `sap bi -> sap_bi`
- `teamfähig -> teamfaehigkeit`

В JSONL это выглядит так:

```jsonl
{"run_id":"syn-20260324T100000Z","alias":"k8s","canonical":"kubernetes","domain":"it_software_daten","category":"stack","facet":"technology_stack","skill_class":"not_applicable","confidence":0.98,"source":"rule","candidate_status":"accepted","created_at":"2026-03-24T10:00:00+00:00"}
{"run_id":"syn-20260324T100000Z","alias":"sap bi","canonical":"sap_bi","domain":"finanzen_versicherung_recht","category":"tools","facet":"tool_or_platform","skill_class":"not_applicable","confidence":0.95,"source":"rule","candidate_status":"accepted","created_at":"2026-03-24T10:00:01+00:00"}
{"run_id":"syn-20260324T100000Z","alias":"teamfähig","canonical":"teamfaehigkeit","domain":"sonstiges","category":"skills","facet":"interpersonal_competency","skill_class":"interpersonal","confidence":0.84,"source":"llm","candidate_status":"needs_adjudication","created_at":"2026-03-24T10:00:02+00:00"}
```

В JSON array:

```json
[
  {"run_id":"syn-20260324T100000Z","alias":"k8s","canonical":"kubernetes","domain":"it_software_daten","category":"stack","facet":"technology_stack","skill_class":"not_applicable","confidence":0.98,"source":"rule","candidate_status":"accepted","created_at":"2026-03-24T10:00:00+00:00"},
  {"run_id":"syn-20260324T100000Z","alias":"sap bi","canonical":"sap_bi","domain":"finanzen_versicherung_recht","category":"tools","facet":"tool_or_platform","skill_class":"not_applicable","confidence":0.95,"source":"rule","candidate_status":"accepted","created_at":"2026-03-24T10:00:01+00:00"},
  {"run_id":"syn-20260324T100000Z","alias":"teamfähig","canonical":"teamfaehigkeit","domain":"sonstiges","category":"skills","facet":"interpersonal_competency","skill_class":"interpersonal","confidence":0.84,"source":"llm","candidate_status":"needs_adjudication","created_at":"2026-03-24T10:00:02+00:00"}
]
```

На маленьком объёме оба варианта рабочие.
Различия проявляются на длинных прогонах и при частичной обработке.

### Сложный пример 1: прогон оборвался на 18000-й записи

Сценарий:
- builder пишет `synonyms.candidates`;
- после 18000 записей процесс падает.

Если формат JSONL:
- всё, что уже было дописано как валидные строки, можно сохранить;
- resume может продолжить append;
- validator может пройти по строкам и показать только битые записи.

Если формат JSON array:
- возможно, файл остался без закрывающей `]`;
- один оборванный хвост делает невалидным весь JSON;
- для resume либо нужна полная перезапись, либо более сложный atomic-write choreography.

Это очень важный operational difference.

### Сложный пример 2: LLM adjudication работает батчами по 50 записей

Pipeline:
- candidates собраны целиком;
- worker берет только `candidate_status = needs_adjudication`;
- отправляет их в LLM батчами.

JSONL:
- легко стримить, фильтровать и chunk-ить по записям;
- можно строить батчи поверх одного линейного потока записей.

JSON array:
- сначала надо загрузить весь массив или писать отдельный parser/iterator;
- batch handling всё еще возможно, но operationally тяжелее.

При этом именно adjudication worker сам по себе может потом работать batch-oriented.
Но это не означает, что storage format промежуточного слоя тоже обязан быть batch JSON.

### Сложный пример 3: одна битая запись и ручная диагностика

Проблема:
- в артефакте появилась одна запись с `invalid_domain`.

JSONL:
- validator говорит `line=183 invalid_domain=...`;
- можно быстро открыть конкретную строку, исправить или отбросить;
- остальной файл остается читаемым.

JSON array:
- ошибка может сделать невалидным весь документ;
- line/offset диагностика хуже читается человеком;
- при ручной правке выше шанс случайно сломать структуру массива.

### Сложный пример 4: одно решение хорошо в одном месте и хуже в другом

Пример A: append-heavy candidate generation
- JSONL явно удобнее.

Пример B: маленький curated adjudication export, который хочется передать как один bundle в другой tool
- JSON array может быть удобнее как self-contained batch artifact.

Вывод:
- формат хранения intermediate pipeline и формат отдельного handoff-batch не обязательно должны совпадать.

## 5. Варианты решения

### Вариант A

Определение:
- primary intermediate format = JSONL;
- одна строка = одна запись;
- candidate/adjudication stages работают с record stream.

Как это работает:
- builder аппендит записи в `synonyms.candidates.jsonl`;
- classifier может писать `synonyms.adjudicated.jsonl` или аналогичный JSONL output;
- validator и diagnostics работают по строкам.

Плюсы:
- append-friendly;
- хорошо работает с resume/checkpoint logic;
- одна битая запись не делает логически бесполезным весь файл;
- удобно для streaming, `tail`, `jq -c`, grep-like отладки;
- уже соответствует operational pattern проекта.

Минусы:
- нет единого "объектного контейнера" вокруг файла;
- если нужны общие batch-level metadata, их надо хранить либо в каждой записи, либо в sidecar/meta file;
- для некоторых внешних инструментов JSON array воспринимается естественнее.

Риски:
- если проект начнет злоупотреблять огромными JSONL без нормальной partitioning policy, позже может понадобиться sharding по run/batch.

### Вариант B

Определение:
- primary intermediate format = JSON arrays;
- одна стадия пишет один JSON document с массивом записей.

Как это работает:
- builder собирает все candidates в памяти/во временном файле;
- затем пишет один batch JSON;
- следующие стадии читают целый массив.

Плюсы:
- один self-contained JSON document;
- удобно держать top-level metadata рядом с массивом;
- может быть удобнее для небольших handoff-batches.

Минусы:
- хуже для append/resume;
- при падении прогона риск потерять валидность всего файла;
- тяжелей operationally на длинных прогонах;
- менее удобен для line-oriented tooling и быстрой диагностики.

Риски:
- intermediate pipeline станет сильнее завязан на atomic full-file rewrites;
- это не очень хорошо сочетается с тем, как уже устроены process/checkpoint артефакты проекта.

### Вариант C

Определение:
- primary storage format = JSONL;
- но отдельные export/handoff bundles для adjudication допускаются как batch JSON files.

Как это работает:
- основной pipeline хранит `candidates` и `adjudicated` в JSONL;
- при необходимости формируется отдельный временный batch JSON только как transport format для конкретного worker step.

Плюсы:
- сохраняет operational преимущества JSONL;
- не запрещает удобный batch handoff там, где он действительно нужен;
- отделяет storage contract от transport contract.

Минусы:
- появляется второй формат в экосистеме pipeline;
- нужно явно описать, что является contract storage layer, а что transport artifact.

Риски:
- если это не описать четко, команда снова может начать путать primary contract и temporary handoff format.

## 6. Сравнение вариантов на конкретных сценариях

### Сценарий: большой append-heavy run

Пример:
- builder проходит 100k aggregated aliases и постепенно формирует candidates.

Вариант A:
- естественный fit.

Вариант B:
- придется собирать/переписывать большие JSON arrays.

Вариант C:
- storage остается удобным как в A.

Вывод:
- A и C лучше B.

### Сценарий: частичный rerun и resume

Пример:
- после падения нужно продолжить generation без потери уже обработанных записей.

Вариант A:
- хорошо сочетается с append/checkpoint.

Вариант B:
- хуже, потому что batch document может потребовать rebuild целого массива.

Вариант C:
- как A для storage.

Вывод:
- здесь JSONL имеет явное преимущество.

### Сценарий: compact batch handoff в LLM worker

Пример:
- передать в worker ровно 50 ambiguous candidates одним payload.

Вариант A:
- можно просто прочитать 50 строк и собрать запрос;
- storage не мешает.

Вариант B:
- batch format естественен, но это преимущество transport layer, а не обязательно storage contract.

Вариант C:
- позволяет explicit export batch without changing storage contract.

Вывод:
- потребность в batch handoff не доказывает необходимость batch storage.

## 7. Какой вариант я считаю предпочтительным и почему

Предпочтителен **Вариант C**, а если держаться строго двух вариантов gate — **Вариант A**.

Причина:
- как primary intermediate storage contract проекту лучше всего подходит JSONL;
- это согласуется с уже существующим operational style проекта;
- это лучше для append/resume/checkpoint/debug;
- при этом реальные LLM adjudication handoff-батчи можно делать отдельно и локально, не превращая batch JSON в основной persisted contract.

То есть practically:
- если `DG-2B` должен выбрать один основной storage format, я рекомендую **JSONL**;
- если хотим сформулировать решение точнее, то storage contract = JSONL, transport batches = optional JSON arrays.

## 8. На какие конкретные вопросы нужно ответить, чтобы закрыть DG-2B

Нужно зафиксировать:

- primary intermediate storage format:
  - JSONL или JSON arrays?
- должны ли `synonyms.candidates` и `synonyms.adjudicated` использовать один и тот же primary format?
- считаем ли мы допустимым отдельный batch export format для adjudication worker?
- где хранить batch-level metadata:
  - в каждой записи;
  - в sidecar file;
  - в top-level JSON object?
- является ли partial append/resume обязательным свойством intermediate pipeline?

Критические вопросы:
- primary storage format;
- можно ли отделять storage format от transport batch format;
- обязателен ли append/resume как часть contract expectation.

## 9. Какие вопросы действительно требуют явного выбора пользователя

По существу здесь есть один главный контрактный выбор:

- считать ли primary intermediate contract strictly record-oriented (`JSONL`),
  или делать intermediate artifacts batch-oriented (`JSON arrays`).

Второй, более тонкий выбор возникает только если вы хотите разрешить вариант C:

- допускаем ли мы отдельные temporary batch exports для adjudication worker,
  не считая их primary intermediate contract.

Остальные вопросы можно закрыть default-решениями.

## 10. Рекомендуемое решение по умолчанию

Если не задавать дополнительных ограничений, рекомендую закрыть `DG-2B` так:

- primary intermediate storage format = JSONL;
- `synonyms.candidates` и `synonyms.adjudicated` оба используют JSONL как основной persisted format;
- append/resume считается обязательным operational свойством intermediate pipeline;
- при необходимости допускаются отдельные temporary batch JSON exports для LLM worker, но они не считаются canonical contract artifacts.

Это решение лучше всего соответствует текущему состоянию проекта:
- проект уже heavily использует JSONL для рабочих логов и промежуточных слоев;
- pipeline ориентирован на append/checkpoint/resume;
- у intermediate artifacts больше operational, чем презентационная, роль.
