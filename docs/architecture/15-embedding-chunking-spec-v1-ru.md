# Embedding/Chunking Spec v1 (RU)

## Цель

Этот документ фиксирует точные правила, по которым:
1. мы собираем текст вакансии для индексации;
2. разбиваем описание на чанки;
3. считаем векторы;
4. решаем, когда пересчитывать, а когда пропускать.

Документ нужен, чтобы реализация была одинаковой у всех разработчиков и не зависела от "догадок".

## 1) Входные данные и приоритет полей

Для каждой `referenznummer` собираем merged-документ из `stellenangebote` + `jobdetails`.

Правила приоритета:
1. `title`:
- `details.stellenangebotsTitel`
- fallback: `meta.titel`
2. `role`:
- `details.hauptberuf`
- fallback: `meta.beruf`
3. `description`:
- `details.stellenangebotsBeschreibung`
4. `location`:
- `meta.arbeitsort.*` + `details.stellenlokationen.*`
5. `employment`:
- `details.arbeitszeit*`, `details.vertragsdauer`, `details.istGeringfuegigeBeschaeftigung`
6. `alternatives` (если есть):
- `details.alternativBeruf*`

Почему так:
- `title/role` дают сильный сигнал профессии и не должны теряться;
- `description` даёт основной смысл для chunk-level поиска;
- `location/employment` нужны для фильтров и metadata_score.

## 2) Что исключаем из embedding-текста

Не включаем:
1. `_id`
2. timestamps (`created_at`, `updated_at`, `*_dt`)
3. технические хеши (`*_hash`)
4. служебные debug-поля и поля состояния пайплайна

Задача этого правила:
- убрать "шум";
- избежать ложных пересчётов из-за технических изменений.

### 2.1 Нормализация `ALT_ROLES` (обязательное правило)

Проблема:
- в источнике часто встречаются "псевдозначения" вроде `"false"` или `"null"` строкой.

Правило v1:
1. Берём значения `alternativBeruf1..N`.
2. Для каждого значения:
- приводим к строке;
- `trim`;
- переводим в lower-case для проверки;
- если значение в списке `{"", "false", "none", "null", "n/a", "-"}` -> исключаем.
3. Удаляем дубли.
4. Если список пуст, пишем `ALT_ROLES:` как пустую строку.

Важно:
- `"false"` здесь трактуется как "данных нет", а не как название профессии.

Псевдокод:

```text
invalid = {"", "false", "none", "null", "n/a", "-"}
result = []

for raw in alternativBeruf1..N:
    s = str(raw).strip()
    if lower(s) in invalid:
        continue
    if s not in result:
        result.append(s)

ALT_ROLES = ", ".join(result)   # или пустая строка
```

## 3) Канонический текст вакансии (строгий шаблон)

Перед chunking/embedding формируем стабильный текст:

```text
TITLE: <title>
ROLE: <role>
ALT_ROLES: <alternatives comma-separated or empty>
LOCATION: <normalized location summary>
EMPLOYMENT: <normalized employment summary>
DESCRIPTION:
<normalized description or empty>
```

Нормализация:
1. trim по краям;
2. схлопывание повторных пробелов;
3. переносы строк нормализуются до `\n`;
4. пустые значения -> пустая строка (не `null`, не `None`).

Почему важно:
- одинаковый смысловой вход должен давать одинаковый `embedding_input_hash`.

Подробные реалистичные примеры (полные/избыточные/неполные/плохие данные):
- [`docs/architecture/18-canonical-text-payload-llm-examples-ru.md`](18-canonical-text-payload-llm-examples-ru.md)

## 4) Segment -> Semantic Chunking

### 4.1 Базовая сегментация

1. Сначала делим `DESCRIPTION` на предложения.
2. Режим выбирается конфигом `matching.chunking.sentence_splitter`:
- `spacy` (по умолчанию);
- `syntok` (облегченный sentence splitter);
- `regex` (аварийный fallback).
3. Для `spacy` модель задается `matching.chunking.spacy_model` (по умолчанию `de_core_news_md`).
4. Если выбранный splitter недоступен в окружении, включается следующий безопасный fallback:
- `spacy`/`syntok` -> `regex`.
5. Фактически использованный splitter фиксируется в `matching_index_state.sentence_splitter_used`.

### 4.2 Семантическое объединение предложений

Параметры из `conf/app.json`:
1. `semantic_similarity_threshold`
2. `semantic_sentence_window`
3. `semantic_min_sentences`
4. `semantic_max_sentences`

Правило:
1. Берём текущее окно соседних предложений.
2. Если новое предложение семантически близко к текущему чанку (>= threshold), добавляем его.
3. Если близость ниже порога или достигнут `semantic_max_sentences`, закрываем чанк.

Ограничители безопасности:
1. `hard_max_chunk_chars`
2. `hard_min_chunk_chars`
3. `fallback_overlap_chars`

Важно:
- это не основная логика чанкинга, а защита от аномально коротких/длинных фрагментов.

### 4.4 Token-aware split (перед hard char limits)

