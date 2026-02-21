# Cache + LLM Handoff: практическое объяснение (RU)

## Зачем нужен этот документ

Этот файл объясняет текущую реализацию retrieval/rerank и целевой подход для `selection cache`:
- что считать изменением значимых входов/версий;
- что такое `cache/LLM handoff`;
- что уходит в handoff для коротких и длинных вакансий;
- как влияет группировка чанков;
- какой rerank используется сейчас.

---

## 1) Что считать изменением значимых входов/версий

Идея простая: если изменилось что-то, что может изменить shortlist или тексты в `top_chunks`, кэш нужно считать невалидным.

Рекомендуемый состав значимых входов:

1. Пользовательский вход:
- `query` (после нормализации);
- `profile_key`;
- `profile_hash` (или версия/updated_at профиля).

2. Параметры retrieval/rerank:
- `retrieval.top_k`;
- `rerank.top_n`;
- `top_chunks_per_job` (CLI/конфиг).

3. Версии пайплайна, влияющие на индекс и выдачу:
- `embeddings.model_name`;
- `chunking.version`;
- `preprocess.version`;
- `rules.ruleset_version`.

4. Версия формата результата:
- `search_output_version` (ручной bump при несовместимом изменении схемы payload).

Практическое правило:
- хотя бы одно изменение в пунктах выше => новый `cache_key` => прежний кэш не переиспользуем.

---

## 2) Что такое `cache/LLM handoff`

### Selection cache
`Selection cache` хранит уже готовый результат retrieval/rerank (shortlist), чтобы не считать его заново при том же входе.

Минимально кэшируем:
- `results[]` с `rerank_score`, `confidence`, `top_chunks`;
- служебные поля (`query`, `profile_key`, `top_k`, `top_n`, `returned`).

### LLM handoff
`LLM handoff` — это компактный пакет данных, который передается в этап генерации (например, `Anschreiben`):
- shortlist вакансий;
- по каждой вакансии ограниченный набор `top_chunks` (а не полное описание вакансии целиком).

То есть:
- retrieval/rerank выбирает кандидатов;
- handoff передает в LLM только самое релевантное.

---

## 3) Что входит для длинных и коротких вакансий

Текущее поведение в `app/search_jobs.py`:

1. Для обеих категорий (длинные/короткие) одинаковый контракт:
- `top_chunks` берутся из лучших чанков вакансии (`build_compact_chunks`, лимит `top_chunks_per_job`);
- если в чанке пустой текст, подставляется fallback:
  - `TITLE`
  - `ROLE`
  - `ARBEITGEBER`

2. Короткая вакансия:
- обычно 1–2 информативных чанка;
- в handoff попадают они же (или fallback, если текста почти нет).

3. Длинная вакансия:
- в индексе больше чанков;
- в handoff попадает только top-N чанков по `chunk_score`, остальные отбрасываются как шум для LLM.

Итог:
- длинные тексты сжимаются в релевантный минимум;
- короткие не искусственно раздуваются.

---

## 4) Как учитывается влияние групп чанков

Сейчас логика job-level агрегации такая:

1. Сначала чанки группируются по `referenznummer`.
2. Внутри группы чанки сортируются по убыванию score.
3. `chunk_score` вакансии = score лучшего чанка (top-1).
4. Для handoff берутся первые `top_chunks_per_job` чанков.

Важно:
- в финальный `rerank_score` напрямую входит именно `chunk_score` top-1;
- остальные чанки сейчас влияют главным образом на текстовый handoff (через `top_chunks`), а не через отдельный агрегированный вес.

---

## 5) Что используется как rerank сейчас

Текущий rerank — baseline без внешней cross-encoder модели:

1. Считаются lexical-метрики:
- `role_score` = max(overlap(query,title), overlap(query,role));
- `profile_score` = overlap(query,profile_text);
- `metadata_score` = overlap(query,location);
- `chunk_score` = score лучшего чанка из Qdrant.

2. Предскоринг:
- `pre_llm_score = w_role*role + w_chunk*chunk + w_profile*profile + w_metadata*metadata`

3. Финальный rerank:
- `lexical_boost = overlap(query, top_chunk_text)`
- `rerank_score = w_pre_llm*pre_llm_score + w_lexical*lexical_boost`

4. Уверенность:
- `confidence = sqrt(max(rerank_score, 0))`, затем ограничение в `[0,1]`.

Весы берутся из `matching.rerank.weights` (с дефолтами в коде).

---

## Важно: текущий статус cache

На текущем этапе retrieval/rerank уже реализован, а полноценный `selection cache` (persist + reuse + TTL-инвалидация) описан как следующий этап.

Этот документ фиксирует целевой контракт, чтобы внедрение кэша не ломало текущий runtime-поток.
