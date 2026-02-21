# Chunking Smoke Scenarios (spacy / syntok / regex+char)

## Цель

Этот документ дает быстрый и повторяемый smoke-план для проверки chunking этапа в `app.build_job_index`.

Проверяем:
1. какой sentence splitter реально сработал;
2. применился ли token-aware split;
3. какие fallback-причины записались в `matching_index_state`.

## Предусловия

1. Подняты MongoDB и Qdrant.
2. В базе есть актуальные документы в `stellenangebote` и `jobdetails`.
3. В `.env` задан `MONGODB_URI`.

Базовая команда smoke:

```bash
MATCHING_BUILD_MAX_DOCS=30 .venv/bin/python -m app.build_job_index
```

Для ускоренного smoke без Qdrant upsert:

```bash
MATCHING_BUILD_MAX_DOCS=30 MATCHING_SKIP_EMBEDDINGS=true .venv/bin/python -m app.build_job_index
```

## Общая проверка результата

После каждого сценария проверьте `matching_index_state`:

```javascript
db.getCollection("matching_index_state").find(
  {},
  {
    _id: 0,
    referenznummer: 1,
    index_status: 1,
    chunks_count: 1,
    sentence_splitter_used: 1,
    token_splitter_used: 1,
    token_aware_applied: 1,
    tokenizer_model_used: 1,
    chunking_fallback_reasons: 1
  }
).limit(20)
```

## Сценарий A: `spacy + transformers` (default)

Команда:

```bash
MATCHING_BUILD_MAX_DOCS=30 \
MATCHING_SENTENCE_SPLITTER=spacy \
MATCHING_SPACY_MODEL=de_core_news_md \
MATCHING_TOKEN_AWARE_ENABLED=true \
MATCHING_TOKEN_SPLITTER=transformers \
MATCHING_TOKENIZER_MODEL_NAME=intfloat/multilingual-e5-large \
MATCHING_MAX_TOKENS_PER_CHUNK=320 \
MATCHING_OVERLAP_TOKENS=48 \
.venv/bin/python -m app.build_job_index
```

Ожидаем:
1. `sentence_splitter_used=spacy` для большинства записей.
2. `token_splitter_used=transformers`.
3. `tokenizer_model_used=intfloat/multilingual-e5-large`.
4. `token_aware_applied=true` хотя бы на части длинных описаний.

## Сценарий B: `syntok + transformers` (легкий sentence splitter)

Команда:

```bash
MATCHING_BUILD_MAX_DOCS=30 \
MATCHING_SENTENCE_SPLITTER=syntok \
MATCHING_TOKEN_AWARE_ENABLED=true \
MATCHING_TOKEN_SPLITTER=transformers \
MATCHING_TOKENIZER_MODEL_NAME=intfloat/multilingual-e5-large \
MATCHING_MAX_TOKENS_PER_CHUNK=320 \
MATCHING_OVERLAP_TOKENS=48 \
.venv/bin/python -m app.build_job_index
```

Ожидаем:
1. `sentence_splitter_used=syntok` если пакет `syntok` доступен.
2. Если `syntok` недоступен:
  - `sentence_splitter_used=regex`;
  - `chunking_fallback_reasons` содержит `syntok_unavailable_or_failed`.
3. Token-часть работает как в сценарии A.

## Сценарий C: `regex + char` (минимальный fallback путь)

Команда:

```bash
MATCHING_BUILD_MAX_DOCS=30 \
MATCHING_SENTENCE_SPLITTER=regex \
MATCHING_TOKEN_AWARE_ENABLED=true \
MATCHING_TOKEN_SPLITTER=char \
MATCHING_CHUNKING_HARD_MAX_CHARS=2200 \
MATCHING_CHUNKING_HARD_MIN_CHARS=300 \
MATCHING_CHUNKING_FALLBACK_OVERLAP_CHARS=120 \
.venv/bin/python -m app.build_job_index
```

Ожидаем:
1. `sentence_splitter_used=regex`.
2. `token_splitter_used=char`.
3. `token_aware_applied=false`.
4. Разбиение контролируется hard char limits.

## Быстрый итог по распределению режимов

```javascript
db.getCollection("matching_index_state").aggregate([
  {
    $group: {
      _id: {
        sentence_splitter_used: "$sentence_splitter_used",
        token_splitter_used: "$token_splitter_used",
        token_aware_applied: "$token_aware_applied"
      },
      count: { $sum: 1 }
    }
  },
  { $sort: { count: -1 } }
])
```

## Интерпретация результатов

1. Если почти все записи `regex` при `MATCHING_SENTENCE_SPLITTER=spacy`, значит `spaCy` модель не загружена в окружении.
2. Если `token_splitter_used=transformers`, но `token_aware_applied=false`, значит тексты короткие и не превышают `max_tokens_per_chunk`.
3. Если часто встречается `tokenizer_unavailable`, надо проверить `transformers` и доступ к tokenizer-модели.
