# Semantic chunking + embedding + rerank: как это работает у нас (с формулами)

## Короткий ответ на главный вопрос
Да, в текущем пайплайне **semantic chunking** делается совместно:
1. `spaCy` (или fallback) разбивает текст вакансии на предложения;
2. embedding-модель считает векторы предложений;
3. по косинусной близости соседних предложений решается, объединять их в один chunk или начинать новый.

То есть:
- `spaCy` отвечает за **границы предложений**;
- embedding отвечает за **смысловую близость**;
- rule-engine присваивает **метку секции** (`tasks/requirements/benefits/...`).

---

## 1) Полный поток (связка с предыдущим объяснением)

1. Из Mongo берется текст вакансии (`description`, иначе `canonical_text`).
2. `spaCy` делит текст на предложения.
3. Для каждого предложения считается embedding-вектор.
4. Предложения склеиваются в chunk по порогу семантической близости.
5. На каждый chunk накладывается `section_label` через rules.
6. Chunk-векторы индексируются в Qdrant.
7. При поиске query/profile -> retrieval top-k chunks -> scoring/rerank.

---

## 2) Математика: как строится semantic chunking

### 2.1 Обозначения
- Пусть `s_i` — i-е предложение вакансии.
- `v_i = Embedding(s_i)` — embedding-вектор предложения `s_i`.
- `W_i` — окно последних предложений текущего chunk (например, 2).
- `tau` — порог семантической близости (`semantic_similarity_threshold`, сейчас 0.72).

### 2.2 Косинусная близость (база)
\[
\cos(v_i, v_j) = \frac{v_i \cdot v_j}{\|v_i\| \cdot \|v_j\|}
\]

Где:
- `v_i · v_j` — скалярное произведение векторов,
- `||v_i||` — евклидова норма (длина вектора).

### 2.3 Средняя близость нового предложения к текущему chunk
\[
\text{sim}_i = \frac{1}{|W_i|}\sum_{v_k \in W_i}\cos(v_i, v_k)
\]

Где:
- `W_i` — последние `window` векторов текущего chunk.

### 2.4 Правило объединения
Новое предложение `s_i` добавляется в текущий chunk, если:
\[
\text{sim}_i \ge \tau
\]
или если текущий chunk еще не достиг минимума предложений (`semantic_min_sentences`).

Иначе начинается новый chunk.

Дополнительно:
- chunk ограничивается `semantic_max_sentences`;
- потом применяются token-aware и hard limits.

---

## 3) Кто что делает в системе

### 3.1 Кто выделяет “смысловые блоки”
В текущей реализации это не делает сама embedding-модель напрямую.

Фактически блоки формируются в два шага:
1. **Chunk boundaries** (границы) — по семантической близости предложений (embedding + threshold).
2. **Section labels** (имя блока) — через `rule_engine.infer_section_label(chunk_text)` на базе правил в `conf/rules/label_rules.v1.json`.

Пример label-правил:
- если встречается `aufgaben` -> `tasks`;
- если `anforderungen`, `qualifikation` -> `requirements`;
- если `wir bieten`, `benefits` -> `benefits`;
- иначе `unknown`.

Итого:
- кто “называет” блоки: **rule-engine по словарным правилам**;
- кто “склеивает по смыслу”: **semantic chunking через embeddings**.

### 3.1.1 Откуда берется `rule-engine` и как он присваивает `section_label`

`rule-engine` — это модуль `MatchingRuleEngine` в коде, который:
1. загружает rule-файлы из `conf/rules/*.json`;
2. компилирует правила;
3. применяет их к тексту chunk.

Источник rule-файлов настраивается в `conf/app.json`:
- `matching.rules.path`
- `matching.rules.files.normalize/fallback/label/reindex`
- `matching.rules.ruleset_version`

То есть да: **вы этим полностью управляете** через конфиг и JSON-правила.

#### Как именно считается `section_label` (приближенно)

Пусть:
- `x = normalize(chunk_text).lower()` — нормализованный текст чанка;
- `R = [r_1, r_2, ..., r_m]` — упорядоченный список label-правил;
- у каждого правила есть условие `when_i`, метка `label_i`, confidence `c_i`.

Тогда алгоритм:
1. Идти по правилам сверху вниз.
2. Найти первое правило, где `when_i(x) = true`.
3. Вернуть `(label_i, c_i, rule_id_i)`.
4. Если ни одно не сработало, вернуть `default_label/default_confidence`.

Формально:
\[
\text{section\_label}(x) =
\begin{cases}
label_k, & k = \min\{i \mid when_i(x)=1\}\\
label_{default}, & \text{если подходящих правил нет}
\end{cases}
\]

Комментарии к формуле:
- `k = min(...)` означает “берем первое сработавшее правило” (first-match).
- поэтому **порядок правил в JSON важен**.
- `default_label` обычно `unknown`.

#### Из какого набора это берется сейчас

Текущий набор:
- `conf/rules/label_rules.v1.json`

