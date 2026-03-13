# Handoff: текущее состояние проекта (на 2026-02-23)

## 1) Снимок состояния

- Дата UTC: `2026-02-23T02:58:08Z`
- Текущая ветка: `chore/de-comments-readme-de`
- `git status` (не закоммичено):
  - `M .env.example`
  - `M conf/app.json`
  - `M docker/docker-compose.yml`
  - `?? temp.txt`

## 2) Что уже закрыто (последние завершенные блоки)

- Добавлены и закоммичены runtime-пресеты matching + архитектурная дока.
- Docker-контур разделен:
  - базовый: `mongo`, `qdrant`;
  - аналитика: `mongo-express`, `nosqlclient`, `metabase`.
- Скрипты backup/restore усилены:
  - `tools/mongo_backup.sh` и `tools/mongo_restore.sh` поддерживают `--env-file`;
  - runbook обновлен.
- В `.gitignore` добавлены `backups/` и `*.archive.gz`.
- Mongo по умолчанию выровнен на `8.0`.

## 3) Что остается открытым (по планам)

- `PLAN-20260219-03` (локальный matching-конвейер): **не завершен**.
  - Ключевой незакрытый пункт: полноразмерный smoke/метрики и закрытие оставшихся фаз.
- `PLAN-20260222-01` (quantized models rollout): **запланирован, не начат**.
- [`docs/worklog/2026-02-22-jobcenter-api-audit-high-priority.md`](2026-02-22-jobcenter-api-audit-high-priority.md): статус **открыто**, требуется архитектурное решение.

Примечание: в `PLANS.md` также есть старый незакрытый `PLAN-20260208-07`, фактическое состояние этапов анализа частично ушло вперед относительно формального чекбокса в плане.

## 4) Текущее runtime-состояние (факт)

- `docker compose --env-file .env -f docker/docker-compose.yml config` резолвит:
  - `mongo:8.0`
  - `qdrant:qdrant/qdrant:v1.17.0` (локальное текущее изменение, еще не закоммичено)
- `app.bootstrap_qdrant` работает.
- `app.build_job_index` работает при корректной поддерживаемой модели fastembed.
- `app.search_jobs` работает и возвращает:
  - `results[]` (топ вакансий)
  - `top_chunks` внутри каждой вакансии.

## 5) Важные нюансы/риски

1. `fastembed` и модель:
- `intfloat/multilingual-e5-small` в текущем окружении **не поддерживается**.
- Использовать только модели из `TextEmbedding.list_supported_models()`.

2. Qdrant reset без state reset:
- Если очистить только Qdrant, но оставить `matching_index_state` в Mongo, индексатор может сделать `skip` и не восстановить точки.
- Для корректного полного реиндекса:
  - либо удалить `matching_index_state`,
  - либо bump `matching.chunking.version`/`preprocess.version`,
  - либо использовать `AUTO_FULL_REINDEX_ON_COLLECTION_CREATE=true` при заново созданной коллекции.

3. `search_jobs` не делает downstream persistence:
- Сейчас это retrieval+rеrank CLI output.
- Автоматической передачи в следующий LLM-handoff этап пока нет.

4. Prompt из `analysis_prompts`:
- Используется в `app.analyze_jobs`,
- не используется в `app.search_jobs`.

## 6) С чем продолжать после включения ПК (рекомендуемый порядок)

1. Проверить чистоту/ожидаемость git-изменений:
```bash
git status --short
```

2. Поднять инфраструктуру:
```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d
```

3. Проверить bootstrap Qdrant:
```bash
.venv/bin/python -m app.bootstrap_qdrant
```

4. Если делали reset Qdrant, обеспечить реиндекс:
- вариант A: bump версии в `conf/app.json`;
- вариант B: очистить `matching_index_state`;
- затем:
```bash
AUTO_FULL_REINDEX_ON_COLLECTION_CREATE=true .venv/bin/python -m app.build_job_index
```

5. Проверить retrieval:
```bash
.venv/bin/python -m app.search_jobs --query "Anwender bei IT-Anfragen und Problemen im täglichen Betrieb" --profile-key default --limit 20
```

6. Если нужен анализ с реальным prompt из Mongo:
```bash
ANALYZER_PROMPT_KEY=default ANALYZER_PROMPT_VERSION=1 .venv/bin/python -m app.analyze_jobs
```

## 7) Что сейчас желательно зафиксировать отдельным коммитом

- Выровнять дефолт Qdrant на существующий тег `v1.17.0`:
  - `docker/docker-compose.yml`
  - `.env.example`
- Если в `conf/app.json` подбиралась другая embedding-модель/длина вектора, зафиксировать это отдельным осмысленным коммитом (или вернуть на baseline, если это был только эксперимент).

## 8) Кратко: где остановились

- Retrieval/indexing контур в рабочем состоянии.
- Идет подбор оптимального быстрого embedding-конфига без сильной деградации качества.
- Следующий логичный этап: либо закрывать незавершенный `PLAN-20260219-03` (полный smoke + метрики), либо переходить к внедрению model-based reranker как отдельному этапу.

## 9) Рекомендация по проблеме role mismatch

Наблюдение по фактической выдаче: в топ иногда попадают вакансии с явным mismatch роли
(например, нерелевантный технический домен при IT-запросе).

Рекомендованный минимальный фикс без усложнения архитектуры:
- ужесточить штраф за несовпадение роли в `conf/app.json`:
  - `matching.rerank.weights.role_mismatch_threshold`: поднять с `0.08` до `0.12-0.15`;
  - `matching.rerank.weights.role_mismatch_penalty`: снизить с `0.65` до `0.35-0.50`.

Почему это сейчас приоритетно:
- быстро уменьшает ложноположительные попадания в top-20;
- не требует миграций схемы данных;
- не ломает текущий pipeline-контракт.

Как валидировать после изменения:
1. `python3 -m app.search_jobs --query \"...\" --profile-key default --limit 20`
2. Сравнить top-20 до/после на 10-20 фиксированных запросах.
3. Зафиксировать долю явных нерелевантных вакансий (должна снизиться).
