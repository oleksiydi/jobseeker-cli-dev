# DG-1A: Рекомендованный Вариант A и Его Последствия

Audience: `Human + AI`  
Status: `accepted-with-pending-formalization`  
Last updated: `2026-03-24`

## Статус решения

Вариант A принят как согласованный рабочий вариант для `DG-1A`.

Связанный explanatory документ:
- `docs/process/dg-1a-variant-a-explained-examples-ru.md`
- `docs/process/dg-1a-decision-closure-support-ru.md`

Принятая формулировка:
1. `canonical` = internal stable key.
2. `term` = extractive-first source-near alias.
3. translated-only canonical запрещены.
4. DE/EN equivalence решается в synonyms layer.
5. provenance для canonical обязателен.
6. `canonical_display` пока не вводится, но контракт не должен блокировать его будущее появление.

## Что ещё не хватает для окончательного закрытия Decision Gate

Хотя вариант уже принят, для полного контрактного закрытия `DG-1A` ещё нужно явно зафиксировать:

1. Точное правило генерации `canonical`
- какие transforms считаются допустимыми для internal key;
- какие transforms считаются недопустимой абстракцией.

2. Формальную проверку запрета translated-only canonical
- только prompt policy недостаточно;
- нужен validator rule или эквивалентный post-parse guard.

3. Минимальный обязательный provenance contract
- какие поля provenance считаются обязательными;
- в каких артефактах provenance хранится обязательно.

4. Место этого решения в execution-plan
- `DG-1A` должен быть помечен как выбранный;
- остаются только implementation details, а не semantic choice.

5. Набор acceptance examples
- 5-10 контрольных кейсов, по которым можно автоматически проверить, что реализация соответствует принятой семантике.

## Коротко

Рекомендованный вариант для `DG-1A`:
- `term` остается extractive-first и близким к тексту;
- `canonical` становится стабильным внутренним ключом;
- synonyms layer отвечает за эквивалентность DE/EN и surface-вариантов;
- translated-only canonical без source evidence не допускается.

Это не самый богатый по модели вариант, но лучший по балансу:
- простота;
- стабильность;
- пригодность для runtime без LLM;
- низкая миграционная цена на текущем этапе.

## Какие наши проблемы решает вариант A

### 1. Решает проблему нестабильности surface forms

Проблема:
- одно и то же понятие приходит в разных написаниях.

Как решает:
- aliases мапятся в один internal key.

Пример:
- `k8s`
- `Kubernetes`
- `K8s-Plattform`

Все могут вести к одному `canonical`: `kubernetes`.

### 2. Решает проблему dedup и агрегирования

Проблема:
- surface-near canonical раздувает словарь и затрудняет аналитику.

Как решает:
- один internal key уменьшает дубли в aggregate/synonyms/release.

Пример:
- `Teamfähigkeit`
- `teamfähig`
- `teamorientierte Arbeitsweise`

Они могут агрегироваться вокруг одного stable key, если synonyms layer построен корректно.

### 3. Решает проблему deterministic runtime

Проблема:
- runtime без LLM должен быстро и однозначно решать, к чему мапить термин.

Как решает:
- runtime lookup работает по aliases;
- target всегда один stable canonical key;
- нет необходимости “понимать” текст в runtime.

### 4. Решает проблему cross-lingual mapping

Проблема:
- в текстах могут встречаться и DE, и EN формы.

Как решает:
- surface forms DE/EN живут как aliases;
- canonical остается единым internal key.

Пример:
- `Continuous Delivery`
- `Lieferpipeline`
- `delivery pipeline`

Могут вести к одному canonical, если domain-aware synonyms это подтверждают.

## Какие проблемы вариант A не решает

### 1. Не решает автоматически границу “слишком сильной абстракции”

Если internal key генерируется без строгих правил, то:
- `prozess_und_loesungsorientierung` может быть ок;
- `process_orientation` может стать уже вредной абстракцией.