Примеры правил из набора:
- встречается `aufgaben`/`tätigkeiten` -> `tasks`;
- `anforderungen`/`qualifikation` -> `requirements`;
- `wir bieten`/`benefits` -> `benefits`;
- иначе -> `unknown`.

#### Чем именно вы управляете

Через файлы правил и конфиг вы задаете:
1. список правил;
2. порядок правил;
3. значения `label`;
4. `confidence`;
5. default-метку;
6. версию ruleset.

По сути это управляемая “бизнес-логика labeling” без перекомпиляции модели.

### 3.2 Кто выделяет фасеты
`Фасеты` = структурированные “оси” профиля/вакансии:
- роль,
- навыки,
- индустрии,
- локации,
- условия занятости.

В текущем коде профиля фасеты по сути задаются вручную через whitelist полей:
- `key`, `target_role`, `skills`, `tools`, `stack`, `experience`, `seniority`, `industries`, `locations`, `languages`, `summary`.

То есть сейчас фасеты:
- не извлекаются моделью автоматически,
- а формируются на уровне инженерного контракта полей.

---

## 4) Взаимодействие spaCy и embedding-модели

Это последовательная связка:
1. `spaCy` -> список предложений: `[s_1, s_2, ..., s_n]`.
2. Embedding -> список векторов: `[v_1, v_2, ..., v_n]`.
3. Алгоритм chunking проходит по `v_i` и решает границы chunks.

Важно:
- если `spaCy` недоступен, есть fallback (`syntok`/`regex`);
- если embedding недоступен, semantic chunking деградирует в безопасный baseline.

---

## 5) Где тут query, а где кандидат

На этапе retrieval:
- строится embedding query (с profile context),
- ищутся ближайшие chunk-векторы вакансий.

На этапе rerank (текущий baseline):
- используются `chunk_score` + лексические признаки (`role/profile/metadata`).

На этапе model-based rerank (план):
- будет inference cross-encoder на паре:
  - `(query_or_constraints, candidate_text)`.

---

## 6) Практический смысл для твоей идеи

Твоя логика “сначала fit кандидата к вакансии, потом ограничения запроса” правильная.

Формально это можно записать так:
\[
\text{final\_score} = \alpha \cdot \text{fit\_score} + \beta \cdot \text{constraint\_score}
\]

Где:
- `fit_score` — насколько профиль кандидата подходит вакансии;
- `constraint_score` — насколько вакансия удовлетворяет текущим условиям (remote, ночные смены и т.д.).

Для строгих условий лучше сначала сделать hard-filter:
- если условие “обязательно”, вакансия отсекается до rerank.

### 6.1 Зафиксированная целевая схема реализации (при переходе на model-based rerank)

Фиксируем как контракт:
1. `fit_score` считается отдельно:
\[
\text{fit\_score} = f(\text{profile\_context}, \text{vacancy\_text})
\]

2. `intent_score` считается отдельно:
\[
\text{intent\_score} = g(\text{query\_context}, \text{vacancy\_text})
\]

3. Итог:
\[
\text{final\_score} = \alpha \cdot \text{fit\_score} + \beta \cdot \text{intent\_score}
\]

Где:
- `profile_context`: структурированный профиль кандидата (фасеты роли/навыков/опыта и т.д.);
- `query_context`: текущий запрос и уточняющие условия пользователя;
- `vacancy_text`: фиксированный шаблон кандидата для rerank (`title + role + top_chunks (+meta)`).

4. Обязательные ограничения (constraints) применяются до rerank:
\[
\text{candidate\_set} = \{v \in \text{retrieval\_top\_k} \mid \text{hard\_constraints}(v)=1\}
\]

Примеры `hard_constraints`:
- `remote_only=true`;
- `night_shift_required=true`;
- обязательная локация/тип занятости.

Практический порядок:
1. retrieval top-k chunks;
2. агрегация кандидатов;
3. hard-filter по constraints;
4. model-based rerank по `fit_score` и `intent_score`;
5. финальная сортировка по `final_score`.

---

## 7) Что проверить, чтобы не “плавало”

1. Зафиксировать шаблон `candidate_text` для rerank.
2. Зафиксировать веса `alpha`, `beta`.
3. Использовать один и тот же benchmark-набор запросов.
4. Сравнивать:
- долю role-mismatch в top-20;
- latency;
- стабильность результатов между прогонами.

---

## 8) Где смотреть в коде

- Semantic chunking: `app/build_job_index.py` (`_build_semantic_chunk_texts`, `_split_text_to_sentences`).
- Косинусная близость: `app/build_job_index.py` (`_mean_similarity_vectorized`, `_cosine_similarity`).
- Labeling sections: `app/matching/rule_engine.py` + `conf/rules/label_rules.v1.json`.
- Profile facets (whitelist полей): `app/search_jobs.py` (`PROFILE_SEARCH_FIELDS`).
- Текущий scoring/rerank baseline: `app/matching/search.py` (`compute_scores`).
