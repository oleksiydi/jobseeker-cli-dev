# DG-1A: Разбор Decision Gate по Semantics `canonical`

Audience: `Human + AI`  
Status: `decision-support`  
Last updated: `2026-03-24`

## Зачем нужен этот документ

Этот документ фиксирует полную предметную разборку `DG-1A` из
`docs/process/taxonomy-synonyms-codex-implementation-plan-ru.md`.

Decision gate:
- нужно определить, что означает поле `canonical` в extractive-first схеме;
- решение влияет на словарь, synonyms pipeline, runtime mapping и downstream аналитику;
- это нельзя выбирать молча в коде.

## 1. В чём состоит задача или проблема

В нашей схеме уже принято, что:
- `term` должен быть extractive-first и максимально близок к исходному тексту;
- runtime должен работать без LLM;
- эквивалентность должна учитываться через synonyms layer.

Неопределённым остаётся смысл `canonical`.

Вопрос:
- `canonical` тоже должен быть близок к поверхности текста;
- или это уже внутренний устойчивый ключ, который существует ради нормализации и deduplication.

Проблема возникает потому, что `canonical` одновременно может использоваться как:
- машинный идентификатор;
- человекочитаемая форма;
- цель для synonyms mapping;
- единица агрегации и аналитики.

Если эти роли не разделить осознанно, словарь получится либо нестабильным, либо неудобным для runtime, либо слишком абстрактным.

## 2. Почему здесь вообще нужен decision gate

Это не локальная деталь реализации.

Решение по `canonical` влияет на:
- extraction contract;
- формат `taxonomy_domain_aggregate`;
- дизайн `synonyms.auto/release`;
- поведение runtime normalization;
- логику dedup;
- качество cross-lingual mapping;
- миграционную сложность в будущем.

Если сейчас выбрать вариант молча, дальше это закрепится в:
- тестах;
- словарных файлах;
- intermediate artifacts;
- pipeline логике.

Потом менять будет дорого.

## 3. Какие неоднозначности нужно разрешить

Нужно заранее ответить на следующие вопросы:

- `canonical` — это internal key или display form?
- допускается ли `canonical`, которого буквально нет в корпусе?
- можно ли использовать английский `canonical`, если source phrase немецкая?
- можно ли склеивать несколько surface forms в один более абстрактный concept key?
- должен ли `canonical` быть человеком читаемым без дополнительного display поля?
- как сильно мы готовы жертвовать traceability ради dedup/stability?

## 4. Реалистичные примеры

### Базовый пример

Source phrase:
- `prozess- und lösungsorientierte Arbeitsweise`

Возможные представления:
- `term`: `prozess- und lösungsorientierte arbeitsweise`
- `canonical` как surface-near: `prozess- und lösungsorientierte arbeitsweise`
- `canonical` как internal key: `prozess_und_loesungsorientierung`
- `canonical` как abstract EN key: `process_orientation`

Вывод:
- surface-near хорошо объясним человеку;
- internal key хорош для стабильности;
- abstract EN key без aliases и evidence опасен для runtime.

### Сложный пример 1: Аббревиатуры и брендовые формы

Source phrases:
- `Kubernetes`
- `k8s`
- `K8s-Plattform`

Если `canonical` surface-near:
- возможны три разные canonical-формы.

Если `canonical` internal key:
- всё можно свести к `kubernetes`.

Здесь internal key работает заметно лучше.

### Сложный пример 2: Похожие, но не полностью идентичные термины

Source phrases:
- `CI/CD`
- `Continuous Integration`
- `Continuous Delivery`
- `Continuous Integration / Continuous Deployment`

Если `canonical` слишком абстрактный:
- можно ошибочно склеить разные концепты.

Если `canonical` слишком surface-near:
- можно не получить нужной нормализации вообще.

Это пример, где слишком агрессивный canonical вреден, а слишком консервативный тоже создает проблему.

### Сложный пример 3: Soft skills

Source phrases:
- `teamfähig`
- `Teamfähigkeit`
- `teamorientierte Arbeitsweise`
- `kommunikativ und teamfähig`

Если `canonical` surface-near:
- словарь разрастается;
- traceability отличная;
- dedup слабый.

Если `canonical` internal key:
- можно вести к `teamfaehigkeit`;
- aliases решают вариативность.

Если `canonical` слишком abstract:
- можно ошибочно склеить `teamfähigkeit`, `collaboration`, `kommunikativ`.

### Сложный пример 4: Одно и то же решение полезно в одном случае и вредно в другом

Пример A:
- `SAP BI` -> `sap_bi`

Здесь internal key почти идеален.

Пример B:
- `prozess- und lösungsorientierte Arbeitsweise`

Здесь internal key тоже допустим, но только если:
- `term` и aliases остаются surface-near;
- `canonical` не отрывается от evidence;
- не используется вольный перевод.

Пример C:
- `process orientation`

Если такой `canonical` появляется без реального DE/EN alias evidence, решение уже становится проблемным.

## 5. Варианты решения

### Вариант A

