# Codex-Ready Implementation Plan: Taxonomy/Synonyms Runtime

Audience: `Human + AI`  
Status: `execution-ready-plan`  
Last updated: `2026-03-24`

## Назначение документа

Этот документ превращает архитектурный план
`docs/process/taxonomy-synonyms-runtime-plan-ru.md`
в execution-ready план для пошаговой реализации в VS Code под контролем пользователя.

Цель:
- каждый шаг можно брать в работу отдельно;
- у каждого шага есть явный write scope по репозиторию;
- спорные места заранее выделены как `Decision Gate`;
- Codex не должен молча принимать спорные решения;
- для каждой итерации определен минимальный practical scope;
- есть проверяемые критерии завершения;
- понятно, какие шаги можно реализовывать уже сейчас, а какие требуют доопределения.

## Связанные документы

- `docs/process/taxonomy-synonyms-runtime-plan-ru.md`
- `docs/process/dg-1a-canonical-semantics-analysis-ru.md`
- `docs/process/dg-1a-variant-a-recommendation-ru.md`
- `docs/process/dg-1a-decision-closure-support-ru.md`
- `docs/process/dg-2a-runtime-format-analysis-ru.md`
- `docs/process/dg-2a-clean-migration-recommendation-ru.md`
- `docs/process/dg-2a-decision-closure-support-ru.md`
- `docs/process/dg-2b-intermediate-format-analysis-ru.md`

## Правила работы по этому плану

1. Перед началом каждого шага Codex обязан проверить `Decision Gate` этого шага.
2. Если `Decision Gate` не закрыт и выбор влияет на контракт, Codex не должен молча выбирать вариант.
3. Если выбор не влияет на внешний контракт и есть безопасный default, Codex может предложить default, но обязан явно его назвать.
4. Каждый шаг должен завершаться локальными проверками и кратким отчетом:
- что изменено;
- какие файлы затронуты;
- что проверено;
- что осталось открытым.
5. Если шаг меняет контракт или формат артефактов, нужно обновить docs/tests в этой же итерации.

## Статусы готовности шагов

- `ready_now`: можно брать в реализацию без дополнительных контрактных решений.
- `needs_gate`: сначала нужно закрыть хотя бы один `Decision Gate`.
- `depends_on_previous`: шаг нельзя начинать до завершения предыдущего шага.

## Фаза 0. Execution Baseline

### Шаг 0.1. Зафиксировать execution baseline и map существующего кода

1. Название шага:
   Зафиксировать execution baseline и map существующего кода.
2. Задача шага:
   собрать список уже существующих модулей, контрактов и тестов, которые будут переиспользованы.
3. Проблема, которую шаг решает:
   без явной карты текущего состояния легко дублировать уже существующие контракты и скрипты.
4. Как именно шаг решает проблему:
   составляется inventory по файлам `tools/*`, `app/normalization/*`, `conf/normalization/schemas/*`, `tests/*`, а также по существующим секциям `PLANS.md`.
5. Результат:
   зафиксирован список reusable компонентов и пробелов реализации.
6. Условия приемки:
   в execution report перечислены:
   - существующие схемы;
   - существующие loader/runtime части;
   - существующие тесты;
   - будущие/отсутствующие скрипты;
   - конфликтующие или устаревшие секции в `PLANS.md`.

Write scope:
- без изменения кода;
- допускается только обновление документации, если инвентаризация фиксируется в docs.

Decision Gate:
- `DG-0A`: фиксировать inventory в отдельном документе или только в отчете итерации.
  - Вариант A: отдельный markdown-файл в `docs/process/`.
  - Вариант B: только execution report в рамках итерации.
  - Recommended: `B`, если отдельный inventory не планируется поддерживать постоянно.

Minimal practical scope:
- только inventory + список реиспользуемых файлов.

Статус готовности:
- `ready_now`

## Фаза 1. Контракт Extraction Raw

### Шаг 1.1. Зафиксировать extraction policy `extractive-first`

1. Название шага:
   Зафиксировать extraction policy `extractive-first`.
2. Задача шага:
   определить, какие именно термины разрешено извлекать в raw-слой.
3. Проблема, которую шаг решает:
   текущий extraction допускает абстракции и переводы, что ломает deterministic runtime mapping.
4. Как именно шаг решает проблему:
   формализуется policy:
   - `term` должен быть surface-near;
   - `context_phrase` обязателен как evidence;
   - translated-only / abstract-only candidates запрещены;
   - canonical не должен подменять source evidence.
