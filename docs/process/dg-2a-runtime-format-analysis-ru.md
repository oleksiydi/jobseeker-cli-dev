# DG-2A: Разбор Decision Gate по Primary Runtime Format словаря синонимов

Audience: `Human + AI`  
Status: `decision-support; clean-migration accepted`  
Last updated: `2026-03-24`

## Зачем нужен этот документ

Этот документ фиксирует предметную разборку `DG-2A` из
`docs/process/taxonomy-synonyms-codex-implementation-plan-ru.md`.

Decision gate:
- нужно определить, какой формат словаря синонимов считается primary для publish/runtime слоя;
- решение влияет на `synonyms.auto.json`, `synonyms.release.json`, loader, merge rules, тесты и будущий переход к domain-aware runtime;
- это нельзя выбирать молча в коде, потому что потом формат закрепится в генераторах, валидаторах и release-артефактах.

## 1. В чём состоит задача или проблема

В проекте уже есть два разных представления словаря синонимов:

- rich-формат через `entries[]` в schema и документации;
- flat-формат через `aliases{}` в текущем runtime loader.

Сейчас это означает следующее:

- `conf/normalization/schemas/synonyms_dictionary.schema.json` требует `entries`;
- `docs/process/synonyms-contract-ru.md` уже показывает формат с `entries[]` и `aliases{}`;
- `app/normalization/loader.py` фактически читает только `aliases{}`.

Проблема не в том, что формат "некрасивый".  
Проблема в том, что без решения по `DG-2A` мы не можем однозначно ответить:

- что является source-of-truth в опубликованном словаре;
- что runtime обязан уметь читать;
- что считается legacy-слоем;
- где именно находятся domain/category/facet/confidence/provenance;
- как publisher должен вести себя при конфликтах и merge release/auto.

Если это не закрыть заранее, следующий builder/publisher начнет писать артефакты в "полу-совместимом" виде, а runtime будет жить по другому контракту, чем документация.

## 2. Почему здесь вообще нужен decision gate

Это контрактное решение, а не локальная деталь реализации.

Решение по `DG-2A` влияет на:

- формат `synonyms.auto.json` и `synonyms.release.json`;
- поведение `app/normalization/loader.py`;
- сложность миграции текущего flat-runtime;
- возможность хранить domain-aware metadata;
- отладку и аудит словаря;
- устойчивость к будущим расширениям без очередного redesign.

Если сейчас выбрать формат молча:

- тесты зафиксируют это как "истину";
- инструменты валидации начнут проверять именно его;
- builder/publisher привяжутся к нему;
- изменение позже потребует миграции артефактов и loader-кода.

## 3. Какие именно неоднозначности нужно разрешить

Нужно заранее ответить на следующие вопросы:

- Primary contract словаря: это rich `entries[]` или flat `aliases{}`?
- Должен ли published словарь уже содержать metadata для domain-aware поведения, или это можно вынести в будущий шаг?
- Нужно ли хранить в файле сразу и rich, и flat слой, или flat должен строиться только в памяти loader'ом?
- Какой слой считается authoritative при конфликте: `entries[]` или `aliases{}`?
- Что должен уметь runtime сейчас:
  - только alias -> canonical;
  - или уже чтение richer metadata, даже если он пока использует их частично?
- Насколько мы готовы платить за дублирование данных в published JSON ради обратной совместимости и простоты текущего runtime?

## 4. Реалистичные примеры

### Базовый простой пример

Нужно нормализовать сокращение:

- alias в тексте: `k8s`
- canonical: `kubernetes`

Flat-формат:

```json
{
  "aliases": {
    "k8s": "kubernetes"
  }
}
```

Rich-формат:

```json
{
  "entries": [
    {
      "alias": "k8s",
      "canonical": "kubernetes",
      "domain": "it_software_daten",
      "category": "stack",
      "facet": "technology_stack",
      "skill_class": "not_applicable",
      "confidence": 0.98,
      "source": "rule",
      "updated_at": "2026-03-24T10:00:00+00:00"
    }
  ]
}
```

