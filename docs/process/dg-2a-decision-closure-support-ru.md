# DG-2A: Decision-Closure Support

Audience: `Human + AI`  
Status: `closure-proposed`  
Last updated: `2026-03-24`

## Назначение

Этот документ закрывает оставшиеся пункты по `DG-2A` после принятия решения:
- primary published runtime contract = `entries[]`;
- выбран путь немедленной clean migration;
- published `aliases{}` больше не считается обязательным compatibility-слоем.

Связанные документы:
- `docs/process/dg-2a-runtime-format-analysis-ru.md`
- `docs/process/dg-2a-clean-migration-recommendation-ru.md`
- `docs/process/taxonomy-synonyms-codex-implementation-plan-ru.md`

## 1. Resolvable now

### 1.1. Что является primary published contract

Что можно решить сразу:
- какой слой published словаря считается нормативным и authoritative.

Предлагаемое решение:
- published словарь синонимов обязан содержать `entries[]`;
- `entries[]` является единственным source-of-truth;
- published `aliases{}` не является частью нового обязательного контракта.

Суть принятого решения:
- словарь публикуется как rich список записей, а не как flat map;
- все runtime и publish правила выводятся из `entries[]`, а не наоборот.

Что это означает для реализации:
- schema/doc/tests должны исходить из того, что `entries[]` обязательны;
- publisher больше не должен считать `aliases{}` необходимым артефактом;
- любые derived runtime indices строятся из `entries[]`.

Что будет считаться нарушением:
- published файл без `entries[]`;
- published файл, где authoritative считается `aliases{}` или иной derived слой;
- ситуация, когда `entries[]` и `aliases{}` сосуществуют как два равноправных источника истины.

Почему выбрано:
- это убирает dual-source contract;
- это лучше соответствует будущему domain-aware runtime;
- это снижает риск рассинхронизации между rich и flat представлениями.

### 1.2. Как должен вести себя loader после clean migration

Что можно решить сразу:
- что именно обязан делать runtime loader с published `entries[]`.

Предлагаемое решение:
- loader читает `entries[]`;
- нормализует `alias` в runtime key по тем же string-normalization rules, что и остальная normalizer infrastructure;
- строит в памяти derived lookup index для текущего runtime;
- runtime derived index не публикуется обратно в словарь как обязательный persisted слой.

Суть принятого решения:
- flat lookup остается допустимым runtime-механизмом, но не persisted publish contract;
- логика перехода идет через loader, а не через дублирование published JSON.

Что это означает для реализации:
- `app/normalization/loader.py` должен уметь строить alias index из `entries[]`;
- return contract loader-а должен быть документирован:
  - минимум derived `aliases`;
  - при необходимости рядом может сохраняться `entries` для будущих consumers;
- tests должны проверять, что одинаковые entry payloads дают одинаковый derived runtime index.

Что будет считаться нарушением:
- loader продолжает читать только published `aliases{}`;
- derived index зависит от произвольного порядка записей без документированного merge rule;
- runtime требует от publish файла иметь заранее материализованный flat слой.

Почему выбрано:
- это позволяет сразу перейти на clean contract без одновременного полного redesign runtime consumers;
- flat runtime lookup сохраняется как внутренняя оптимизация, а не как источник истины.

### 1.3. Какие поля в `entries[]` обязательны для runtime contract

Что можно решить сразу:
- какой минимальный набор полей должен считаться обязательным в published entry.

Предлагаемое решение:
- обязательные поля для каждой записи:
  - `alias`
  - `canonical`
  - `domain`
  - `category`
  - `facet`
  - `skill_class`
- strongly recommended publish fields:
  - `confidence`
  - `source`
  - `updated_at`

Суть принятого решения:
- published runtime contract уже сейчас должен нести не только alias -> canonical, но и минимальный semantic context;
- optional metadata не отменяет того, что запись без смыслового контекста считается неполной.

Что это означает для реализации:
- schema validation и runtime loader tests должны считать первые шесть полей обязательными;
- publisher должен заполнять recommended metadata whenever available;
- runtime текущего этапа может пока не использовать все поля, но не должен терять их при чтении/валидации.

Что будет считаться нарушением:
- published entry без `domain` или `facet`, если проект позиционирует словарь как domain-aware;
- silent drop этих полей в loader при том, что они есть в published contract.

Почему выбрано:
- это минимально достаточный richer contract;
- он уже соответствует существующей schema и не требует нового переосмысления данных.

### 1.4. Merge behavior для `release` и `auto`

Что можно решить сразу:
- как сливать `synonyms.release.json` и `synonyms.auto.json` в текущем runtime.

Предлагаемое решение:
- merge идет на уровне `entries[]`, а не на уровне published `aliases{}`;
- текущий runtime derived lookup строится после merge;
- базовое правило overlay:
  - `auto` перекрывает `release` только при совпадении одного и того же нормализованного alias key и одинакового semantic target;
  - если нормализованный alias key совпадает, но semantic target различается, это не silent override, а conflict/error до publish или во время validation.

Суть принятого решения:
- не любой одинаковый alias можно просто перезаписать;
- silent override допустим только там, где запись по сути та же, но свежее или богаче по metadata.

Что это означает для реализации:
- нужен deterministic entry-identity/compatibility check;
- validator/publisher должен уметь обнаруживать конфликт:
  - одинаковый normalized alias;
  - разные canonical и/или incompatible semantic fields;
- loader не должен молча выбирать "последний победил" для конфликтных semantic entries.

