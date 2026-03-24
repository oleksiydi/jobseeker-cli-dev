# DG-1A Variant A: Объяснение На Примерах

Audience: `Human + AI`  
Status: `accepted-explanation`  
Last updated: `2026-03-24`

## Назначение

Этот документ сохраняет практическое объяснение принятого варианта A на примерах.

Связанные документы:
- `docs/process/dg-1a-variant-a-recommendation-ru.md`
- `docs/process/dg-1a-canonical-semantics-analysis-ru.md`
- `docs/process/taxonomy-synonyms-codex-implementation-plan-ru.md`

## Принятый вариант

1. `canonical` = internal stable key.
2. `term` = extractive-first source-near alias.
3. translated-only canonical запрещены.
4. DE/EN equivalence решается в synonyms layer.
5. provenance для canonical обязателен.
6. `canonical_display` пока не вводится, но контракт не должен блокировать его будущее появление.

## Объяснение на примерах

### 1. `canonical` = internal stable key

Это внутренний устойчивый ключ, в который система сводит разные формы одного смысла.

Пример:
- source phrase: `Erfahrung mit Kubernetes und k8s-Clustern`
- term: `k8s`
- canonical: `kubernetes`

Почему это internal key:
- в другой вакансии может быть `Kubernetes`
- в третьей `K8s-Plattform`
- все они должны вести к одному ключу `kubernetes`

Смысл:
- `canonical` нужен не для красоты, а для стабильной агрегации, dedup и runtime mapping.

### 2. `term` = extractive-first source-near alias

Это термин, максимально близкий к тому, как он реально встретился в тексте.

Пример:
- source phrase: `prozess- und lösungsorientierte Arbeitsweise`
- хороший `term`: `prozess- und lösungsorientierte arbeitsweise`
- плохой `term`: `process orientation`

Почему:
- хороший `term` можно прямо соотнести с текстом вакансии;
- плохой уже является интерпретацией, а не извлечением.

Еще пример:
- source phrase: `Erfahrung im Group Reporting`
- хороший `term`: `group reporting`
- плохой `term`: `financial consolidation workflow`

### 3. translated-only canonical запрещены

Нельзя делать `canonical`, который существует только как перевод или модельная интерпретация, если такой формы нет в корпусе и она не подтверждена alias-слоем.

Плохой пример:
- source phrase: `prozess- und lösungsorientierte Arbeitsweise`
- term: `prozess- und lösungsorientierte arbeitsweise`
- canonical: `process_orientation`

Почему плохо:
- это уже английская абстракция;
- runtime по словарю не сможет надежно матчить немецкие формы без богатого alias-слоя;
- ухудшается traceability.

Хороший пример:
- source phrase: `prozess- und lösungsorientierte Arbeitsweise`
- term: `prozess- und lösungsorientierte arbeitsweise`
- canonical: `prozess_und_loesungsorientierung`

Это уже нормализованный internal key, но он не отрывается далеко от исходной формы.

### 4. DE/EN equivalence решается в synonyms layer

Связь между немецкими и английскими формами должна храниться явно в слое синонимов, а не в свободной семантике `canonical`.

Пример:
- aliases:
  - `teamfähigkeit`
  - `teamfähig`
  - `team-oriented working style`
- canonical:
  - `teamfaehigkeit`

То есть:
- DE/EN формы живут как alias;
- system key один;
- equivalence явно хранится в `synonyms`.

Плюс:
- это прозрачно;
- это domain-aware;
- это не зависит от runtime LLM.

### 5. provenance для canonical обязателен

Для каждого canonical нужно знать, откуда он взялся и почему появился именно в такой форме.

Пример:
- canonical: `kubernetes`
- provenance:
  - source aliases: `k8s`, `kubernetes`, `k8s-plattform`
  - source refs count: `37`
  - domains: `it_software_daten`
  - created_from_run: `vac-taxo-20260320...`
  - decision source: `rule` или `llm_adjudication`

Зачем:
- можно проверить, не был ли canonical ошибочным;
- можно делать rollback;
- можно отлаживать collisions;
- можно сравнивать версии словаря.

Плохой случай без provenance:
- у вас есть `prozess_und_loesungsorientierung`;
- но непонятно, из каких фраз он был образован;
- вы не можете проверить, был ли это хороший mapping или ошибка.

### 6. `canonical_display` пока не вводится, но контракт не должен блокировать его будущее появление

Сейчас мы храним только один `canonical`, чтобы не усложнять модель данных слишком рано.

Но мы не должны проектировать схему так, чтобы потом нельзя было добавить display-форму.

Пример сейчас:
- canonical: `teamfaehigkeit`

Потом можно добавить:
- canonical_display: `Teamfähigkeit`

Или:
- canonical_display: `prozess- und lösungsorientierte Arbeitsweise`

Это полезно для UI, отчетов и ручного анализа.
Но сейчас это не обязательное поле.

## Как понять, что вариант A внедрен правильно

### `term` можно соотнести с source phrase

Пример:
- source phrase: `Erfahrung mit SAP BI`
- term: `sap bi`

Это хорошо.

Плохо:
- source phrase: `Erfahrung mit SAP BI`
- term: `financial reporting platform`

Такой term уже нельзя проверить по тексту.

### `canonical` стабилен между прогонами

Пример:
- сегодня `k8s -> kubernetes`
- завтра `k8s -> kubernetes`

Это хорошо.

Плохо:
- сегодня `k8s -> kubernetes`
- завтра `k8s -> cloud_native_container_orchestration`

Одинаковый вход должен давать тот же stable key.

### synonyms layer решает surface variability

Пример:
- `k8s`
- `Kubernetes`
- `K8s-Cluster`

Все через alias-layer ведут к одному canonical.

Значит runtime не должен отдельно “понимать” эти формы.

### runtime не требует LLM

Пример:
- в профиле есть `teamfähig`
- в вакансии есть `Teamfähigkeit`
- runtime по `synonyms` мапит оба в `teamfaehigkeit`
- никакой LLM не вызывается

Это ключевое свойство принятой схемы.

### human-debug trace сохраняется через term/context/provenance

Когда вы видите:
- term: `prozess- und lösungsorientierte arbeitsweise`
- canonical: `prozess_und_loesungsorientierung`
- context: `strukturierte sowie prozess- und lösungsorientierte Arbeitsweise`
- provenance: `run_id`, `referenznummer`, `decision_source`

вы можете понять:
- что произошло;
- откуда это взялось;
- почему mapping выглядит именно так.

### расширение до варианта C возможно без полного redesign

Если сейчас:
- `canonical` уже internal key;
- provenance есть;
- aliases хранятся отдельно;
- display semantics не смешаны с key semantics,

то потом можно добавить:
- `canonical_display`

Пример:
- сейчас: `canonical = teamfaehigkeit`
- позже:
  - `canonical = teamfaehigkeit`
  - `canonical_display = Teamfähigkeit`

Если же сейчас canonical местами key, местами display, местами перевод, переход к варианту C станет дорогим.