Вывод:
- для простого lookup flat-формат достаточно хорош;
- но как только нужен аудит, домен, confidence или publish provenance, flat-формат уже теряет часть смысла.

### Сложный пример 1: один alias, но важен доменный контекст

Alias:
- `führung`

Возможные трактовки:
- soft skill / leadership competency;
- task responsibility / team lead responsibility.

Если словарь хранит только:

```json
{
  "aliases": {
    "führung": "fuehrung"
  }
}
```

то мы не знаем:
- это skill или task;
- какой facet нужно вернуть;
- какой домен был исходным;
- почему этот mapping вообще был принят.

Если хранится rich entry:
- можно явно зафиксировать `category`, `facet`, `domain`, `confidence`.

Здесь flat-формат уже не просто беднее, а потенциально недостаточен для дальнейшего runtime поведения.

### Сложный пример 2: одинаковый alias, разные publish-слои

Release-слой:

```json
{
  "aliases": {
    "sap bi": "sap_bi"
  }
}
```

Auto-слой:

```json
{
  "aliases": {
    "sap bi": "sap_business_intelligence"
  }
}
```

С flat-форматом runtime сможет сделать только одно:
- auto перезапишет release.

Но без rich metadata мы не поймем:
- почему возник конфликт;
- какой домен/источник был у каждой записи;
- какая из записей надежнее;
- можно ли было обе записи держать как отдельные candidate lineage.

### Сложный пример 3: alias нормализуется, но человеку нужен audit trail

Фраза из вакансии:
- `strukturierte sowie prozess- und lösungsorientierte Arbeitsweise`

Publish entry:

```json
{
  "alias": "prozess- und lösungsorientierte arbeitsweise",
  "canonical": "prozess_und_loesungsorientierung",
  "domain": "finanzen_versicherung_recht",
  "category": "skills",
  "facet": "interpersonal_competency",
  "skill_class": "interpersonal",
  "confidence": 0.83,
  "source": "llm",
  "updated_at": "2026-03-24T10:00:00+00:00"
}
```

Если published contract только flat:

```json
{
  "aliases": {
    "prozess und lösungsorientierte arbeitsweise": "prozess_und_loesungsorientierung"
  }
}
```

runtime lookup сработает, но теряется:
- почему alias вообще был принят;
- из какого домена он пришел;
- какой facet ожидался;
- с какой уверенностью он был опубликован.

### Сложный пример 4: одно решение удачно в одном случае и проблемно в другом

Пример A:
- `k8s -> kubernetes`

Здесь flat-слой почти идеален.

Пример B:
- `2-Schicht-Betrieb`

Если alias нужно нормализовать не только в canonical, но и знать, что это `work_condition`, flat-слой уже слишком бедный.

Пример C:
- future runtime хочет использовать домен для ограничения synonyms lookup.

Если primary contract был flat-only, потом придется менять не только runtime, но и publish contract.

## 5. Варианты решения

### Вариант A

Определение:
- primary published contract = `entries[]`;
- `aliases{}` публикуется как derived convenience/legacy layer;
- source-of-truth находится в `entries[]`.

Как это работает:
- publisher формирует rich entries;
- из них же строит flat `aliases{}` для текущего runtime;
- при конфликте authoritative считается `entries[]`, а `aliases{}` обязано быть их производным представлением.

Пример:

```json
{
  "version": 1,
  "entries": [
    {
      "alias": "k8s",
      "canonical": "kubernetes",
      "domain": "it_software_daten",
      "category": "stack",
      "facet": "technology_stack",
      "skill_class": "not_applicable",
      "confidence": 0.98,
      "source": "rule",
      "updated_at": "2026-03-24T10:00:00+00:00"
    }
  ],
  "aliases": {
    "k8s": "kubernetes"
  }
}
```

