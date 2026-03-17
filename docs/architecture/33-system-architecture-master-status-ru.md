# Архитектура системы, алгоритмы решений и статус roadmap

## 1. Назначение документа
Этот файл является единым источником:
- как устроена текущая система `jobseeker-cli`;
- где в пайплайнах происходят ветвления и принятие решений;
- что уже реализовано, что запланировано и с каким приоритетом;
- как оценивать сложность интеграции новых фич и технологий.

Документ ориентирован одновременно на человека и на LLM/агента:
- человеческая часть: объяснение логики и причин решений;
- машинная часть: фиксированные секции, таблицы, шкалы сложности и критерии.

## 1.1 Точки входа после декомпозиции общей схемы

Старая “общая схема” сохранена для обратной совместимости, но основной вход теперь разбит на 4 более читаемых карты:

1. [35 System Context Map](35-system-context-map.md)  
Что внутри: L0-контуры системы (крупные блоки и интеграции без перегрузки деталями).

2. [36 Data Lifecycle Map](36-data-lifecycle-map.md)  
Что внутри: жизненный цикл данных вакансий/профилей/артефактов индексации.

3. [37 Runtime Request Flow](37-runtime-request-flow.md)  
Что внутри: runtime-поток одного пользовательского запроса до top-N выдачи.

4. [38 Taxonomy Governance Flow](38-taxonomy-governance-flow.md)  
Что внутри: наполнение taxonomy/synonyms, review/freeze и использование в runtime.

Рекомендованный порядок чтения: `35 -> 36 -> 37 -> 38`, затем этот master-документ для детализации и приоритизации roadmap.

---

## 2. Контур системы (runtime)

### 2.1 Основные коллекции MongoDB
Неизменяемые названия (согласно `AGENTS.md`):
- `stellenangebote` — актуальный список вакансий (meta/list слой).
- `jobdetails` — актуальные детальные описания вакансий.
- `analysen` — версии анализа/оценки вакансий под профиль кандидата.
- `profiles` — профили кандидатов.

Дополнительные коллекции истории:
- `stellenangebote_history`
- `jobdetails_history`

### 2.2 Основные пайплайны
- `app.job_list_collector` — сбор списка вакансий.
- `app.job_details_collector` — сбор/обновление деталей вакансий с ранним date-gate.
- `app.analyze_jobs` — анализ вакансий под профиль и prompt.
- `app.build_job_index` — retrieval индекс (чанки + эмбеддинги + Qdrant).
- `app.search_jobs` — поиск и ранжирование вакансий для запроса пользователя.

---

## 3. Линии обработки и алгоритмические ветвления

### 3.1 Сбор списка вакансий (`job_list_collector`)
Идея: писать только реальные изменения, повторный запуск должен быть идемпотентным.

Алгоритм (упрощенно):
1. Получить список из внешнего API.
2. Для каждой вакансии посчитать стабильный `meta_hash`.
3. Сравнить с текущей записью в `stellenangebote`.
4. Ветвление:
- `hash изменился` -> обновить документ.
- `hash совпал` -> пропустить обновление.
5. Зафиксировать `list_seen` событие в history.

Ключевые decision points:
- что включаем в hash (чтобы не шуметь из-за служебных полей);
- как обрабатываем неактивные/исчезнувшие вакансии.

### 3.2 Сбор деталей (`job_details_collector`)
Критичный anti-overload этап: ранний отсев до API деталей.

Алгоритм (упрощенно):
1. Взять активные вакансии из `stellenangebote`.
2. Батчем прочитать соответствующие `jobdetails` по `referenznummer`.
3. Для каждой вакансии сравнить даты:
- `SA = stellenangebote.modifikationsTimestamp`
- `JD = jobdetails.aenderungsdatum`
4. Ветвление:
- `SA <= JD` -> `skipped_guaranteed` (API не вызывается).
- `SA > JD` или отсутствуют данные -> кандидат на verify (API вызов).
5. После API:
- `404` -> специальная обработка (`remote_not_found`).
- `new <= old` -> skip без перезаписи.
- `new > old` -> сравнение хеша деталей и update только при изменении.

