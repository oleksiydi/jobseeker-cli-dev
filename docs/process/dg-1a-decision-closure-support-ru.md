# DG-1A: Decision-Closure Support

Audience: `Human + AI`  
Status: `closure-proposed`  
Last updated: `2026-03-24`

## Назначение

Этот документ закрывает оставшиеся пункты по `DG-1A` после принятия варианта A.

Связанные документы:
- `docs/process/dg-1a-canonical-semantics-analysis-ru.md`
- `docs/process/dg-1a-variant-a-recommendation-ru.md`
- `docs/process/dg-1a-variant-a-explained-examples-ru.md`
- `docs/process/taxonomy-synonyms-codex-implementation-plan-ru.md`

## 1. Resolvable now

### 1.1. Точное правило генерации `canonical`

Что можно решить сразу:
- правило генерации internal stable key.

Предлагаемое решение:
- `canonical` всегда строится из одного подтвержденного source-near alias, а не генерируется свободно моделью;
- базовый источник для canonical: alias-кандидат, прошедший auto-gate/adjudication;
- допустимые transforms:
  - trim;
  - lowercase;
  - collapse whitespace;
  - унификация пробельных/дефисных разделителей;
  - замена пробелов и дефисов на `_` в итоговом key;
  - немецкая ASCII-транслитерация для key-стабильности: `ä->ae`, `ö->oe`, `ü->ue`, `ß->ss`;
  - удаление внешней пунктуации;
- недопустимые transforms:
  - свободный перевод;
  - смысловое обобщение;
  - расширение термина словами, которых нет в source aliases;
  - склейка разных понятий в один key без synonyms evidence.

Суть принятого решения:
- в коде должен существовать один deterministic builder для `canonical`;
- builder принимает на вход approved alias и возвращает один stable key;
- два одинаково нормализуемых alias всегда должны давать один и тот же `canonical`;
- `canonical` не может приходить напрямую из свободного текста ответа LLM как отдельное “умное” поле без повторной deterministic нормализации.

Что это означает для реализации:
- `canonical` должен вычисляться программно;
- LLM может предложить alias/связь/группировку, но финальный `canonical` формирует код по фиксированным правилам;
- правила derivation должны быть покрыты unit-тестами на идентичность и стабильность.

Что будет считаться нарушением:
- `canonical` берется из raw LLM output без deterministic rebuild;
- `canonical` содержит слова, которых нет ни в одном approved alias и не получаются допустимыми transforms;
- один и тот же alias в одинаковых условиях дает разные canonical между прогонами.

Почему выбрано:
- это самый предсказуемый способ сохранить stable key и не потерять связь с source text;
- правило детерминировано и хорошо тестируется;
- оно соответствует уже принятому варианту A.

### 1.2. Формальная проверка запрета translated-only canonical

Что можно решить сразу:
- как именно технически проверять запрет translated-only canonical.

Предлагаемое решение:
- использовать двухслойную защиту:
  - prompt-level запрет;
  - post-parse validator rule;
- validator считает canonical допустимым, если выполняется хотя бы одно из условий:
  - canonical derived from accepted alias deterministic transforms;
  - canonical совпадает с approved internal-key derivation из alias;
  - canonical уже существует как approved key в словаре и новый term мапится к нему через synonyms evidence.

Суть принятого решения:
- одного промпта недостаточно;
- решение о допустимости `canonical` должно подтверждаться кодом после ответа модели;
- перевод или semantic paraphrase без alias evidence считается ошибкой контракта.

Что это означает для реализации:
- после extraction/adjudication должен запускаться validator;
- validator должен уметь возвращать machine-readable причину отказа:
  - `translation_without_evidence`
  - `canonical_not_derived_from_alias`
  - `unsupported_semantic_abstraction`
- такие записи не должны попадать в publish stage.

Что будет считаться нарушением:
- translated canonical проходит в `taxonomy.auto` или `synonyms.auto` без validator approval;
- единственным основанием допустимости canonical является “так решила модель”.

Почему выбрано:
- prompt alone недостаточно надежен;
- validator дает повторяемую и наблюдаемую гарантию.

### 1.3. Минимальный обязательный provenance contract

Что можно решить сразу:
- какие provenance поля должны стать обязательными.

Предлагаемое решение:
- обязательный provenance для canonical:
  - `created_from_run_id`
  - `decision_source` (`rule` | `llm_adjudication` | `publisher_merge`)
  - `source_aliases`
  - `source_refs_count`
  - `sample_phrases`
  - `domains`
  - `updated_at`
- минимальное требование по слоям:
  - в intermediate artifacts provenance обязателен полностью;
  - в runtime dictionary допускается компактная форма, но не полное отсутствие provenance в publish chain.

Суть принятого решения:
- у каждого canonical должен быть минимально достаточный след происхождения;
- provenance не является опциональным “nice to have”, он входит в контракт построения словаря.

Что это означает для реализации:
- intermediate JSONL/JSON артефакты должны хранить provenance-поля явно;
- publish pipeline может упрощать presentation-слой, но не должен терять возможность восстановить происхождение canonical;
- для debug/rollback должна существовать связь:
  - canonical -> source_aliases -> source_refs/sample_phrases -> run_id/decision_source.

Что будет считаться нарушением:
- canonical существует в опубликованном словаре, но его невозможно связать хотя бы с одним approved alias и одним run source;
- provenance удаляется или не записывается на intermediate этапе.

