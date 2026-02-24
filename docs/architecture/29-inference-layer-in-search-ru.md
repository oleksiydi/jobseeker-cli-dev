# Inference-слой в search: что это и как реализуется

## Что такое inference-слой
**Inference-слой** — это часть кода, где модель реально запускается на данных.

Просто:
- конфиг с моделью = “у нас есть имя модели”;
- inference-слой = “мы действительно вызвали модель и получили score”.

## Почему это важно
Без inference-слоя `rerank.model_name` в конфиге не влияет на выдачу.
То есть модель “заявлена”, но ранжирование идет старой формулой.

## Как это работает в поиске (идеальная схема)
1. Получили shortlist кандидатов после retrieval (например, 50 вакансий).
2. Для каждой вакансии собрали `candidate_text`.
3. Сформировали пары `(query, candidate_text)`.
4. Запустили rerank-модель на этих парах.
5. Сохранили `model_score` и пересортировали список.

## Как реализуется технически
Нужны 4 части:
1. **Инициализация модели**:
- загрузка один раз в процессе;
- контроль ошибок загрузки.

2. **Batch inference**:
- пары гоняются батчами (например, 8/16/32);
- снижает накладные расходы и стабилизирует latency.

3. **Интеграция score**:
- `model_score` встраивается в `rerank_score` (или полностью заменяет baseline rerank).

4. **Fallback**:
- если модель недоступна, работаем на baseline.

## Минимальный контракт результата
Для каждого кандидата:
- `pre_llm_score` (как было),
- `model_rerank_score` (новое),
- `rerank_score` (итог для сортировки),
- `rerank_source = model|baseline` (очень полезно для диагностики).

## Что уже есть у нас сейчас, а чего нет
По текущему коду:
- есть baseline-скоры и сортировка;
- есть `rerank.model_name` в конфиге;
- **нет** отдельного запуска cross-encoder reranker в `search_jobs`.

Иными словами:
- инфраструктура ранжирования есть;
- inference reranker как отдельный runtime-шаг еще не подключен.

## Пример псевдореализации

```text
if MATCHING_RERANK_ENABLED:
    pairs = [(query, build_candidate_text(c)) for c in shortlist]
    model_scores = reranker.predict(pairs, batch_size=16, timeout=...)
    for c, s in zip(shortlist, model_scores):
        c.rerank_score = s
        c.rerank_source = "model"
else:
    for c in shortlist:
        c.rerank_score = c.baseline_rerank
        c.rerank_source = "baseline"
```

## Риски и контроль
1. Рост latency.
2. Ошибки загрузки/памяти модели.
3. Неустойчивый эффект без фиксированного benchmark.

Контроль:
- ограничить `top_n`;
- запускать батчами;
- логировать время этапа inference;
- иметь fallback на baseline.
