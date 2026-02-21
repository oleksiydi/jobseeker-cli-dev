# Кэширование, индексы и инвалидация: общий обзор (RU)

## Назначение

Показать на одной схеме:
- какие кэш/индекс-контуры есть в проекте;
- какие данные они сохраняют;
- какие события вызывают инвалидацию;
- как это влияет на следующий запуск пайплайнов и retrieval.

Важно:
- блок `selection cache` для retrieval сейчас описан как целевой слой (планируемый runtime reuse);
- остальные контуры (history, versioning, matching index state) уже используются в проекте.

## Общая схема (контуры проекта)

```mermaid
flowchart TD
    subgraph SRC[Внешний источник]
        BA[BA List/Details API]
    end

    subgraph CORE[Основные коллекции]
        SA[(stellenangebote)]
        JD[(jobdetails)]
        AN[(analysen)]
        PR[(profiles)]
    end

    subgraph HIST[История и аудит]
        SAH[(stellenangebote_history)]
        JDH[(jobdetails_history)]
    end

    subgraph MATCH[Matching индекс и служебное состояние]
        MIS[(matching_index_state)]
        QD[(Qdrant collection)]
    end

    subgraph CACHE[Кэш выбора для retrieval]
        SC[(selection_cache, target)]
    end

    BA -->|list fetch| SA
    SA -->|list_seen/content_change| SAH

    SA -->|active refs + date-gate| JD
    BA -->|details fetch when needed| JD
    JD -->|content_change/no_change| JDH

    SA -->|merge + normalize| MIS
    JD -->|merge + normalize| MIS
    PR -->|profile context| MIS
    MIS -->|reindex decision| QD

    SA -->|analysis input| AN
    JD -->|analysis input| AN
    PR -->|candidate_profile_key| AN

    PR -->|query + profile| SC
    QD -->|retrieval hits| SC
    SA -->|meta for shortlist| SC
    SC -->|top candidates + top_chunks| AN

    SA -.изменение данных.-> MIS
    JD -.изменение данных.-> MIS
    PR -.изменение профиля.-> SC
    MIS -.смена версии model/chunking/preprocess/ruleset.-> SC
```

## Что считается инвалидацией по доменам

1. `stellenangebote/jobdetails`:
- новое значение значимых полей (фактический content change);
- изменение активности вакансии;
- изменение дат/состояний, влияющих на date-gate и merge.

2. `matching_index_state` / Qdrant:
- `embedding_input_hash` изменился;
- изменилась версия `embedding_model`, `chunking_version`, `preprocess_version`;
- текущий `index_status` не `indexed`.

3. `analysen` (версионирование в самой коллекции):
- изменился `input_hash` (meta + details + profile + prompt + model);
- сменился `candidate_profile_key`;
- сменился prompt/model.

4. `selection_cache` (целевой runtime-контур):
- изменился `query` (после нормализации);
- изменился профиль (`profile_hash`/версия);
- изменились `retrieval.top_k`, `rerank.top_n`, `top_chunks_per_job`;
- изменились версии индекса/правил (`embedding/chunking/preprocess/ruleset`);
- сменился `search_output_version`;
- истек TTL.

## Жизненный цикл cache key (целевой для retrieval)

```mermaid
stateDiagram-v2
    [*] --> BuildKey: normalize query + collect versions + profile hash
    BuildKey --> Lookup

    Lookup --> CacheHit: key exists and TTL active
    Lookup --> CacheMiss: key absent or expired

    CacheMiss --> Compute: retrieval + aggregate + rerank
    Compute --> Store: upsert payload by cache_key
    Store --> CacheHit

    CacheHit --> InvalidateByInputChange: query/profile changed
    CacheHit --> InvalidateByVersionChange: embedding/chunking/preprocess/ruleset changed
    CacheHit --> InvalidateByOutputSchema: search_output_version changed
    CacheHit --> InvalidateByTTL: expires_at reached

    InvalidateByInputChange --> BuildKey
    InvalidateByVersionChange --> BuildKey
    InvalidateByOutputSchema --> BuildKey
    InvalidateByTTL --> BuildKey
```

## Какие проблемы решает инвалидация

1. Защита от устаревшего shortlist:
- кэш не отдает старые кандидаты после изменения профиля/запроса.

2. Защита от несоответствия версии индекса:
- после смены модели/чанкинга не используется результат, рассчитанный на старой версии.

3. Контроль качества LLM handoff:
- `top_chunks`, переданные в LLM, соответствуют актуальному retrieval/rerank состоянию.

4. Баланс скорости и корректности:
- одинаковый вход = быстрый `cache hit`;
- значимые изменения = пересчет и новый результат.