Определение:
- `term` обязан быть extractive-first;
- `canonical` — стабильный внутренний ключ;
- `canonical` не должен быть произвольной интерпретацией без source evidence.

Как это работает:
- source: `prozess- und lösungsorientierte Arbeitsweise`
- term: `prozess- und lösungsorientierte arbeitsweise`
- canonical: `prozess_und_loesungsorientierung`

Плюсы:
- хороший dedup;
- удобно для runtime и analytics;
- удобно объединять DE/EN aliases;
- меньше дублей в словаре.

Минусы:
- `canonical` уже не равен тексту;
- нужно хорошо объяснять разницу между `term` и `canonical`.

Риски:
- если разрешить слишком абстрактные canonical, модель начнет “придумывать” concepts.

### Вариант B

Определение:
- `term` и `canonical` оба должны быть surface-near.

Как это работает:
- source: `prozess- und lösungsorientierte Arbeitsweise`
- term: `prozess- und lösungsorientierte arbeitsweise`
- canonical: `prozess- und lösungsorientierte arbeitsweise`

Плюсы:
- максимально прозрачно;
- высокая traceability;
- минимальный риск semantic hallucination в canonical.

Минусы:
- слабый dedup;
- растущий объем почти-дубликатов;
- cross-lingual equivalence переносится почти полностью в synonyms.

Риски:
- словарь станет громоздким и менее удобным как база для аналитики.

### Вариант C

Определение:
- вводятся два слоя:
  - `canonical_key` — стабильный internal key;
  - `canonical_display` — человекочитаемая surface-near форма.

Как это работает:
- source: `prozess- und lösungsorientierte Arbeitsweise`
- term: `prozess- und lösungsorientierte arbeitsweise`
- canonical_key: `prozess_und_loesungsorientierung`
- canonical_display: `prozess- und lösungsorientierte arbeitsweise`

Плюсы:
- лучшая совместимость между human-readable и system-stable semantics;
- проще дебажить и объяснять;
- мягче мигрировать к richer UX и аналитике.

Минусы:
- сложнее контракт;
- больше полей во всех intermediate/runtime artifacts;
- выше цена внедрения сейчас.

Риски:
- преждевременное усложнение pipeline до стабилизации базового контракта.

## 6. Сравнение вариантов на конкретных примерах

### Пример: `k8s`

Вариант A:
- term: `k8s`
- canonical: `kubernetes`
- хорошо работает.

Вариант B:
- term: `k8s`
- canonical: `k8s`
- нормализация слабее, нужен более богатый synonyms layer.

Вариант C:
- term: `k8s`
- canonical_key: `kubernetes`
- canonical_display: `Kubernetes`
- лучший UX, но сложнее контракт.

### Пример: `CI/CD`

Вариант A:
- риск зависит от того, насколько аккуратно задается internal key.
- может быть хорошо, если не склеивать `CI` и `CD` автоматически.

Вариант B:
- safest по traceability;
- но хуже по dedup.

Вариант C:
- самый гибкий, если проект готов платить за сложность.

### Пример: `prozess- und lösungsorientierte Arbeitsweise`

Вариант A:
- подходит, если canonical — internal key из source semantics, а не вольный EN перевод.

Вариант B:
- идеально для объяснимости;
- хуже для дедупликации.

Вариант C:
- наиболее чистая модель, но пока дороже внедрять.

## 7. Предпочтительный вариант

Предпочтителен `Вариант A`.

Причины:
- он лучше всего соответствует уже принятой общей схеме;
- он сохраняет extractive-first на уровне `term`;
- он дает стабильный системный ключ для словаря;
- он хорошо сочетается с domain-aware synonyms;
- он не требует немедленного расширения контракта, как вариант C.

Но у него есть жесткое ограничение:
- `canonical` не должен быть translated-only или free-form abstract label без связи с реальными alias/source evidence.

Иначе вариант A превратится в плохо контролируемую интерпретацию.

## 8. Вопросы, на которые нужно ответить, чтобы закрыть decision gate

1. Должен ли `canonical` считаться внутренним техническим ключом, а не display-формой?
2. Разрешаем ли мы DE и EN aliases для одного `canonical`?
3. Разрешаем ли translated canonical, если такой формы нет в корпусе?
4. Насколько агрессивно мы готовы склеивать surface forms в один canonical?
5. Нужен ли нам уже сейчас отдельный `canonical_display`, или это future enhancement?

## 9. Критические вопросы

Критические:
- Разрешаем ли translated canonical без surface evidence?
- Является ли `canonical` техническим internal key или human-readable формой?

Пока эти два вопроса не закрыты, реализацию extraction/synonyms pipeline нельзя считать полностью контрактно определенной.

## 10. Рекомендуемое решение по умолчанию

Если не задано дополнительных ограничений, рекомендуется:

- `term` = extractive-first, максимально близок к source text;
- `canonical` = стабильный internal key;
- `canonical` нельзя делать произвольным переводом или абстракцией без source evidence;
- DE/EN equivalence хранится в synonyms layer, а не в свободной семантике `canonical`;
- `canonical_display` пока не вводится, но вариант C сохраняется как возможное следующее расширение контракта.
