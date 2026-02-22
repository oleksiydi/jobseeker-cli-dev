# Runtime Contract (Current)

Status: `current`  
Last verified: `2026-02-22`

## Назначение

Этот документ фиксирует текущее поведение runtime в коде.
Если любой другой документ конфликтует с этим контрактом, для операционных решений приоритет у этого файла.

## Источники истины в коде

- `app/job_list_collector.py`
- `app/job_details_collector.py`
- `app/analyze_jobs.py`
- `app/build_job_index.py`
- `app/vector/qdrant_client.py`

## Контракт по активности вакансии

1. `job_list_collector` задает `is_active` как **scope-based** состояние.
- Scope определяется `jobcenter.list_api.api_url + params`.
- Текущий scope хешируется (`query_hash`) и хранится в `pipeline_state`.

2. Массовая деактивация `list_missing` выполняется только если:
- run завершился полным проходом страниц (`completed_full_scan=true`);
- scope не изменился относительно предыдущего run (`query_hash` совпал).

3. Поведение downstream:
- `job_details_collector` читает `{"is_active": {"$ne": False}}`.
- `analyze_jobs` читает `{"is_active": {"$ne": False}}`.
- `build_job_index` читает `{"is_active": True}`.

## Контракт по retry/устойчивости

1. `job_list_collector`:
- bounded retry для list API страницы;
- retry только для временных ошибок (`timeout/connection/429/5xx`);
- backoff: exponential + jitter.

2. `job_details_collector`:
- bounded retry для details API кандидата (2 попытки);
- backoff: предсказуемый exponential + jitter;
- ошибка записи Mongo по одной вакансии не останавливает batch (`continue`).

3. `qdrant-client`:
- обязателен; при отсутствии индексатор завершится с понятной ошибкой.

## Контракт по анализу (`analysen`)

1. Ключ пропуска повторной обработки:
- unchanged detection сейчас основан на `(referenznummer, input_hash)`.

2. Профиль кандидата:
- сохраняется в поле `candidate_profile_key`;
- участвует в версионировании/чтении latest, но не входит в уникальность `input_hash` на уровне индекса.

## Что важно не перепутать

1. `is_active` сейчас не означает “глобально активна на рынке”.
- Это “активна в рамках текущего list scope”.

2. Частая смена list scope без явной стратегии multi-scope приводит к смешению семантики “active”.

3. Различие фильтра активности между `build_job_index` и остальными пайплайнами является известным техническим долгом.

## Следующий технический фикс (рекомендуемый)

Выравнивание фильтра активности между пайплайнами:
- привести все к единому контракту (либо везде `is_active == True`, либо везде `is_active != False`);
- отдельно задокументировать и протестировать выбранную семантику.

