# ADR-20260224-01: Разделение model-rerank на `fit_score` и `intent_score`

## Статус
- Accepted

## Контекст
- Текущий baseline rerank допускает role-mismatch в top-выдаче при сильном `chunk_score`.
- Пользовательский запрос и профиль кандидата смешиваются в одном сигнале, что снижает управляемость качества.
- Для реального подбора работы важны два разных типа релевантности:
1. Насколько вакансия подходит кандидату в целом.
2. Насколько вакансия соответствует текущему намерению и ограничениям запроса.

## Старое состояние (Before)
- Retrieval: embedding-поиск top-k chunk-векторов вакансий.
- Rerank: baseline-формула (`pre_llm + lexical_boost + role_mismatch_penalty`).
- `role_score/profile_score/metadata_score` рассчитываются лексически (Jaccard).
- Обязательные пользовательские ограничения не зафиксированы как отдельный hard-filter контракт.

## Принятое решение (After)
- Зафиксировать целевую схему model-based rerank:
1. Считать отдельно:
   - `fit_score = f(profile_context, vacancy_text)`
   - `intent_score = g(query_context, vacancy_text)`
2. Итог:
   - `final_score = alpha * fit_score + beta * intent_score`
3. Применять обязательные constraints до финального rerank:
   - `candidate_set = {v in retrieval_top_k | hard_constraints(v)=1}`

## Почему выбрано именно это
- Качество:
  - снижает смешение “общей пригодности кандидата” и “текущих пожеланий”.
  - уменьшает вероятность нерелевантных ролей в top-20.
- Скорость/latency:
  - сохраняется двухэтапность (быстрый retrieval + более дорогой rerank на shortlist).
  - hard-filter сокращает число кандидатов для rerank.
- Сопровождение:
  - четкий контракт проще тестировать и объяснять.

## Последствия
- Положительные:
1. Более управляемая и объяснимая финальная выдача.
2. Проще калибровать веса (`alpha`, `beta`) и правила constraints.
- Риски:
1. Возможный рост latency из-за model-based rerank.
2. Нужна формализация структуры запроса (`constraints`, `filters`, `must_have`).

## Откат
- Временно отключить model-based rerank флагом и вернуть baseline scoring.
- Оставить только `fit_score` или только penalty/gate по constraints при проблемах с latency.

## Проверка
- Команды:
1. `bash tools/run_matching_metrics.sh --env-file .env --queries-file conf/bench/queries.txt --profile-key default --search-limit 20`
2. `.venv/bin/pytest -q`
- Метрики:
1. Доля role-mismatch в top-20 (до/после).
2. Latency поиска (до/после).
3. Стабильность результатов на фиксированном наборе запросов.

## Ссылки
- `PLANS.md`:
  - `PLAN-20260223-02` (high priority после базовых этапов).
- Связанные docs:
  - [`docs/architecture/32-semantic-chunking-embedding-rerank-math-ru.md`](../32-semantic-chunking-embedding-rerank-math-ru.md)
  - [`docs/architecture/29-inference-layer-in-search-ru.md`](../29-inference-layer-in-search-ru.md)
