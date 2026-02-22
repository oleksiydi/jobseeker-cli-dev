# Presets моделей и runtime-режимов (скорость / баланс / качество)

## Зачем этот документ
Нужен единый, практичный набор пресетов для быстрого тестирования и для "боевого" качества без ручной настройки десятков параметров.

Важно для текущей реализации:
- в `app/search_jobs.py` уже используется эмбеддинг-модель из `matching.embeddings.model_name`;
- поле `matching.rerank.model_name` пока не участвует в расчете (текущий rerank baseline = формула `pre_llm + lexical` в `app/matching/search.py`).

## Быстрый выбор пресета

| Режим | Когда использовать | Embedding модель | Chunking | Retrieval/Rerank | Оценка скорости | Оценка качества |
|---|---|---|---|---|---|---|
| `speed` | smoke, частые короткие прогоны, отладка | `intfloat/multilingual-e5-small` (384) | `baseline` | `top_k=60`, `top_n=20` | `1.0x` (самый быстрый) | `~0.82-0.88` от quality |
| `balanced` | ежедневная разработка, CI-like проверка релевантности | `intfloat/multilingual-e5-base` (768) | `semantic + token-aware` | `top_k=80`, `top_n=35` | `~1.5-1.8x` от speed | `~0.90-0.95` от quality |
| `quality` | nightly и финальная проверка выдачи | `intfloat/multilingual-e5-large` (1024) | `semantic + token-aware` | `top_k=120`, `top_n=60` | `~2.1-2.6x` от speed | `1.0x` (эталон) |

Примечание по оценкам:
- это косвенная инженерная оценка по размерности векторов (384/768/1024), объему retrieval (`top_k`) и стоимости semantic chunking;
- точные числа зависят от CPU/диска/размера Mongo/Qdrant и доли документов, требующих реиндексации.

## Что это дает на практике
- `speed`: максимально быстрый feedback loop. Хорошо видно регрессии пайплайна, но возможны пропуски "тонких" совпадений.
- `balanced`: лучший режим "по умолчанию" для разработки: ощутимо быстрее quality, но близко по релевантности.
- `quality`: максимальная полнота retrieval и лучший шанс поймать слабые сигналы в описании вакансии.

## Готовые наборы в проекте

Добавлены шаблоны:
- `conf/presets/matching-speed.env.example`
- `conf/presets/matching-balanced.env.example`
- `conf/presets/matching-quality.env.example`

## Как использовать (шаги)

1. Выберите пресет и загрузите ENV:
```bash
source conf/presets/matching-balanced.env.example
```

2. Пересоберите индекс под выбранную модель (обязательно при смене `MATCHING_EMBEDDING_MODEL`/`VECTOR_SIZE`):
```bash
python3 -m app.build_job_index
```

3. Проверьте поиск на контрольном запросе:
```bash
python3 -m app.search_jobs --query "python backend" --profile-key default --limit 20
```

4. При необходимости запустите анализатор:
```bash
python3 -m app.analyze_jobs
```

## Рекомендуемая схема внедрения по этапам

1. Локальная разработка и smoke:
- использовать `speed`.
- цель: быстро подтвердить, что пайплайн целиком живой.

2. Ежедневная функциональная проверка:
- использовать `balanced`.
- цель: видеть более реалистичную выдачу без тяжелого времени прогона.

3. Контрольный nightly/релизный прогон:
- использовать `quality`.
- цель: получить эталон по качеству и сравнить с `balanced`.

## Минимальный протокол сравнения (для вашей базы)

Для честного сравнения фиксируйте одинаковый набор:
- одинаковый `profile_key`;
- 10-20 одинаковых запросов;
- одинаковый срез активных вакансий;
- одинаковая версия кода.

Метрики, которые стоит записывать:
- `build_job_index`: `scanned`, `reindexed`, `skipped`, `failed_quality`, `failed_qdrant`, elapsed;
- `search_jobs`: время ответа, стабильность top-20, доля явно нерелевантных результатов вручную;
- `analyze_jobs`: время батча и доля ошибок.

## Для всего проекта: практическая рекомендация сейчас

- `job_list_collector` / `job_details_collector`: без изменений (модельные пресеты не влияют).
- `build_job_index` + `search_jobs`:
  - default для команды: `balanced`;
  - быстрый smoke: `speed`;
  - nightly контроль: `quality`.
- `analyze_jobs`:
  - пока оставить текущий `analyzer.model` из `conf/app.json` для стабильного контракта runtime;
  - смену LLM-модели делать отдельным экспериментом, чтобы не смешивать эффекты retrieval и генерации.