Вариант A сам по себе не гарантирует качество.
Нужны дополнительные правила generation и validation.

### 2. Не решает проблему human-readable explainability до конца

Когда пользователь увидит:
- term: `prozess- und lösungsorientierte arbeitsweise`
- canonical: `prozess_und_loesungsorientierung`

это еще объяснимо.

Но если key станет более техническим или англоязычным, readability ухудшится.

### 3. Не решает доменные коллизии сам по себе

Если один alias имеет разные значения в разных доменах, internal canonical не спасает.

Нужно:
- domain-aware synonyms;
- collision detection;
- adjudication.

## Какие новые проблемы или риски создает вариант A

### 1. Риск “тихой семантической нормализации”

Если canonical начинает создаваться слишком свободно, система перестает быть действительно extractive-first.

Риск:
- источник говорит одно;
- canonical говорит более широкое или более абстрактное другое.

### 2. Риск потери traceability

Если не хранить качественный provenance, потом трудно понять:
- откуда взялся canonical;
- какие aliases к нему привели;
- почему был принят именно такой mapping.

### 3. Риск раннего vendor/heuristic lock-in

Если логика генерации canonical будет “зашита” в prompt одной модели или в узкий rule set, потом менять ее станет дорого.

## Какие улучшения возможны поверх варианта A

### Улучшение 1. Ввести строгие правила на generation canonical

Например:
- canonical обязан быть derived from source aliases;
- запрещены translated-only canonical;
- разрешены только approved normalization transforms.

Это улучшение желательно почти сразу.

### Улучшение 2. Добавить `canonical_display` позже

Не меняя базовую идею варианта A, можно позже добавить human-readable display field.

Это уже шаг в сторону варианта C, но без полного перепроектирования сразу.

### Улучшение 3. Добавить quality reports по canonical drift

Метрики:
- сколько canonical не встречаются как alias;
- сколько canonical language-mismatch;
- сколько canonical имеют >1 конкурирующий смысл.

## Насколько сложно перейти от варианта A к варианту C

### Общая оценка

Переход `A -> C` имеет умеренную сложность.

Почему не высокая:
- в варианте A уже есть internal key;
- нужно добавить display layer, а не ломать весь design.

Почему не низкая:
- придется менять контракт артефактов;
- придется обновлять schemas, validators, publish flow и runtime consumers.

### Что именно придется менять

1. Схемы:
- aggregate;
- synonyms intermediate;
- synonyms dictionary;
- возможно taxonomy dictionary.

2. Runtime:
- loader;
- runtime normalization return contract;
- места, где canonical используется для UI/reporting/debug.

3. Publish flow:
- generation of `canonical_display`;
- freeze/release validation;
- backward compatibility.

### Что не придется переписывать полностью

- aliases layer;
- domain-aware mapping идея;
- deterministic runtime architecture;
- embeddings offline stage.

### Практическая сложность миграции

Если A реализован аккуратно:
- с provenance;
- с versioned schemas;
- без смешивания display и key semantics,

то переход к C будет контролируемым расширением, а не болезненным рефакторингом.

Если A реализован неаккуратно:
- canonical местами key, местами display, местами перевод,

то переход к C станет дорогим и потребует миграции исторических артефактов.

## Рекомендуемое operational решение

Рекомендуется внедрять вариант A в следующем виде:

1. `canonical` = internal stable key.
2. `term` = extractive-first source-near alias.
3. translated-only canonical запрещены.
4. DE/EN equivalence решается в synonyms layer.
5. provenance для canonical обязателен.
6. `canonical_display` пока не вводится, но контракт не должен блокировать его будущее появление.

## Критерий того, что вариант A внедрен правильно

- `term` можно соотнести с source phrase;
- `canonical` стабилен между прогонами;
- synonyms layer решает surface variability;
- runtime не требует LLM;
- human-debug trace сохраняется через term/context/provenance;
- расширение до варианта C возможно без полного redesign.