Плюсы:
- rich metadata не теряется;
- текущий flat-runtime продолжает работать;
- проще отлаживать и мигрировать;
- publish contract уже готов к domain-aware runtime.

Минусы:
- дублирование данных в файле;
- нужен sync-контроль между `entries[]` и `aliases{}`;
- publisher и validator становятся чуть сложнее.

Риски:
- если derived `aliases{}` начинают редактировать вручную или генерировать по другой логике, contract распадается на два источника истины.

### Вариант B

Определение:
- primary published contract = только flat `aliases{}`.

Как это работает:
- publisher пишет только alias -> canonical;
- всё остальное либо не хранится, либо остается только в промежуточных артефактах, но не попадает в release/auto словарь.

Пример:

```json
{
  "version": 1,
  "aliases": {
    "k8s": "kubernetes",
    "sap bi": "sap_bi"
  }
}
```

Плюсы:
- самый простой runtime;
- самый короткий JSON;
- минимальный loader code.

Минусы:
- теряются domain/category/facet/skill_class/confidence/source в published слое;
- сложнее аудит и debugging;
- будущий domain-aware runtime придется строить поверх нового контракта, а не на основе существующего.

Риски:
- удобство сейчас может привести к повторному redesign уже на ближайшем расширении runtime.

### Вариант C

Определение:
- primary published contract = только `entries[]`;
- `aliases{}` в файле не публикуется;
- runtime loader строит flat lookup map в памяти при загрузке.

Как это работает:
- publisher пишет только rich entries;
- loader читает `entries[]`, валидирует их и сам строит runtime index.

Пример:

```json
{
  "version": 1,
  "entries": [
    {
      "alias": "k8s",
      "canonical": "kubernetes",
      "domain": "it_software_daten",
      "category": "stack",
      "facet": "technology_stack",
      "skill_class": "not_applicable",
      "confidence": 0.98,
      "source": "rule",
      "updated_at": "2026-03-24T10:00:00+00:00"
    }
  ]
}
```

Плюсы:
- один source-of-truth и без дублирования;
- более чистый контракт;
- меньше риска рассинхронизации published данных.

Минусы:
- требуется сразу переписать loader/runtime слой;
- усложняется первый шаг миграции;
- текущий flat-runtime больше нельзя оставить без изменений.

Риски:
- это более "чистое" решение архитектурно, но оно хуже как инкрементальный переход от текущего состояния проекта.

## 6. Сравнение вариантов на конкретных сценариях

### Сценарий: быстрый MVP lookup без domain metadata

Пример:
- `k8s`
- `sap bi`

Вариант A:
- работает;
- runtime может продолжить читать `aliases{}`;
- metadata уже лежит рядом в `entries[]`.

Вариант B:
- тоже работает;
- но publish слой уже теряет useful metadata.

Вариант C:
- тоже работает, но требует более раннего изменения loader/runtime.

Вывод:
- для простого MVP все варианты выполнимы;
- различия проявляются не в lookup, а в цене развития контракта.

### Сценарий: audit/debug проблемы маппинга

Проблема:
- alias `prozess- und lösungsorientierte arbeitsweise` неожиданно начал вести в неправильный canonical.

Вариант A:
- есть `entries[]`, можно посмотреть домен, facet, confidence, source.

Вариант B:
- в published слое есть только alias -> canonical;
- для выяснения причины придется копаться в отдельных промежуточных логах.

Вариант C:
- rich audit есть;
- но нужен уже новый loader/runtime.

Вывод:
- flat-only contract слабее именно в operational debugging.

### Сценарий: будущий domain-aware runtime

Проблема:
- один и тот же alias может нормализоваться только в рамках подходящего домена/категории.

Вариант A:
- published contract уже содержит нужные поля;
- loader можно эволюционно расширить.

Вариант B:
- published contract придется менять;
- это уже второй migration wave.