Что будет считаться нарушением:
- auto всегда молча перезаписывает release при любом совпадении alias;
- runtime строит итоговый lookup, скрывая semantic collision без ошибки или явного reject.

Почему выбрано:
- flat override rule слишком опасен для richer contract;
- conflict detection нужна уже на этом этапе, иначе rich metadata теряет практическую ценность.

### 1.5. Что делать с legacy `aliases{}`

Что можно решить сразу:
- как проект должен относиться к legacy `aliases{}` после принятия clean migration.

Предлагаемое решение:
- новый publish contract не требует `aliases{}`;
- новые словари, публикуемые проектом, не должны содержать `aliases{}` как обязательное поле;
- tolerant-reader режим допустим только как короткий migration helper в коде, но не как зафиксированная часть published contract.

Суть принятого решения:
- distinction проводится четко:
  - `aliases{}` в памяти runtime = допустимо;
  - `aliases{}` в published contract = больше не норма;
- backward compatibility становится локальной инженерной тактикой, а не частью формального API словаря.

Что это означает для реализации:
- docs/schema должны сдвигаться в сторону `entries[]`-only понимания;
- если loader временно терпит `aliases{}`, это должно быть явно помечено как transition behavior;
- generator/publisher не должен продолжать писать `aliases{}` "на всякий случай".

Что будет считаться нарушением:
- новые published артефакты продолжают регулярно включать `aliases{}` без явной причины;
- transition behavior маскируется как постоянный контракт.

Почему выбрано:
- иначе проект останется в half-migrated состоянии;
- цель clean migration именно в том, чтобы прекратить тащить compatibility field в published API.

### 1.6. Набор acceptance examples

Что можно решить сразу:
- какой минимальный набор кейсов нужен, чтобы считать `DG-2A` закрытым не только словами.

Предлагаемое решение:
- зафиксировать минимум 6 acceptance cases:
  - single entry `k8s -> kubernetes` читается loader-ом из `entries[]` и дает корректный derived alias lookup;
  - `sap bi -> sap_bi` читается из `entries[]` без published `aliases{}`;
  - file с `entries[]`, но без `aliases{}` считается valid;
  - duplicate normalized alias с одинаковым semantic target либо дедуплицируется по документированному правилу, либо валидируется как acceptable duplicate;
  - duplicate normalized alias с разным canonical считается conflict/error;
  - published файл, который пытается объявить `aliases{}` как равноправный primary слой, считается contract violation.

Суть принятого решения:
- закрытие `DG-2A` подтверждается проверяемыми loader/publisher/validator кейсами;
- тесты должны доказывать работу clean migration на практике.

Что это означает для реализации:
- acceptance suite оформляется в unit/integration tests;
- релевантные шаги реализации должны запускать эти кейсы;
- docs/examples должны быть синхронизированы с тестами.

Что будет считаться нарушением:
- contract меняется, а acceptance-suite не обновляется;
- runtime требует published `aliases{}` несмотря на принятый `entries[]`-only contract.

Почему выбрано:
- этого достаточно для первой формальной верификации clean migration;
- набор покрывает happy path, отсутствие legacy field и conflict behavior.

## 2. Needs explicit user choice

Дополнительных пользовательских решений для закрытия именно `DG-2A` больше не требуется.

Причина:
- ключевой контрактный выбор уже сделан: принят путь немедленной clean migration;
- оставшиеся пункты можно безопасно закрыть default-решениями без возврата к альтернативным архитектурным вариантам.

## 3. Proposed closure set

Ниже полный набор фиксаций, которых достаточно, чтобы считать `DG-2A` формально закрытым:

1. Primary published contract словаря синонимов = `entries[]`.
2. `entries[]` является единственным source-of-truth.
3. Published `aliases{}` не входит в новый обязательный contract.
4. Loader обязан читать `entries[]` и строить derived flat runtime index в памяти.
5. Обязательные entry fields:
   - `alias`
   - `canonical`
   - `domain`
   - `category`
   - `facet`
   - `skill_class`
6. Merge `release` + `auto` происходит на уровне `entries[]`, а не published `aliases{}`.
7. Semantic conflict по одному normalized alias не может решаться silent override; это validator/publisher error.
8. Legacy `aliases{}` допускается только как временное transition behavior в коде, но не как часть нормального published API.
9. Acceptance suite фиксирует минимум 6 loader/publisher/validator кейсов под clean migration.

В реализации это означает:
- semantic часть `DG-2A` больше не обсуждается;
- дальнейшие шаги должны реализовать уже принятый contract;
- любые отклонения от этого решения считаются либо багом, либо новым contract change request.

Этого набора достаточно, чтобы считать gate закрытым на уровне published/runtime contract semantics.

## 4. Residual risks

После принятия этих решений остаются не открытые вопросы, а рабочие риски реализации:

1. Loader complexity
- runtime loader станет заметно богаче, чем при flat JSON;
- это требует аккуратных тестов на normalization и conflict detection.

2. Partial migration window
- docs/schema и runtime могут временно разъехаться, если миграцию делать неатомарно.

3. Future domain-aware behavior
- даже rich `entries[]` не решают автоматически policy вопрос, как runtime должен вести себя при multi-domain alias ambiguity.

4. Legacy file tolerance
- если оставить tolerant reader слишком надолго, проект может незаметно вернуться к неявной dual-format жизни.

## Итог

Рекомендуемая фиксация:
- `DG-2A` можно считать формально закрытым;
- дальше остаются только implementation tasks:
  - loader migration;
  - schema/docs alignment;
  - merge validator rules;
  - acceptance tests.