5. Результат:
   есть формальный extraction policy, на который можно опираться в prompt, валидаторе и тестах.
6. Условия приемки:
   policy зафиксирован в docs и отражен в тест-кейсах на допустимые/недопустимые примеры.

Write scope:
- `docs/process/taxonomy-synonyms-runtime-plan-ru.md`
- `tools/taxonomy_extract.py` (на следующем шаге реализации)
- `tests/test_build_taxonomy_from_vacancies.py` (на следующем шаге реализации)

Decision Gate:
- `DG-1A`: что считать допустимым `canonical`.
  - Вариант A: canonical может быть нормализованным internal key, но `term` обязан быть extractive.
  - Вариант B: и `term`, и `canonical` должны быть surface-near.
  - Recommended: `A`, так как дает стабильный internal key и не ломает runtime design.
  - Current decision status: closed-by-defaults in favor of Variant A; remaining work is implementation of generation/validation/provenance rules.

Minimal practical scope:
- только policy и примеры.

Проверки:
- doc review;
- тестовые примеры на 5-10 спорных кейсах.

Статус готовности:
- `needs_gate`

### Шаг 1.2. Обновить prompt/response contract extraction

1. Название шага:
   Обновить prompt/response contract extraction.
2. Задача шага:
   привести prompt и response expectations к принятой extraction policy.
3. Проблема, которую шаг решает:
   если policy не отражена в prompt/contract, модель продолжит возвращать абстракции.
4. Как именно шаг решает проблему:
   в `tools/taxonomy_extract.py` меняется system/user prompt; при необходимости добавляются post-parse guards.
5. Результат:
   extraction код и policy синхронизированы.
6. Условия приемки:
   prompt явно запрещает translation-only abstractions; локальные unit tests покрывают новые правила.

Write scope:
- `tools/taxonomy_extract.py`
- возможно `conf/normalization/schemas/vacancy_term_extraction.schema.json`
- `tests/test_build_taxonomy_from_vacancies.py`

Decision Gate:
- `DG-1B`: enforce на уровне prompt только или prompt + post-parse validator.
  - Вариант A: только prompt.
  - Вариант B: prompt + программная валидация после LLM.
  - Recommended: `B`, потому что только prompt недостаточно надежен.

Minimal practical scope:
- изменить prompt;
- добавить 1 простой validator rule;
- обновить тесты.

Проверки:
- `pytest` по taxonomy extraction тестам;
- dry-run на небольшой контрольной выборке.

Статус готовности:
- `needs_gate`

## Фаза 2. Контракт и пайплайн Synonyms

### Шаг 2.1. Уточнить runtime contract словаря синонимов

1. Название шага:
   Уточнить runtime contract словаря синонимов.
2. Задача шага:
   зафиксировать, какой именно формат словаря будет читать runtime и какие поля обязательны для publish.
3. Проблема, которую шаг решает:
   сейчас есть несколько уровней контрактов (`pending`, `dictionary`, legacy `aliases`), но publish/runtime контракт не полностью закрыт под новую схему.
4. Как именно шаг решает проблему:
   формируется точный publish contract для:
   - `synonyms.auto.json`
   - `synonyms.release.json`
   - merge behavior в loader.
5. Результат:
   runtime contract словаря зафиксирован до начала реализации генератора synonyms.
6. Условия приемки:
   схема/документ дают однозначный ответ:
   - какие поля обязательны;
   - что читает runtime;
   - что считается legacy-слоем;
   - как ведет себя merge.

Write scope:
- `docs/process/synonyms-contract-ru.md`
- возможно `conf/normalization/schemas/synonyms_dictionary.schema.json`
- возможно `app/normalization/loader.py` tests, но не обязательно в этом шаге

Decision Gate:
- `DG-2A`: какой runtime format считать primary.
  - Decision accepted: немедленная clean migration.
  - Primary published contract: `entries[]`.
  - Published `aliases{}` не считается обязательным compatibility-слоем.
  - Remaining work: loader migration, merge/validator rules, acceptance tests.

Minimal practical scope:
- обновить docs/schema без изменения runtime кода.

Проверки:
- schema validation;
- contract review against current loader.

Статус готовности:
- `ready_now`

#### Зависимости после закрытия `DG-2A`

Закрытие `DG-2A` означает только то, что contract choice по primary runtime format уже принят.
Это **не означает**, что все следующие decision gates или contract steps автоматически заблокированы до полной реализации clean migration.

Ниже фиксируется рабочее правило интерпретации зависимостей:

1. Что можно делать сразу после закрытия `DG-2A`
- разбирать и закрывать следующие contract gates, если они не требуют уже реализованного loader behavior;
- уточнять schemas/docs для intermediate artifacts;
- проектировать candidate/adjudication stages;
- писать decision-support/recommendation/closure docs по следующим gate.

2. Что нельзя считать автоматически готовым только потому, что `DG-2A` принят
- runtime implementation, которая зависит от чтения `entries[]`;
- merge behavior в коде loader/publisher;
- tests, которые опираются на уже работающий `entries[]`-based loader;
- publish flow, если он должен реально выпускать `entries[]`-only артефакты.

3. Практическое правило для следующих шагов
- `analysis/contract work` может идти дальше, если ему достаточно уже принятого решения;
- `implementation work` должно ждать завершения тех шагов, которые фактически реализуют clean migration, если без них следующий кодовый шаг не может быть корректно проверен.

4. Следствие для `DG-2B`
- `DG-2B` можно анализировать и закрывать до завершения runtime clean migration;
- но кодовые шаги, которые используют уже реализованный `entries[]` runtime contract, должны учитывать фактическую готовность шага 2.1 implementation-wise.

### Шаг 2.2. Спроектировать `synonyms.candidates` и `synonyms.adjudicated` технические форматы

1. Название шага:
   Спроектировать технические форматы `synonyms.candidates` и `synonyms.adjudicated`.
2. Задача шага:
   определить внутренние intermediate артефакты для полного автоматического пайплайна.
3. Проблема, которую шаг решает:
   без промежуточных форматов невозможно прозрачно отделить generation, gating и adjudication.
4. Как именно шаг решает проблему:
   добавляются machine-readable contracts для candidate/adjudication stages.
5. Результат:
   промежуточные слои формально описаны и готовы под реализацию скриптов.
6. Условия приемки:
   есть schema/doc с обязательными полями, status values и provenance fields.

Write scope:
- новые схемы в `conf/normalization/schemas/`
- docs в `docs/process/`
- возможно validator helpers в `app/normalization/synonyms_contract.py`

Decision Gate:
- `DG-2B`: оформлять intermediate data как JSONL или JSON batch files.
  - Вариант A: JSONL.
  - Вариант B: JSON arrays.
  - Recommended: `A`, потому что JSONL лучше для append/resume/streaming.

Minimal practical scope:
- только schema + docs.

Проверки:
- schema lint;
- примеры валидных/невалидных записей.

Статус готовности:
- `needs_gate`

## Фаза 3. Реализация MVP Synonyms Pipeline

### Шаг 3.1. Реализовать deterministic candidate builder

1. Название шага:
   Реализовать deterministic candidate builder.
2. Задача шага:
   построить первый автоматический генератор alias-кандидатов из aggregate/raw evidence.
3. Проблема, которую шаг решает:
   без candidate builder нет synonyms pipeline как такового.
4. Как именно шаг решает проблему:
   создается скрипт, который читает aggregate/raw, применяет string normalization rules и пишет `synonyms.candidates.jsonl`.
5. Результат:
   первый рабочий этап synonyms pipeline.
6. Условия приемки:
   скрипт стабильно генерирует candidate-файл из текущего aggregate;
   файл проходит schema validation;
   тесты на детерминированность и базовые правила проходят.

Write scope:
- новый `tools/build_synonyms_from_taxonomy.py`
- новый/обновленный helper в `tools/taxonomy_transform.py` или отдельный модуль
- `conf/normalization/schemas/synonyms_*.json`
- `tests/test_build_synonyms_from_taxonomy.py`

Decision Gate:
- `DG-3A`: источник evidence для candidate builder.
  - Вариант A: только aggregate.
  - Вариант B: aggregate + raw terms.
  - Recommended: `B`, потому что raw terms нужны для provenance и surface evidence.

Minimal practical scope:
- поддержать только deterministic generation;
- без embeddings и без LLM adjudication;
- ограничиться `skills` категорией, если нужно быстрое первое внедрение.

Проверки:
- unit tests;
- dry-run на текущем `taxonomy_domain_aggregate.mistral-nemo-300.json`.

Статус готовности:
- `depends_on_previous`

### Шаг 3.2. Реализовать auto-gate classifier

1. Название шага:
   Реализовать auto-gate classifier.
2. Задача шага:
   классифицировать candidates на `accepted`, `rejected`, `needs_adjudication`.
3. Проблема, которую шаг решает:
   без gate весь поток уйдет либо в шум, либо в ручной review, который по требованиям запрещен.