Вариант C:
- published contract уже готов;
- но migration cost наступает сразу.

Вывод:
- A и C лучше B, если думать не только о текущем flat runtime.

## 7. Какой вариант я считаю предпочтительным и почему

Предпочтителен **Вариант A**.

Почему:
- он сохраняет rich published contract уже сейчас;
- не заставляет переписывать runtime в этой же итерации;
- позволяет держать текущий `aliases{}` как derived compatibility layer;
- делает будущий domain-aware runtime эволюционным расширением, а не повторным redesign.

Это не означает, что `aliases{}` становится равноправным источником истины.  
Наоборот, смысл Варианта A именно в том, что:

- `entries[]` = primary contract;
- `aliases{}` = derived layer для текущего runtime и плавной миграции.

Если позже flat-runtime будет полностью снят, проект сможет перейти от A к C заметно легче, чем от B к richer contract.

## 8. На какие конкретные вопросы нужно ответить, чтобы закрыть DG-2A

Нужно зафиксировать:

- authoritative layer:
  - `entries[]` или `aliases{}`?
- является ли `aliases{}` обязательным published полем на переходном этапе?
- должен ли loader считать `aliases{}` derived-only и игнорировать его как источник истины при несовпадении с `entries[]`?
- должен ли publish contract уже сейчас требовать domain/category/facet/skill_class для каждой записи?
- нужно ли считать отсутствие `aliases{}` ошибкой contract violation или допустимой формой после будущей миграции?

Критические вопросы:
- какой слой source-of-truth;
- обязателен ли `aliases{}` в publish-файле сейчас;
- что делать при расхождении `entries[]` и `aliases{}`.

## 9. Какие вопросы действительно требуют явного выбора пользователя

По существу `DG-2A` есть один действительно контрактный выбор:

- считать ли переходный compatibility-слой `aliases{}` обязательным published полем уже сейчас, или можно планировать immediate migration на `entries[]`-only runtime.

Варианты:

- **Переходный compatibility режим**
  - `entries[]` primary;
  - `aliases{}` публикуется обязательно;
  - runtime пока читает `aliases{}`.

- **Немедленная clean migration**
  - `entries[]` primary;
  - `aliases{}` не публикуется;
  - loader/runtime меняется сразу.

Разница:
- первый путь проще и безопаснее по внедрению;
- второй путь чище архитектурно, но требует большего одномоментного объема изменений.

Остальные вопросы можно закрыть default-решениями без отдельного выбора пользователя.

## 10. Рекомендуемое решение по умолчанию

Если не задавать дополнительных ограничений, рекомендую закрыть `DG-2A` так:

- primary published contract = `entries[]`;
- `aliases{}` = обязательный derived compatibility layer на переходный период;
- `entries[]` является единственным source-of-truth;
- при расхождении authoritative считается `entries[]`, а mismatch — это validator/publisher error;
- runtime на ближайшем шаге продолжает читать `aliases{}`, но contract и docs уже строятся вокруг `entries[]`.

Это решение лучше всего соответствует текущему состоянию проекта:
- schema и docs уже двигаются в сторону rich contract;
- runtime пока еще flat;
- проекту нужен инкрементальный переход, а не второй большой redesign прямо сейчас.

## 11. Фактически принятое решение

После обсуждения принято более строгое решение, чем default из этого analysis:

- выбран путь **немедленной clean migration**;
- primary published contract = `entries[]`;
- published `aliases{}` не сохраняется как обязательный compatibility-слой;
- runtime должен перейти на чтение `entries[]` и строить flat lookup только как внутреннюю derived структуру.

Это решение зафиксировано в:
- `docs/process/dg-2a-clean-migration-recommendation-ru.md`
- `docs/process/dg-2a-decision-closure-support-ru.md`

То есть `DG-2A` больше не находится в стадии open choice.
Открыты только implementation details этого решения.