1. Этап включается флагом `matching.chunking.token_aware_enabled`.
2. Режим задается `matching.chunking.token_splitter`:
- `transformers` (по токенам модели, режим по умолчанию);
- `char` (token-aware отключен, остаются char-limits);
- `disabled` (полное отключение token-aware этапа).
3. Для `transformers` используются:
- `tokenizer_model_name`;
- `max_tokens_per_chunk`;
- `overlap_tokens`.
4. При доступном fast-tokenizer разрез выполняется по `offset_mapping`, чтобы сохранить исходный текст без decode-искажений.
5. Фактический результат фиксируется в `matching_index_state`:
- `token_splitter_used`;
- `token_aware_applied`;
- `tokenizer_model_used`;
- `chunking_fallback_reasons`.

### 4.3 `section_label`: как ставим метку секции реалистично

Semantic chunking сам по себе не гарантирует метки `tasks/requirements/benefits`.

Правило v1:
1. Сначала пытаемся определить метку простой эвристикой по маркерам в тексте чанка:
- `tasks`: `aufgaben`, `tätigkeiten`, `verantwortung`, `deine aufgaben`
- `requirements`: `anforderungen`, `profil`, `voraussetzungen`, `qualifikation`, `ihr profil`
- `benefits`: `wir bieten`, `benefits`, `angebot`, `vergütung`, `urlaub`, `zuschuss`
2. Если совпадений нет или они слабые -> `section_label = "unknown"`.
3. Параллельно считаем `section_confidence` (0..1).

Почему так:
- у нас нет гарантированной структуры текста;
- лучше честно поставить `unknown`, чем ошибочно "придумать" секцию.

Псевдокод:

```text
matches = {
  "tasks": count_markers(chunk_text, ["aufgaben", "tätigkeiten", "verantwortung"]),
  "requirements": count_markers(chunk_text, ["anforderungen", "profil", "voraussetzungen", "qualifikation"]),
  "benefits": count_markers(chunk_text, ["wir bieten", "benefits", "urlaub", "zuschuss", "vergütung"])
}

best_label, best_score = argmax(matches)

if best_score < min_marker_score:
    section_label = "unknown"
    section_confidence = 0.2..0.4
else:
    section_label = best_label
    section_confidence = 0.6..0.95
```

## 5) Ограничение объёма для длинных описаний

Проблема:
- некоторые вакансии очень длинные и могут дать слишком много чанков.

Правило v1:
1. Если после chunking чанков > `MAX_CHUNKS_PER_JOB` (константа реализации), оставляем:
- чанки с наиболее высокой "информативностью" (длина + плотность ключевых терминов роли);
- и обязательно первый чанк (часто содержит ключевой контекст вакансии).
2. Остальные помечаем как `dropped_for_budget=true` и не отправляем в Qdrant.

Зачем:
- контролировать размер индекса и время CPU-обработки;
- не терять ключевые части.

## 6) Неполные данные (fallback правила)

### 6.1 Нет `description`

Если описание отсутствует:
1. создаём synthetic chunk из `title + role + alternatives + employment + location`;
2. ставим `description_missing=true` в payload;
3. снижаем confidence на этапе ранжирования.

### 6.2 Короткое описание

Если описание слишком короткое:
1. разрешаем чанк меньше обычного минимума;
2. дополняем context-полями (`title`, `role`) в начале чанка.

## 7) Векторизация

Считаем:
1. chunk embeddings (обязательно);
2. summary embedding вакансии (опционально, но рекомендуется).

Модель по умолчанию:
- `intfloat/multilingual-e5-large`

Формат:
1. для chunk: префикс `passage: `
2. для query: префикс `query: `

Это важно для E5-семейства и повышает качество сопоставления.

## 8) Payload в Qdrant (минимальный контракт)

Для каждого chunk-point:
1. `referenznummer`
2. `chunk_id`
3. `chunk_index`
4. `section_label` (`tasks|requirements|benefits|about_company|marketing|unknown`)
5. `section_confidence`
6. `description_missing`
7. `embedding_input_hash`
8. `embedding_model`
9. `chunking_version`
10. `preprocess_version`

Зачем:
- для фильтрации, диагностики и точной инвалидации.

## 9) Правила skip/reindex

Пересчитываем вакансию только если изменилось хотя бы одно:
1. `embedding_input_hash`
2. `embedding_model`
3. `chunking_version`
4. `preprocess_version`

Иначе:
- `skip_reason=unchanged_inputs`

## 10) Контроль качества (обязательно логировать)

На каждый запуск:
1. `jobs_total`
2. `jobs_changed`
3. `jobs_skipped`
4. `chunks_total`
5. `chunks_per_job_avg`
6. `runtime_total_sec`
7. `embedding_runtime_sec`

Это нужно, чтобы видеть регрессии по времени и качеству данных.

## 11) Что должен понимать новичок

1. Мы не "режем просто по символам" — сначала смысл, потом ограничения.
2. `title/role` всегда важны, даже если описание плохое или пустое.
3. Пересчёт только при изменениях — ключ к скорости ночного батча.
4. Для LLM потом передаются только лучшие чанки, а не весь сырой текст.