Ключевые decision points:
- date-gate обязателен;
- API вызывается только для verify-кандидатов;
- история фиксируется отдельными событиями.

### 3.3 Анализ вакансий (`analyze_jobs`)
Идея: версия анализа зависит от входа, а не от факта запуска.

Алгоритм (упрощенно):
1. Собрать вход анализа: `meta + details + profile + prompt + model`.
2. Посчитать `input_hash`.
3. Проверить наличие анализа по ключу:
- `(referenznummer, candidate_profile_key, input_hash)`.
4. Ветвление:
- `есть` -> unchanged, skip новой версии.
- `нет` -> создать новую версию в `analysen`.

Ключевые decision points:
- какие поля входят в input_hash;
- как фиксировать версию и профильно-зависимую уникальность.

### 3.4 Matching индекс (`build_job_index`)
Идея: индексировать только изменившиеся вакансии.

Алгоритм (упрощенно):
1. Джойн `stellenangebote + jobdetails` по `referenznummer`.
2. Нормализация текста вакансии.
3. Чанкинг:
- baseline fallback;
- semantic strategy (если включено).
4. Подсчет `embedding_input_hash`.
5. Ветвление:
- hash/versions не изменились -> skip reindex.
- изменились -> пересчет эмбеддингов и upsert в Qdrant.

Ключевые decision points:
- правила chunking;
- состав полей для hash;
- поведение при ошибках эмбеддинга/Qdrant.

### 3.5 Поиск (`search_jobs`)
Текущий режим: retrieval + baseline rerank/scoring.

Алгоритм (упрощенно):
1. Сформировать эмбеддинг query.
2. Получить top-K чанков из Qdrant.
3. Сгруппировать по `referenznummer`.
4. Посчитать компоненты score (`chunk`, `role`, `profile`, `metadata`, etc.).
5. Сформировать `pre_llm_score`, `rerank_score` (пока baseline формула).
6. Вернуть top-N с `top_chunks` для LLM handoff.

Ключевые decision points:
- как агрегировать chunk->vacancy;
- штрафы за role mismatch;
- баланс релевантности и фильтров/предпочтений.

---

## 4. Что уже сделано и что запланировано

### 4.1 Реализовано (подтверждено в `PLANS.md` и коде)
- Rule stack (`Pydantic + Pandera + JSONLogic + Audit`) — завершено.
- Двухэтапный `job_details_collector` с batch prefilter/date-gate — завершено.
- Архитектурная документация и Mermaid-схемы — завершено.
- Local matching Phase 1/2 foundation:
- конфиг matching,
- bootstrap Qdrant,
- инкрементальный индексатор,
- chunking (включая semantic + fallback),
- baseline retrieval/rerank CLI.
- Локальный LLM контур (llama.cpp docker + scripts) — частично готов.
- Pipeline нормализации профиля из текста:
- `tools/extract_profile_from_text.py`,
- `tools/update_taxonomy_from_unknown_terms.py`,
- `tools/run_profile_normalization_cycle.sh`.

### 4.2 В работе
- Полноразмерный smoke/nightly прогон matching с метриками на реальных объемах.
- Стабилизация profile extraction (локальная LLM иногда возвращает `{}`).
- Доработка model-based rerank (в коде пока baseline rerank).

### 4.3 Запланировано
- Model-based rerank (cross-encoder) с fallback.
- Разделение score на `hire_fit` и `preference_fit` с configurable weight от 0.
- Улучшение pipeline нормализации резюме -> profile JSON (retry, stricter prompt strategy, taxonomy growth).
- Дальнейшая формализация quality-метрик: top-20 relevance, role-mismatch, p50/p95 latency.

---

## 5. Приоритеты (операционный backlog)