Почему выбрано:
- этого достаточно для audit/debug/rollback;
- контракт остаётся компактным, но практичным.

### 1.4. Место решения в execution-plan

Что можно решить сразу:
- как пометить `DG-1A` после принятия semantic choice и formalization defaults.

Предлагаемое решение:
- пометить `DG-1A` как `closed-by-defaults`;
- в execution-plan оставить только implementation tasks:
  - prompt update;
  - validator implementation;
  - provenance fields implementation;
  - acceptance tests.

Суть принятого решения:
- `DG-1A` больше не считается открытым архитектурным выбором;
- для Codex это теперь не место для нового выбора, а только место для реализации уже принятого контракта.

Что это означает для реализации:
- при последующих итерациях Codex не должен снова предлагать варианты A/B/C по `DG-1A`;
- любые изменения в этой точке должны трактоваться как contract change request, а не как обычная реализация шага.

Что будет считаться нарушением:
- новая реализация тихо меняет semantics `canonical`;
- Codex предлагает другой вариант в рамках обычной итерации без явного reopen decision gate.

Почему выбрано:
- semantic choice уже принят;
- оставшиеся пункты больше не являются альтернативными архитектурными вариантами.

### 1.5. Набор acceptance examples

Что можно решить сразу:
- какой минимальный набор тестовых кейсов нужен для закрытия gate.

Предлагаемое решение:
- зафиксировать 8 acceptance examples:
  - `k8s -> kubernetes`
  - `Kubernetes -> kubernetes`
  - `prozess- und lösungsorientierte Arbeitsweise -> prozess_und_loesungsorientierung`
  - `Teamfähigkeit -> teamfaehigkeit`
  - `teamfähig -> teamfaehigkeit`
  - `SAP BI -> sap_bi`
  - `CI/CD -> ci_cd`
  - negative case: `prozess- und lösungsorientierte Arbeitsweise -> process_orientation` должно быть rejected

Суть принятого решения:
- закрытие `DG-1A` подтверждается не описанием, а фиксированным набором примеров;
- эти примеры становятся обязательной acceptance-suite для implementation steps, которые затрагивают `canonical`.

Что это означает для реализации:
- примеры должны быть оформлены в тестах;
- каждый релевантный шаг реализации должен запускать эти тесты;
- positive/negative cases должны проверять не только финальный canonical, но и корректность reject behavior.

Что будет считаться нарушением:
- изменение canonical semantics без обновления acceptance-suite;
- positive кейсы начинают давать новые canonical без отдельного contract review;
- negative translation-only кейс перестает отклоняться.

Почему выбрано:
- этого достаточно для первой formal closure проверки;
- покрываются abbreviations, DE soft skills, technical stack, composite forms и negative translation-only case.

## 2. Needs explicit user choice

На текущем уровне проработки для закрытия именно `DG-1A` дополнительных обязательных пользовательских решений не требуется.

Причина:
- semantic choice уже принят;
- оставшиеся пункты можно безопасно закрыть default-решениями без изменения общей схемы;
- спорные вопросы, которые действительно могут менять контракт, уже вынесены в другие decision points (`DG-2A`, `DG-2B` и далее).

## 3. Proposed closure set

Ниже полный набор фиксаций, которых достаточно, чтобы считать `DG-1A` формально закрытым:

1. `canonical` трактуется как internal stable key.
2. `term` трактуется как extractive-first source-near alias.
3. `canonical` генерируется только deterministic derivation из approved alias, без свободного перевода и без смысловой интерпретации.
4. translated-only canonical запрещены и проверяются не только prompt-ом, но и post-parse validator rule.
5. DE/EN equivalence не кодируется в свободной семантике canonical, а хранится в synonyms layer.
6. Минимальный обязательный provenance contract:
   - `created_from_run_id`
   - `decision_source`
   - `source_aliases`
   - `source_refs_count`
   - `sample_phrases`
   - `domains`
   - `updated_at`
7. В execution-plan `DG-1A` меняет статус с semantic gate на implementation-closed-by-defaults.
8. Acceptance suite для `DG-1A` фиксируется минимум из 8 кейсов, включая negative translated-only example.

В реализации это означает:
- semantic часть `DG-1A` больше не обсуждается;
- код должен подстраиваться под эти фиксации, а не выбирать между альтернативами;
- любой отступ от этих правил считается либо багом, либо отдельным новым contract decision.

Этого набора достаточно, чтобы считать gate закрытым на уровне semantics и перейти к обычной реализации.

## 4. Residual risks

После принятия этих решений остаются не открытые вопросы, а рабочие риски реализации:

1. Морфологические случаи
- deterministic derivation не решает все варианты немецкой морфологии;
- часть качества будет зависеть от качества alias/synonyms pipeline.

2. Cross-domain ambiguity
- internal stable key не решает сам по себе междоменные коллизии;
- это остается задачей synonyms gating/adjudication.

3. Canonical drift при плохо написанных правилах
- если реализовать derivation непоследовательно, stable key перестанет быть stable.

4. Будущий переход к варианту C
- он остается возможным;
- но его простота зависит от того, насколько строго сейчас будут разделены key semantics и display semantics.

## Итог

Рекомендуемая фиксация:
- `DG-1A` можно считать формально закрытым без дополнительных пользовательских решений;
- дальше остаются только implementation tasks, которые должны реализовать уже принятое решение, а не переобсуждать его.
