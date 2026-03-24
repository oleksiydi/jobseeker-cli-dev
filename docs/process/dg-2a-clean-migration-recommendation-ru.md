# DG-2A: Clean Migration на `entries[]` и Его Последствия

Audience: `Human + AI`  
Status: `accepted-with-pending-formalization`  
Last updated: `2026-03-24`

## Статус решения

Для `DG-2A` принят вариант **немедленной clean migration**.

Связанные документы:
- `docs/process/dg-2a-runtime-format-analysis-ru.md`
- `docs/process/dg-2a-decision-closure-support-ru.md`
- `docs/process/taxonomy-synonyms-codex-implementation-plan-ru.md`

Принятая формулировка:
1. Primary published contract словаря синонимов = `entries[]`.
2. `entries[]` является единственным source-of-truth.
3. Поле `aliases{}` не публикуется как обязательный compatibility-слой.
4. Текущий flat runtime lookup строится loader-ом в памяти из `entries[]`.
5. Rich metadata (`domain`, `category`, `facet`, `skill_class`, `confidence`, `source`, `updated_at`) сохраняется в published словаре, а не только в промежуточных артефактах.
6. Переход делается сразу, пока код и данные еще достаточно малы и цена миграции низкая.

## Что ещё не хватает для окончательного закрытия Decision Gate

Хотя вариант уже принят, для полного контрактного закрытия `DG-2A` ещё нужно явно зафиксировать:

1. Точное publish contract поведение
- какие поля обязательны в `entries[]`;
- какие поля считаются runtime-required сейчас, а какие future-facing.

2. Поведение loader/runtime при чтении `entries[]`
- как строится flat lookup map в памяти;
- как нормализуется alias key;
- какие поля runtime должен сохранить рядом с derived index.

3. Merge semantics для `synonyms.release.json` и `synonyms.auto.json`
- что происходит при одинаковом alias;
- когда конфликт разрешается overlay-логикой, а когда считается ошибкой.

4. Правило обработки legacy `aliases{}`
- игнорировать ли его;
- считать ли его ошибкой нового publish contract;
- нужен ли временный tolerant reader на период миграции.

5. Набор acceptance examples
- 5-10 контрольных кейсов, которые доказывают, что loader/publisher/runtime действительно живут по `entries[]`-only контракту.

## Коротко

Рекомендованный и принятый вариант для `DG-2A`:
- published словарь хранит только rich `entries[]` как основной и нормативный формат;
- runtime больше не опирается на published `aliases{}`;
- flat alias lookup остается допустимым только как внутренний derived runtime index;
- проект отказывается от компромиссного dual-format publish contract до того, как этот компромисс станет техническим долгом.

Это более резкое решение, чем compatibility-путь, но на текущем этапе оно лучше по балансу:
- меньше дублирования;
- меньше риска рассинхронизации;
- один источник истины;
- чище контракт для дальнейшего развития domain-aware runtime.

## Какие наши проблемы решает clean migration

### 1. Убирает dual-source contract

Проблема:
- если published словарь содержит и `entries[]`, и `aliases{}`, проект получает два представления одной и той же истины.

Как решает:
- после clean migration source-of-truth один: `entries[]`.

### 2. Убирает риск рассинхронизации publish-слоя

Проблема:
- derived `aliases{}` может начать расходиться с `entries[]` из-за багов publisher, ручного редактирования или разных правил нормализации.

Как решает:
- published слой больше не хранит вторую копию lookup-данных;
- derived flat index строится только в памяти runtime.

### 3. Сохраняет rich metadata в основном контракте

Проблема:
- flat-only publish скрывает domain/category/facet/confidence/source за пределами runtime-слоя.

Как решает:
- весь published contract сразу строится вокруг rich entries.

### 4. Делает дальнейший runtime evolution проще

Проблема:
- если сейчас закрепить flat published contract, позже придется делать еще одну миграцию при переходе к domain-aware runtime.

Как решает:
- richer fields уже входят в основной contract;
- runtime можно расширять поверх того же published формата.

## Какие проблемы этот вариант не решает

### 1. Не делает runtime богаче автоматически

Хотя published contract становится richer, текущий runtime не начнет использовать домены и facets сам по себе.

Для этого всё еще нужны:
- новый loader;
- новые runtime структуры;
- тесты на domain-aware поведение.

### 2. Не убирает необходимость merge policy

Даже при одном published формате остается вопрос:
- как сливать release и auto;
- как обрабатывать одинаковые alias;
- как детектировать конфликтные записи.

### 3. Не снимает риск domain collision

Если один alias в разных доменах требует разных canonical, это не решается самим форматом `entries[]`.

Нужны:
- validator rules;
- domain-aware adjudication;
- будущий runtime policy.

## Какие новые риски создает этот вариант

### 1. Migration cost наступает сразу

Нужно раньше переделать:
- `app/normalization/loader.py`;
- tests вокруг synonyms loader/runtime;
- docs/schema/validators.

### 2. Нужен более аккуратный loader contract

Раньше loader просто читал flat map.
Теперь он должен:
- читать rich entries;
- нормализовать alias key;
- строить derived index;
- детектировать конфликты.

### 3. Возможен "частично migrated" период

Если docs/schema уже переключатся на `entries[]`-only, а runtime/tests еще останутся flat-only, проект попадет в временное рассогласование.

## Какие улучшения возможны поверх этого решения

### Улучшение 1. Добавить tolerant migration reader на короткий период

Loader может временно:
- уметь читать `entries[]`;
- игнорировать `aliases{}` или читать его только как fallback в transition branch.

Это не меняет принятый contract, но может снизить боль миграции.

### Улучшение 2. Добавить richer runtime index

После clean migration можно эволюционно добавить:
- alias -> canonical + domain;
- alias -> multiple candidates;
- domain-filtered lookup.

### Улучшение 3. Добавить publish consistency reports

Например:
- сколько unique aliases;
- сколько conflicting normalized aliases;
- сколько domain-collisions;
- сколько записей auto перекрывают release.

## Насколько сложно перейти от этого решения к compatibility-варианту назад

### Общая оценка

Переход назад в compatibility dual-format имеет низкую или умеренную сложность.

Почему:
- `aliases{}` можно снова начать публиковать как derived field;
- published source-of-truth при этом останется `entries[]`.

То есть clean migration не "сжигает мосты".  
Она просто выбирает более чистую точку старта.

## Итог

Для текущего состояния проекта clean migration выглядит оправданно:
- кодовая база вокруг synonyms еще небольшая;
- published contract еще не закостенел;
- цену миграции лучше заплатить сейчас, а не после появления большего числа consumers и артефактов.

Дальше остаётся не новый выбор архитектуры, а формализация и реализация:
- loader behavior;
- merge rules;
- validator rules;
- acceptance tests.
