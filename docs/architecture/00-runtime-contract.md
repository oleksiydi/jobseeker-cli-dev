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
- `job_details_collector` читает `{"is_active": True}`.
- `analyze_jobs` читает `{"is_active": True}`.
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
- unchanged detection сейчас основан на `(referenznummer, candidate_profile_key, input_hash)`.

2. Профиль кандидата:
- сохраняется в поле `candidate_profile_key`;
- участвует и в версионировании/чтении latest, и в уникальности `input_hash` на уровне индекса.

## Что важно не перепутать

1. `is_active` сейчас не означает “глобально активна на рынке”.
- Это “активна в рамках текущего list scope”.

2. Частая смена list scope без явной стратегии multi-scope приводит к смешению семантики “active”.

3. Для clean-up legacy данных есть one-off миграция:
- `tools/backfill_is_active_flag.py`
- цель: явно проставить `is_active=True` документам без поля.

4. Для migration unique-индекса `analysen` есть one-off скрипт:
- `tools/migrate_analysen_profile_scoped_index.py`
- цель: перевести unique-ограничение с `(referenznummer,input_hash)` на `(referenznummer,candidate_profile_key,input_hash)`.

## Следующий технический фокус

Поддерживать строгий контракт активности в новых изменениях:
- новые пайплайны/агрегации должны использовать `{"is_active": True}`;
- one-off миграцию legacy-данных (`tools/backfill_is_active_flag.py`) держать как операционный инструмент для отдельных восстановлений.