| Priority | Item | Why now |
|---|---|---|
| P0 | Стабилизировать profile extraction (убрать пустые `{}`) | Блокирует автоматическую нормализацию профиля и рост taxonomy |
| P0 | Зафиксировать baseline метрики качества/latency | Нужна точка сравнения для любых улучшений ранжирования |
| P1 | Внедрить model-based rerank с fallback | Существенный прирост релевантности top-20 |
| P1 | Split scoring: `hire_fit` vs `preference_fit` | Соответствует целевой задаче “вероятность найма + пожелания кандидата” |
| P2 | LLM handoff + генерация прикладных выходов (объяснение/Anschreiben) | Важно, но зависит от стабильной базы retrieval+ranks |
| P2 | Автоматизация taxonomy роста и governance | Повышает качество в среднесроке, но вторично после P0/P1 |

---

## 6. Матрица оценки сложности интеграции новых фич/технологий

### 6.1 Шкала (0..3)
Оценить каждый критерий:
- `0` — почти нет влияния;
- `1` — локальное изменение;
- `2` — затрагивает несколько модулей/контрактов;
- `3` — архитектурный сдвиг или высокий риск регрессии.

Критерии:
- `C1: Runtime impact` (latency/CPU/RAM).
- `C2: Data/contract impact` (схемы, индексы, JSON контракты).
- `C3: Pipeline coupling` (сколько пайплайнов затронуто).
- `C4: Ops complexity` (deploy, observability, fallback).
- `C5: Testing burden` (новые smoke/unit/regression проверки).

### 6.2 Формула
`ComplexityScore = C1 + C2 + C3 + C4 + C5`

Классы сложности:
- `0..4` -> Low
- `5..8` -> Medium
- `9..12` -> High
- `13..15` -> Very High

### 6.3 Шаблон для LLM/человека
```json
{
  "feature_name": "string",
  "scores": {
    "runtime_impact": 0,
    "data_contract_impact": 0,
    "pipeline_coupling": 0,
    "ops_complexity": 0,
    "testing_burden": 0
  },
  "complexity_score": 0,
  "complexity_class": "Low|Medium|High|Very High",
  "key_risks": ["..."],
  "required_guards": ["feature flag", "fallback", "metrics", "rollback"]
}
```

---

## 7. Пример применения матрицы

### 7.1 Внедрение model-based rerank
Оценка:
- C1=2 (доп. latency, batch inference)
- C2=1 (контракт ответа почти не меняется)
- C3=2 (search + конфиги + метрики)
- C4=2 (fallback и таймауты обязательны)
- C5=2 (A/B сравнение, regression suite)

`Total = 9 => High`

Вывод: делать под флагом, с fallback на baseline, и обязательным offline benchmark до включения по умолчанию.

### 7.2 Добавление нового эмбеддинг-провайдера
Оценка:
- C1=1
- C2=2 (размер вектора, reindex)
- C3=2
- C4=2
- C5=2

`Total = 9 => High`

Вывод: сначала provider-agnostic адаптер + тестовый профиль, потом постепенный rollout.

---

## 8. Набор инвариантов (не нарушать)
- Коллекции: `stellenangebote`, `jobdetails`, `analysen`, `profiles` — без переименования.
- Предфильтр по датам (`SA.modifikationsTimestamp` vs `JD.aenderungsdatum`) обязателен до API деталей.
- Идемпотентность: повторный запуск не должен генерировать лишние записи.
- Секреты только через ENV, без вывода в логи и документы.
- Для крупных изменений: сначала `PLANS.md`, затем код.

---

## 9. Источники актуальности
- `PLANS.md`
- `AGENTS.md`
- [`docs/process/assistant-operating-rules.md`](../process/assistant-operating-rules.md)
- [`docs/architecture/00-runtime-contract.md`](00-runtime-contract.md)
- [`docs/architecture/01-system-overview.md`](01-system-overview.md)
- [`docs/architecture/03-job-details-decision-and-state.md`](03-job-details-decision-and-state.md)
- [`docs/architecture/23-retrieval-rerank-runtime-flow.md`](23-retrieval-rerank-runtime-flow.md)