4. Как именно шаг решает проблему:
   реализуются frequency/conflict/noise/domain-consistency rules и формируется gate decision.
5. Результат:
   автоматическая приемка без участия человека.
6. Условия приемки:
   gate deterministic;
   decisions воспроизводимы;
   есть отчет по статусам и причинам.

Write scope:
- `tools/build_synonyms_from_taxonomy.py` или отдельный `tools/classify_synonyms_candidates.py`
- `app/normalization/synonyms_contract.py` при необходимости
- `tests/test_synonyms_auto_gate.py`

Decision Gate:
- `DG-3B`: держать gate внутри одного скрипта или вынести в отдельный модуль.
  - Вариант A: один скрипт.
  - Вариант B: отдельный модуль/скрипт.
  - Recommended: `B`, если предполагается iterative tuning и повторное использование.

Minimal practical scope:
- реализовать только базовые rules без embeddings;
- classification report в stdout/JSON.

Проверки:
- unit tests;
- regression test на фиксированном sample input.

Статус готовности:
- `depends_on_previous`

### Шаг 3.3. Реализовать LLM adjudication worker

1. Название шага:
   Реализовать LLM adjudication worker.
2. Задача шага:
   автоматически закрывать ambiguous cases без ручной приемки.
3. Проблема, которую шаг решает:
   даже после gate останутся конфликтные и важные unknown кандидаты.
4. Как именно шаг решает проблему:
   создается отдельный adjudication-скрипт с explicit output contract, работающий только на `needs_adjudication`.
5. Результат:
   machine-only решение сложных случаев.
6. Условия приемки:
   adjudication log валиден по схеме;
   результат можно детерминированно merged back в candidate state;
   retry/checkpoint работают.

Write scope:
- новый `tools/adjudicate_synonyms_candidates.py` или аналог
- новые schema files для adjudication input/output
- `tests/test_synonyms_adjudication.py`

Decision Gate:
- `DG-3C`: adjudication делать per-record или batch-by-block.
  - Вариант A: per-record.
  - Вариант B: batch-by-block `(domain, category, facet, skill_class)`.
  - Recommended: `B`, дешевле и лучше учитывает локальный контекст.

Minimal practical scope:
- adjudication только для ambiguous accepted/rejected boundary;
- без embeddings.

Проверки:
- contract tests;
- dry-run на ограниченном pending sample.

Статус готовности:
- `depends_on_previous`

## Фаза 4. Publish и Runtime

### Шаг 4.1. Реализовать publisher в `synonyms.auto`

1. Название шага:
   Реализовать publisher в `synonyms.auto`.
2. Задача шага:
   собрать итоговый auto dictionary из accepted/adjudicated candidates.
3. Проблема, которую шаг решает:
   intermediate layers бесполезны без publish в runtime-readable словарь.
4. Как именно шаг решает проблему:
   publisher собирает `entries[]`, derived `aliases{}`, version metadata, updated_at и provenance summary.
5. Результат:
   `conf/normalization/synonyms.auto.json`.
6. Условия приемки:
   словарь проходит validator;
   loader может его прочитать;
   одинаковый input дает идентичный output.

Write scope:
- новый `tools/publish_synonyms_auto.py` или publish stage внутри builder
- `conf/normalization/synonyms.auto.json` generation path
- `tests/test_synonyms_publisher.py`

Decision Gate:
- `DG-4A`: публиковать ли `aliases{}` как runtime-required или как derived convenience field.
  - Вариант A: runtime-required.
  - Вариант B: derived compatibility field.
  - Recommended: `B`.

Minimal practical scope:
- publish только auto;
- release/freeze оставить отдельным шагом.

Проверки:
- validator CLI;
- loader smoke test.

Статус готовности:
- `depends_on_previous`

### Шаг 4.2. Подключить словарь к runtime без изменения внешнего поведения

1. Название шага:
   Подключить словарь к runtime без изменения внешнего поведения.
2. Задача шага:
   включить чтение нового словаря в существующие runtime normalization пути.
3. Проблема, которую шаг решает:
   словарь должен использоваться в реальном pipeline, а не только существовать в артефактах.
4. Как именно шаг решает проблему:
   обновляются loader/runtime и соответствующие smoke/integration tests.
5. Результат:
   профили и вакансии нормализуются по новому словарному слою без LLM.
6. Условия приемки:
   e2e normalization smoke проходит;
   runtime не делает LLM calls;
   результаты воспроизводимы.

Write scope:
- `app/normalization/loader.py`
- `app/normalization/runtime.py`
- возможно `tools/run_profile_normalization_cycle.sh`
- `tests/test_normalization_loader.py`
- `tests/test_runtime_taxonomy_consistency.py`

Decision Gate:
- `DG-4B`: включать synonyms runtime сразу для профилей и вакансий или поэтапно.
  - Вариант A: сразу оба.
  - Вариант B: сначала вакансии, затем профили.
  - Recommended: `A`, если loader/runtime единые; `B`, если нужен контроль blast radius.

Minimal practical scope:
- включить только read path;
- без дополнительных UI/reporting изменений.

Проверки:
- unit tests;
- local smoke script.

Статус готовности:
- `depends_on_previous`

## Фаза 5. Масштабирование и hardening

### Шаг 5.1. Внедрить embeddings expansion как отдельный offline stage

1. Название шага:
   Внедрить embeddings expansion как отдельный offline stage.
2. Задача шага:
   повысить long-tail coverage после того, как deterministic + adjudication baseline уже стабилен.
3. Проблема, которую шаг решает:
   на редких формулировках rule-only слой будет давать высокий unknown-rate.
4. Как именно шаг решает проблему:
   строится отдельный embeddings-based suggester для unresolved alias с последующим gate/adjudication.
5. Результат:
   controlled semantic expansion без LLM в runtime.
6. Условия приемки:
   growth in coverage измерим;
   collision-rate остается в пороге;
   stage можно отключить без поломки base pipeline.

Write scope:
- новый offline script/module
- tests for scorer/threshold logic
- docs update

Decision Gate:
- `DG-5A`: какой embedding backend использовать первым.
  - Вариант A: CPU local multilingual sentence-transformers.
  - Вариант B: GPU-required pipeline.
  - Recommended: `A` для первой итерации, если производительность приемлема.

Minimal practical scope:
- только suggestion stage без auto-publish в release.

Проверки:
- benchmark на fixed unresolved sample.

Статус готовности:
- `depends_on_previous`

### Шаг 5.2. Провести rollout 20k -> 100k

1. Название шага:
   Провести rollout 20k -> 100k.
2. Задача шага:
   подтвердить, что pipeline масштабируется по времени, стоимости и качеству.
3. Проблема, которую шаг решает:
   pilot на 300/20k не гарантирует production viability.
4. Как именно шаг решает проблему:
   вводятся batch execution presets, worker-pool policy, retry/rate-limit budget и release gates между масштабами.
5. Результат:
   подтвержденный путь к production-scale.
6. Условия приемки:
   20k и 100k проходят по SLA;
   есть quality reports;
   переход к 700k обоснован фактами, а не предположениями.

Write scope:
- execution scripts
- docs/runbooks
- maybe retry/preset config

Decision Gate:
- `DG-5B`: масштабировать ли extraction sequentially hardened или сразу через parallel worker architecture.
  - Вариант A: сначала hardened sequential/batched.
  - Вариант B: сразу parallel worker pool.
  - Recommended: `B`, если цель 100k+ и есть rate-limit-aware design; иначе `A` как промежуточный stabilization step.

Minimal practical scope:
- сначала подтвердить 20k fully automatic.

Проверки:
- full run reports;
- cost/time/error dashboards or summary reports.

Статус готовности:
- `depends_on_previous`

## Suggested Iteration Order

1. `Шаг 0.1`
2. `Шаг 1.1`
3. `Шаг 1.2`
4. `Шаг 2.1`
5. `Шаг 2.2`
6. `Шаг 3.1`
7. `Шаг 3.2`
8. `Шаг 3.3`
9. `Шаг 4.1`
10. `Шаг 4.2`
11. `Шаг 5.1`
12. `Шаг 5.2`

## Что уже готово к реализации сейчас

- инвентаризация reusable файлов и тестов;
- фиксация extraction policy;
- уточнение runtime contract словаря;
- проектирование intermediate schemas;
- реализация deterministic candidate builder.

## Что требует доопределения контракта до начала кода

- точная семантика `canonical` в extractive-first режиме;
- primary runtime format словаря синонимов;
- формат intermediate adjudication artifacts;
- batching strategy для LLM adjudication;
- первая стратегия embeddings backend.

## Что Codex не должен делать молча

- менять runtime contract словаря без закрытого `Decision Gate`;
- выбирать формат publish-артефактов без явного решения;
- включать embeddings в runtime;
- переводить source terms в абстрактные canonical без зафиксированного правила;
- объединять домены в global synonym map без явного решения.
